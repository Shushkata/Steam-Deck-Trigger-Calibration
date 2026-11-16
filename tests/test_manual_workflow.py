import asyncio
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from manual_service import ManualService
from storage import Store
from trigger_engine import CalibrationError,Values
from simulated import SimulatedHardware,SimulatedScanner,INITIAL
from conflicts import ConflictReport


class ManualWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.adapter=SimulatedHardware()
        self.scanner=SimulatedScanner()
        self.store=Store(self.temp.name)
        self.service=ManualService(self.adapter,self.store,self.scanner)
        state=await self.service.initialize()
        self.assertFalse(state['ok'])
        self.assertTrue(state['can_set_current_as_default'])
        self.assertTrue((await self.service.set_current_as_default(True))['ok'])

    async def asyncTearDown(self):
        self.adapter.fail_writes=False
        self.adapter.read_error=None
        self.scanner.report=ConflictReport([],[],False)
        await self.service.close()
        self.temp.cleanup()

    async def apply(self,left=1834,right=1730,confirmed=True):
        return await self.service.apply_selected(self.adapter.values.as_dict(),left,right,confirmed)

    async def test_selection_changes_preferences_only_and_triggers_independently(self):
        await self.service.select_values(1834,1730)
        await self.service.select_values(1834,1750)
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertEqual(self.adapter.writes,[])
        self.assertEqual(self.service.selected,{'lmax':1834,'rmax':1750})
        self.assertEqual(self.store.load('initial.json')['values'],INITIAL.as_dict())

    async def test_viewport_includes_default_and_caution_without_making_them_write_limits(self):
        state=self.service.status()
        for side in ('l','r'):
            window=state['windows'][side]
            self.assertLessEqual(window['min'],1820)
            self.assertGreaterEqual(window['max'],1820)
            self.assertLessEqual(window['min'],getattr(INITIAL,side+'max'))
        await self.service.select_values(1850,1730)
        self.assertEqual(self.service.selected['lmax'],1850)
        self.assertEqual(self.adapter.writes,[])
        state=await self.apply(1850,1730)
        self.assertEqual(state['current']['lmax'],1850)

    async def test_restart_loads_selection_without_any_automatic_write(self):
        await self.service.select_values(1834,1750)
        await self.service.initialize()
        state=self.service.status()
        self.assertEqual(state['selected'],{'lmax':1834,'rmax':1750})
        self.assertFalse(state['selected_matches_current'])
        self.assertEqual(self.adapter.writes,[])

    async def test_successful_manual_apply_is_read_back_without_starting_timer(self):
        state=await self.apply()
        self.assertEqual(self.adapter.values,INITIAL.proposal(1834,1730))
        self.assertEqual(state['last_apply']['status'],'verified')
        self.assertTrue(state['working_confirmation_available'])
        self.assertIsNone(self.service.timer)
        self.assertIsNone(self.service.profiles['lkg'])

    async def test_apply_requires_exact_confirmation_and_stable_expected_values(self):
        for confirmed in (False,1,'yes',None):
            with self.assertRaises(CalibrationError):
                await self.apply(confirmed=confirmed)
        self.adapter.values=INITIAL.proposal(1815,1730)
        with self.assertRaises(CalibrationError):
            await self.service.apply_selected(INITIAL.as_dict(),1834,1730,True)
        self.assertEqual(self.adapter.writes,[])

    async def test_failed_or_partial_apply_attempts_rollback_and_keeps_lkg(self):
        self.adapter.partial_once=True
        with self.assertRaises(CalibrationError):
            await self.apply(1834,1750)
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertIsNone(self.service.profiles['lkg'])
        self.assertIsNone(self.service.receipt)

    async def test_working_confirmation_requires_matching_verified_apply(self):
        with self.assertRaises(CalibrationError):
            await self.service.confirm_working(INITIAL.as_dict(),True)
        state=await self.apply()
        with self.assertRaises(CalibrationError):
            await self.service.confirm_working(state['current'],False)
        confirmed=await self.service.confirm_working(state['current'],True)
        self.assertEqual(confirmed['profiles']['lkg']['values'],state['current'])
        self.assertFalse(confirmed['profiles']['boot'])
        self.assertFalse(confirmed['profiles']['resume'])

    async def test_lkg_never_updated_after_external_calibration_change(self):
        state=await self.apply()
        self.adapter.values=INITIAL
        with self.assertRaises(CalibrationError):
            await self.service.confirm_working(state['current'],True)
        self.assertIsNone(self.service.profiles['lkg'])

    async def test_confirmed_preset_can_be_reapplied_after_reset(self):
        state=await self.apply()
        await self.service.confirm_working(state['current'],True)
        state=await self.apply(1854,1730)
        await self.service.confirm_working(state['current'],True)
        self.adapter.values=INITIAL  # System reset; no automatic event handler.
        self.assertEqual(self.service.selected['lmax'],1854)
        state=await self.apply(1854,1730)
        self.assertEqual(state['current']['lmax'],1854)

    async def test_invalid_wire_values_are_not_written(self):
        for value in (65536,True,'1834',518):
            with self.assertRaises(CalibrationError):
                await self.apply(value,1730)
        self.assertEqual(self.adapter.writes,[])

    async def test_conflicts_block_apply_but_keep_sliders_and_reading_available(self):
        self.scanner.report=ConflictReport(['external.service'],[],True)
        self.assertTrue((await self.service.read())['ok'])
        await self.service.select_values(1834,1730)
        self.assertFalse(self.service.status()['manual_apply_enabled'])
        with self.assertRaises(CalibrationError):
            await self.apply()
        self.assertEqual(self.adapter.writes,[])

    async def test_restore_uses_protected_backup_not_selected_or_current(self):
        await self.apply()
        await self.service.select_values(1854,1750)
        self.adapter.values=Values(61000,518,60000,506,12,13)
        state=await self.service.restore(True)
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertEqual(state['selected'],{'lmax':1854,'rmax':1750})
        self.assertFalse(state['selected_matches_current'])

    async def test_restore_missing_or_changed_backup_never_guesses(self):
        path=Path(self.temp.name)/'initial.json'
        path.unlink()
        (Path(self.temp.name)/'original-recovery/initial.json').unlink()
        with self.assertRaises(CalibrationError):
            await self.service.restore(True)
        self.assertEqual(self.adapter.writes,[])

    async def test_restore_checks_read_back_failure(self):
        await self.apply()
        self.adapter.ignore_writes=True
        state=await self.service.restore(True)
        self.assertFalse(state['ok'])
        self.assertIn('Read Calibration',state['error'])
        self.assertIsNotNone(state['current'])
        self.adapter.ignore_writes=False

    async def test_old_automatic_flags_are_migrated_off_without_applying(self):
        self.store.save('profiles.json',{'boot':True,'resume':True,'saved':None,'lkg':None})
        self.assertTrue((await self.service.initialize())['ok'])
        self.assertFalse(self.service.profiles['boot'])
        self.assertFalse(self.service.profiles['resume'])
        self.assertEqual(self.adapter.writes,[])
        with self.assertRaises(CalibrationError):
            await self.service.set_automatic(True,True)
        with self.assertRaises(CalibrationError):
            await self.service.auto_apply('boot')

    async def test_two_apply_calls_cannot_race(self):
        before=INITIAL.as_dict()
        results=await asyncio.gather(
            self.service.apply_selected(before,1834,1730,True),
            self.service.apply_selected(before,1814,1750,True),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(self.adapter.max_active,1)

    async def test_bounded_backend_error_log_is_preserved_for_diagnostics(self):
        self.service.event('backend_error','A controlled failure for diagnostic testing.')
        old=self.store.load('events.json')
        self.assertTrue(any(row['event']=='backend_error' for row in old))
        self.service.logs_loaded=False
        self.service.logs.clear()
        await self.service.initialize()
        self.assertTrue(any(row['message']=='A controlled failure for diagnostic testing.' for row in self.service.logs))

    async def test_configuration_repair_keeps_initial_bytes_and_manual_workflow(self):
        before=(Path(self.temp.name)/'initial.json').read_bytes()
        for name in ('preferences.json','profiles.json','events.json'):
            (Path(self.temp.name)/name).write_bytes(b'{broken JSON')
        self.service.logs_loaded=False
        state=await self.service.initialize()
        self.assertTrue(state['manual_apply_enabled'],state['error'])
        self.assertEqual({row['file'] for row in state['configuration_repairs']},
                         {'preferences.json','profiles.json','events.json'})
        for row in state['configuration_repairs']:
            self.assertEqual((Path(self.temp.name)/row['preserved']).read_bytes(),b'{broken JSON')
        await self.apply(1850,1750)
        await self.service.restore(True)
        self.assertEqual((Path(self.temp.name)/'initial.json').read_bytes(),before)

    async def test_migrated_valid_backup_does_not_lock_apply_or_restore(self):
        record=self.store.load('initial.json')
        record['provenance']='Imported v0.1.0 snapshot; negative-range fields observed during migration.'
        import hashlib
        from storage import canonical
        path=Path(self.temp.name)/'initial.json'
        document={'schema':1,'payload':record,'sha256':hashlib.sha256(canonical(record)).hexdigest()}
        path.write_bytes(canonical(document))  # Fixture only; production API is create-only.
        before=(Path(self.temp.name)/'initial.json').read_bytes()
        state=await self.service.initialize()
        self.assertTrue(state['manual_apply_enabled'])
        await self.apply(1850,1750)
        await self.service.restore(True)
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertEqual((Path(self.temp.name)/'initial.json').read_bytes(),before)

    async def test_original_is_never_replaced_by_modified_current_or_restart(self):
        path=Path(self.temp.name)/'initial.json'
        before=path.read_bytes()
        await self.apply(1880,1880)
        state=await self.service.read()
        self.assertFalse(state['current_matches_original'])
        self.assertEqual(state['initial']['values'],INITIAL.as_dict())
        with self.assertRaises(CalibrationError):
            await self.service.set_current_as_default(True)
        await self.service.close()
        self.service=ManualService(self.adapter,Store(self.temp.name),self.scanner)
        state=await self.service.initialize()  # Hardware stays modified across restart.
        self.assertEqual(state['current']['lmax'],1880)
        self.assertFalse(state['current_matches_original'])
        self.assertEqual(path.read_bytes(),before)
        restored=await self.service.restore(True)
        self.assertEqual(restored['current'],INITIAL.as_dict())
        self.assertTrue(restored['current_matches_original'])
        self.assertEqual(path.read_bytes(),before)

    async def test_missing_or_damaged_original_cannot_be_recreated_from_current(self):
        path=Path(self.temp.name)/'initial.json'
        for damage in (None,b'{damaged original'):
            if damage is None:
                path.unlink()
            else:
                path.write_bytes(damage)
            self.adapter.values=INITIAL.proposal(1880,1880)
            state=await self.service.read()
            self.assertFalse(state['can_set_current_as_default'])
            with self.assertRaises(CalibrationError):
                await self.service.set_current_as_default(True)
            self.assertEqual(path.read_bytes() if path.exists() else None,damage)
        self.assertEqual(self.adapter.writes,[])

    async def test_original_remains_available_when_hardware_read_fails(self):
        await self.service.close()
        self.adapter.read_error=CalibrationError('Injected hardware read failure')
        self.service=ManualService(self.adapter,Store(self.temp.name),self.scanner)
        state=await self.service.initialize()
        self.assertFalse(state['ok'])
        self.assertIsNone(state['current'])
        self.assertEqual(state['initial']['values'],INITIAL.as_dict())
        self.assertFalse(state['can_set_current_as_default'])
