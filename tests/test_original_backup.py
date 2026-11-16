"""Targeted archive redundancy/export and cold-start selection regressions."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from storage import Store,canonical
from trigger_engine import CalibrationError
from manual_service import ManualService
from simulated import INITIAL,SimulatedHardware,SimulatedScanner

class OriginalBackupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root);self.adapter=SimulatedHardware()
        self.service=ManualService(self.adapter,self.store,SimulatedScanner())
        await self.service.initialize();await self.service.set_current_as_default(True)
        self.primary=self.root/'initial.json';self.reserve=self.root/'original-recovery/initial.json'
        self.original=self.primary.read_bytes()
    async def asyncTearDown(self):
        self.adapter.read_error=None;await self.service.close();self.temp.cleanup()
    async def test_distinct_copies_remain_unchanged_across_apply_read_restore_and_restart(self):
        self.assertEqual(self.primary.read_bytes(),self.reserve.read_bytes())
        self.assertFalse(self.primary.samefile(self.reserve))
        await self.service.select_values(1820,1820)
        await self.service.apply_selected(INITIAL.as_dict(),1820,1820,True)
        for _ in range(2):
            state=await self.service.read()
            self.assertEqual(state['current']['lmax'],1820)
            self.assertFalse(state['can_set_current_as_default'])
            with self.assertRaises(CalibrationError):await self.service.set_current_as_default(True)
        await self.service.close()
        self.service=ManualService(self.adapter,Store(self.root),SimulatedScanner())
        state=await self.service.initialize()  # Restart/Resume retains hardware calibration.
        self.assertEqual(state['current']['lmax'],1820)
        self.assertEqual(state['selected'],{'lmax':1820,'rmax':1820})
        self.adapter.values=INITIAL  # User-reported full Shutdown / cold start.
        state=await self.service.read()
        self.assertEqual(state['current'],INITIAL.as_dict())
        self.assertEqual(state['selected'],{'lmax':1820,'rmax':1820})
        await self.service.restore(True)
        self.assertEqual(self.service.selected,{'lmax':1820,'rmax':1820})
        self.assertEqual(self.primary.read_bytes(),self.original)
        self.assertEqual(self.reserve.read_bytes(),self.original)
    async def test_primary_corruption_uses_reserve_without_replacing_damaged_history(self):
        self.primary.write_bytes(b'{damaged primary')
        self.adapter.values=INITIAL.proposal(1880,1880)
        state=await self.service.read()
        self.assertTrue(state['manual_apply_enabled'],state['error'])
        self.assertEqual(state['original_backup']['source'],'initial-reserve.json')
        await self.service.restore(True)
        self.assertEqual(self.adapter.values,INITIAL)
        self.assertEqual(self.primary.read_bytes(),b'{damaged primary')
        self.assertEqual(self.reserve.read_bytes(),self.original)
    async def test_bad_reserve_preserved_and_conflicting_valid_copies_refuse_writes(self):
        self.reserve.write_bytes(b'{damaged reserve')
        self.assertEqual(self.store.load('initial.json')['values'],INITIAL.as_dict())
        self.assertEqual(self.store.ensure_original_redundancy()['values'],INITIAL.as_dict())
        self.assertEqual(self.reserve.read_bytes(),b'{damaged reserve')
        record=json.loads(self.original)['payload'];record['values']['lmax']=1880
        self.reserve.write_bytes(canonical({'schema':1,'payload':record,'sha256':hashlib.sha256(canonical(record)).hexdigest()}))
        state=await self.service.read()
        self.assertFalse(state['manual_apply_enabled'])
        with self.assertRaisesRegex(CalibrationError,'disagree'):await self.service.restore(True)
        self.assertEqual(self.adapter.writes,[])
        self.assertEqual(self.primary.read_bytes(),self.original)
    async def test_export_remains_valid_without_current_hardware_read_and_uses_original(self):
        self.primary.write_bytes(b'{damaged primary')
        self.adapter.read_error=CalibrationError('Hardware read unavailable')
        reads=self.adapter.reads if hasattr(self.adapter,'reads') else None
        result=self.store.export_original(self.root/'exports')
        exported=Path(result['path']);doc=json.loads(exported.read_bytes())
        self.assertEqual(doc['payload']['values'],INITIAL.as_dict())
        self.assertEqual(doc['sha256'],hashlib.sha256(canonical(doc['payload'])).hexdigest())
        self.assertFalse(doc['payload']['factory_verified'])
        recovered=Store(self.root/'external-recovery')
        (recovered.root/'initial.json').write_bytes(exported.read_bytes())
        self.assertEqual(recovered.load('initial.json')['values'],INITIAL.as_dict())
        self.assertEqual(self.primary.read_bytes(),b'{damaged primary')
        self.assertEqual(self.reserve.read_bytes(),self.original)
    async def test_upgrade_copies_existing_original_without_adopting_modified_current(self):
        self.reserve.unlink();self.adapter.values=INITIAL.proposal(1880,1880)
        state=await self.service.initialize()
        self.assertTrue(state['manual_apply_enabled'],state['error'])
        self.assertEqual(state['current']['lmax'],1880)
        self.assertEqual(self.primary.read_bytes(),self.original)
        self.assertEqual(self.reserve.read_bytes(),self.original)
    async def test_initial_capture_preserves_preexisting_selected_preferences(self):
        await self.service.close()
        root=self.root/'new-setup';adapter=SimulatedHardware()
        self.service=ManualService(adapter,Store(root),SimulatedScanner())
        await self.service.initialize()
        await self.service.select_values(1820,1820)
        self.assertTrue(self.service.status()['can_set_current_as_default'])
        with self.assertRaises(CalibrationError):await self.service.set_current_as_default(False)
        state=await self.service.set_current_as_default(True)
        self.assertEqual(state['selected'],{'lmax':1820,'rmax':1820})
        self.assertEqual(state['initial']['values'],INITIAL.as_dict())
        self.assertEqual(adapter.writes,[])

if __name__=='__main__':unittest.main()
