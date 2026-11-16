import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from calibration_service import Service
from conflicts import ConflictReport
from hardware import Hardware
from storage import Store
from trigger_engine import CalibrationError, Values
from simulated import SimulatedHardware, SimulatedScanner, INITIAL


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = SimulatedHardware()
        self.scanner = SimulatedScanner()
        self.store = Store(self.temp.name)
        self.service = Service(self.adapter,self.store,self.scanner,test_seconds=10,retry_delays=(0,0))
        self.assertTrue((await self.service.initialize())['ok'])

    async def asyncTearDown(self):
        self.adapter.fail_writes = False
        self.adapter.ignore_writes = False
        self.adapter.read_error = None
        self.scanner.report = ConflictReport([],[],False)
        try:
            await self.service.close()
        finally:
            if self.service.timer:
                self.service.timer.cancel()
            self.store.close()
            self.temp.cleanup()

    async def begin(self,left=1834,right=1730):
        return await self.service.start_test(self.adapter.values.as_dict(),left,right)

    async def confirmed_save(self,left=1834,right=1730):
        result = await self.begin(left,right)
        return await self.service.save_test(result['test']['token'],True)

    async def test_first_launch_read_and_backup_precede_any_write(self):
        record = self.store.load('initial.json')
        self.assertEqual(record['values'],INITIAL.as_dict())
        self.assertFalse(record['factory_verified'])
        self.assertEqual(self.adapter.writes,[])

    async def test_backup_persists_through_reinitialization(self):
        before = (Path(self.temp.name)/'initial.json').read_bytes()
        self.adapter.values = INITIAL.proposal(1834,1730)
        await self.service.initialize()
        self.assertEqual((Path(self.temp.name)/'initial.json').read_bytes(),before)
        self.assertEqual(self.service.initial['values'],INITIAL.as_dict())

    async def test_missing_backup_is_not_recreated(self):
        (Path(self.temp.name)/'initial.json').unlink()
        (Path(self.temp.name)/'original-recovery/initial.json').unlink()
        result = await self.service.initialize()
        self.assertFalse(result['ok'])
        self.assertIn('missing',result['error'])
        self.assertFalse((Path(self.temp.name)/'initial.json').exists())
        self.assertEqual(self.adapter.writes,[])

    async def test_corrupt_backup_disables_writes_and_survives(self):
        path = Path(self.temp.name)/'initial.json'
        path.write_text('{broken')
        (Path(self.temp.name)/'original-recovery/initial.json').write_text('{broken reserve')
        result = await self.service.initialize()
        self.assertFalse(result['write_enabled'])
        with self.assertRaises(CalibrationError):
            await self.begin()
        self.assertEqual(path.read_text(),'{broken')

    async def test_temporary_test_preserves_full_previous_and_profile(self):
        old_profiles = deepcopy(self.service.profiles)
        result = await self.begin()
        self.assertEqual(self.adapter.values,INITIAL.proposal(1834,1730))
        self.assertEqual(self.store.load('pending.json')['previous'],INITIAL.as_dict())
        self.assertEqual(self.service.profiles,old_profiles)
        self.assertIsNone(result['profiles']['lkg'])
        await self.service.cancel_test()
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertIsNone(self.store.load('pending.json'))

    async def test_timeout_rolls_back_without_frontend(self):
        self.service.test_seconds = .025
        await self.begin()
        await asyncio.sleep(.12)
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertIsNone(self.service.pending)
        self.assertIn('expired',self.service.message)

    async def test_stale_timeout_token_cannot_be_saved(self):
        self.service.test_seconds = .02
        result = await self.begin()
        await asyncio.sleep(.1)
        with self.assertRaises(CalibrationError):
            await self.service.save_test(result['test']['token'],True)
        self.assertIsNone(self.service.profiles['lkg'])

    async def test_save_requires_exact_confirmation_and_active_matching_test(self):
        with self.assertRaises(CalibrationError):
            await self.service.save_test('absent',True)
        result = await self.begin()
        token = result['test']['token']
        for confirmation in (False,1,'yes',None):
            with self.assertRaises(CalibrationError):
                await self.service.save_test(token,confirmation)
        with self.assertRaises(CalibrationError):
            await self.service.save_test('wrong-token',True)
        self.assertIsNone(self.service.profiles['lkg'])

    async def test_save_verifies_and_updates_saved_and_lkg_atomically(self):
        result = await self.confirmed_save()
        self.assertIsNone(result['test'])
        self.assertEqual(result['profiles']['saved'],result['profiles']['lkg'])
        self.assertTrue(result['profiles']['lkg']['confirmed'])
        self.assertEqual(result['profiles']['lkg']['values'],self.adapter.values.as_dict())
        self.assertEqual(self.store.load('profiles.json'),result['profiles'])

    async def test_external_change_during_test_rejects_save_and_restores_previous(self):
        result = await self.begin()
        self.adapter.values = INITIAL.proposal(1833,1730)
        with self.assertRaises(CalibrationError):
            await self.service.save_test(result['test']['token'],True)
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertIsNone(self.service.profiles['lkg'])

    async def test_last_good_preserved_on_new_unconfirmed_test(self):
        await self.confirmed_save()
        old = deepcopy(self.service.profiles['lkg'])
        await self.begin(1854,1750)
        self.assertEqual(self.service.profiles['lkg'],old)
        await self.service.cancel_test()
        self.assertEqual(self.service.profiles['lkg'],old)

    async def test_profile_persistence_error_restores_previous_and_preserves_lkg(self):
        await self.confirmed_save()
        old = deepcopy(self.service.profiles['lkg'])
        result = await self.begin(1854,1730)
        real_save = self.store.save
        def failing_save(name,*args,**kwargs):
            if name == 'profiles.json':
                raise PermissionError('simulated disk error')
            return real_save(name,*args,**kwargs)
        with patch.object(self.store,'save',side_effect=failing_save):
            with self.assertRaises(PermissionError):
                await self.service.save_test(result['test']['token'],True)
        self.assertEqual(self.service.profiles['lkg'],old)
        self.assertEqual(self.adapter.values,Values.from_dict(old['values']))

    async def test_partial_two_trigger_write_rolls_back_both(self):
        self.adapter.partial_once = True
        with self.assertRaises(CalibrationError):
            await self.begin(1834,1750)
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertIsNone(self.service.pending)

    async def test_write_verification_failure_attempts_safe_restoration(self):
        self.adapter.ignore_writes = True
        with self.assertRaises(CalibrationError):
            await self.begin()
        self.assertEqual(self.adapter.writes[-1],INITIAL)
        self.assertIsNone(self.service.profiles['lkg'])

    async def test_failed_rollback_retains_journal_and_disables_automatic_application(self):
        await self.begin()
        self.adapter.fail_writes = True
        with self.assertRaises(CalibrationError):
            await self.service.cancel_test()
        self.assertIsNotNone(self.store.load('pending.json'))
        self.assertFalse(self.service.profiles['boot'])
        self.assertFalse(self.service.profiles['resume'])
        self.assertIn('Rollback failed',self.service.error)

    async def test_interrupted_test_unload_rolls_back(self):
        await self.begin()
        await self.service.close()
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertIsNone(self.store.load('pending.json'))

    async def test_interrupted_test_is_recovered_from_journal_on_next_start(self):
        await self.begin()
        self.service._cancel_timer()  # Simulated abrupt process loss; no unload.
        self.store.close()
        restarted = Service(self.adapter,Store(self.temp.name),self.scanner,test_seconds=10)
        result = await restarted.initialize()
        self.assertTrue(result['ok'])
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertIsNone(restarted.pending)
        await restarted.close()
        self.service.pending = None

    async def test_restore_default_disables_auto_before_writing_and_cancels_test(self):
        await self.confirmed_save()
        await self.service.set_automatic(True,True)
        await self.begin(1854,1730)
        real_write = self.adapter.write
        def checked_write(values,identity):
            state = self.store.load('profiles.json')
            self.assertFalse(state['boot'])
            self.assertFalse(state['resume'])
            return real_write(values,identity)
        with patch.object(self.adapter,'write',side_effect=checked_write):
            result = await self.service.restore(True)
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertIsNone(result['test'])
        self.assertIsNotNone(result['profiles']['lkg'])
        self.assertIn('not factory',result['message'])

    async def test_restore_confirmation_required(self):
        with self.assertRaises(CalibrationError):
            await self.service.restore(False)
        self.assertEqual(self.adapter.writes,[])

    async def test_restore_ignores_slider_range_and_unusual_current_values(self):
        self.adapter.values = Values(60000,518,61000,506,12,13)
        await self.service.restore(True)
        self.assertEqual(self.adapter.values,INITIAL)

    async def test_restore_available_after_transient_read_error(self):
        self.adapter.read_error = CalibrationError('simulated temporary read error')
        self.assertFalse((await self.service.read())['ok'])
        self.adapter.read_error = None
        await self.service.restore(True)
        self.assertEqual(self.adapter.values,INITIAL)

    async def test_restore_verification_failure_keeps_auto_disabled(self):
        await self.confirmed_save()
        await self.service.set_automatic(True,True)
        self.adapter.ignore_writes = True
        with self.assertRaises(CalibrationError):
            await self.service.restore(True)
        self.assertFalse(self.service.profiles['boot'])
        self.assertFalse(self.service.profiles['resume'])
        self.assertIn('could not be verified',self.service.error)

    async def test_last_good_recovery(self):
        await self.confirmed_save()
        saved = self.adapter.values
        self.adapter.values = INITIAL
        await self.service.restore(True,last_good=True)
        self.assertEqual(self.adapter.values,saved)
        self.assertFalse(self.service.profiles['boot'])

    async def test_automatic_settings_reject_untested_profiles_and_non_booleans(self):
        for args in ((True,False),(1,False),(False,'yes')):
            with self.assertRaises(CalibrationError):
                await self.service.set_automatic(*args)

    async def test_boot_and_resume_only_apply_saved_confirmed_profile(self):
        await self.confirmed_save()
        saved = self.adapter.values
        await self.service.set_automatic(True,True)
        for event in ('boot','resume'):
            self.adapter.values = INITIAL
            await self.service.auto_apply(event)
            self.assertEqual(self.adapter.values,saved)
        with self.assertRaises(CalibrationError):
            await self.service.auto_apply('arbitrary-shell-command')

    async def test_resume_resolves_pending_test_before_profile(self):
        await self.confirmed_save()
        saved = self.adapter.values
        await self.service.set_automatic(True,True)
        await self.begin(1854,1730)
        await self.service.auto_apply('resume')
        self.assertEqual(self.adapter.values,saved)
        self.assertIsNone(self.service.pending)

    async def test_initialization_delay_retries_are_bounded(self):
        await self.confirmed_save()
        await self.service.set_automatic(True,False)
        self.adapter.fail_reads_count = 2
        await self.service.auto_apply('boot')
        self.assertEqual(self.adapter.fail_reads_count,0)

    async def test_conflict_blocks_start_save_and_automatic_enable(self):
        self.scanner.report = ConflictReport(['trigger-cal-boot.service'],[],True)
        with self.assertRaises(CalibrationError):
            await self.begin()
        self.assertEqual(self.adapter.writes,[])
        self.scanner.report = ConflictReport([],[],False)
        result = await self.begin()
        self.scanner.report = ConflictReport(['trigger-cal-resume.service'],[],True)
        with self.assertRaises(CalibrationError):
            await self.service.save_test(result['test']['token'],True)
        self.assertIsNone(self.service.profiles['lkg'])

    async def test_incomplete_conflict_inspection_fails_closed(self):
        self.scanner.report = ConflictReport([],['systemctl missing'],False,False)
        self.assertTrue((await self.service.read())['ok'])
        with self.assertRaises(CalibrationError):
            await self.begin()

    async def test_synchronization_allows_only_one_pending_test(self):
        results = await asyncio.gather(self.begin(),self.begin(),return_exceptions=True)
        self.assertEqual(sum(isinstance(item,dict) for item in results),1)
        self.assertEqual(sum(isinstance(item,CalibrationError) for item in results),1)
        self.assertEqual(self.adapter.max_active,1)

    async def test_stale_preview_and_strict_inputs_never_write(self):
        for left in (True,'1834',1834.0,65536,1835):
            with self.assertRaises(CalibrationError):
                await self.service.start_test(INITIAL.as_dict(),left,1730)
        self.adapter.values = INITIAL.proposal(1815,1730)
        with self.assertRaises(CalibrationError):
            await self.service.start_test(INITIAL.as_dict(),1834,1730)
        self.assertEqual(self.adapter.writes,[])

    async def test_real_adapter_has_no_write_unlock(self):
        self.service.adapter = Hardware()
        for operation in (lambda:self.begin(),lambda:self.service.set_automatic(True,True),
                          lambda:self.service.restore(True)):
            with self.assertRaises(CalibrationError):
                await operation()
        with self.assertRaises(CalibrationError):
            Hardware().write(INITIAL,'a'*64)
        self.assertEqual(self.adapter.writes,[])

    async def test_test_disabled_without_independent_watchdog(self):
        self.adapter.independent_rollback = False
        self.assertFalse(self.service.status()['test_enabled'])
        with self.assertRaises(CalibrationError):
            await self.begin()
        self.assertEqual(self.adapter.writes,[])

    async def test_identity_mismatch_never_writes(self):
        self.adapter.identity = 'b'*64
        with self.assertRaises(CalibrationError):
            await self.begin()
        self.assertEqual(self.adapter.writes,[])

    async def test_cancelled_rpc_waits_for_inflight_write_then_rolls_back(self):
        entered, release = threading.Event(), threading.Event()
        real_write = self.adapter.write
        def delayed_write(values,identity):
            if values.lmax == 1834:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('Test synchronization timed out')
            return real_write(values,identity)
        with patch.object(self.adapter,'write',side_effect=delayed_write):
            operation = asyncio.create_task(self.begin())
            self.assertTrue(await asyncio.to_thread(entered.wait,3))
            operation.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await operation
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertIsNone(self.service.pending)

    async def test_production_countdown_duration_is_120_seconds(self):
        from calibration_service import TEST_SECONDS
        self.assertEqual(TEST_SECONDS,120)
        self.service.test_seconds = TEST_SECONDS
        result = await self.begin()
        self.assertGreater(result['test']['remaining'],119)
        self.assertLessEqual(result['test']['remaining'],120)


class InitializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_failure_creates_no_backup_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as root:
            adapter = SimulatedHardware()
            adapter.read_error = CalibrationError('Simulated incomplete reading')
            service = Service(adapter,Store(root),SimulatedScanner())
            result = await service.initialize()
            self.assertFalse(result['ok'])
            self.assertFalse((Path(root)/'initial.json').exists())
            adapter.read_error = None
            self.assertTrue((await service.read())['ok'])
            await service.close()

    async def test_legacy_initial_snapshot_is_preserved_and_suspect_restore_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            legacy = {'factory_candidate': {'lmax':1780,'lmin':518,'rmax':1790,'rmin':506},
                      'last_applied': {'lmax':1800,'lmin':518,'rmax':1800,'rmin':506}}
            path = Path(root)/'calibration.json'
            path.write_text(json.dumps(legacy))
            original_bytes = path.read_bytes()
            service = Service(SimulatedHardware(),Store(root),SimulatedScanner())
            result = await service.initialize()
            self.assertTrue(result['ok'])
            self.assertEqual(result['initial']['values']['lmax'],1780)
            self.assertEqual(result['initial']['values']['rmax'],1790)
            self.assertTrue(result['initial']['suspected_modified'])
            self.assertIsNone(result['profiles']['lkg'])
            with self.assertRaises(CalibrationError):
                await service.restore(True)
            self.assertEqual(path.read_bytes(),original_bytes)
            self.assertEqual(service.store.load('legacy-v010.json'),legacy)
            await service.close()

    async def test_known_tool_at_first_read_marks_initial_as_suspected(self):
        with tempfile.TemporaryDirectory() as root:
            scanner = SimulatedScanner()
            scanner.report = ConflictReport([],['External tool present'],True)
            service = Service(SimulatedHardware(),Store(root),scanner)
            result = await service.initialize()
            self.assertTrue(result['initial']['suspected_modified'])
            with self.assertRaises(CalibrationError):
                await service.restore(True)
            await service.close()
