"""Real RPC/adapter/transaction code; substituted process and HID transport."""
import importlib.util
import json
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from hardware import Hardware
from hardware_worker import transaction
from trigger_engine import Values,CalibrationError
from simulated import SimulatedScanner,IDENTITY
from test_native_transaction import FakeHID


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.hid=FakeHID()
        self.original=Values(1703,611,1727,623,12,13)  # Deliberately synthetic; never production defaults.
        self.hid.values=self.original
        self.adapter=Hardware()
        self.fail_refresh=False
        self.launched=[]
        def read():
            if self.fail_refresh:
                self.fail_refresh=False
                raise CalibrationError('Injected one-time post-worker HID read error')
            self.adapter.last_read=(self.hid.values,IDENTITY)
            return self.adapter.last_read
        self.adapter.read=read
        spec=importlib.util.spec_from_file_location('regression_plugin',Path(__file__).parents[1]/'main.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'decky':SimpleNamespace(DECKY_PLUGIN_SETTINGS_DIR=self.temp.name,logger=logging.getLogger('regression'))}):
            spec.loader.exec_module(module)
        self.plugin=module.Plugin()
        with patch.object(module,'Hardware',return_value=self.adapter),patch.object(module,'Scanner',return_value=SimulatedScanner()):
            await self.plugin._main()
        self.assertTrue((await self.plugin.set_current_as_default(True))['ok'])
        self.store=self.plugin.service.store
        self.backup=(Path(self.temp.name)/'initial.json').read_bytes()

    async def asyncTearDown(self):
        await self.plugin._unload()
        self.temp.cleanup()

    def process(self,command,**kwargs):
        self.assertIn('--apply',command)
        self.assertEqual(kwargs['pass_fds'],tuple(self.store.lease))
        self.launched.append(self.store.load('manual-operation.json'))
        test=self
        class Process:
            returncode=0
            response=None
            def poll(self):
                return 0 if self.response is not None else None
            def communicate(self,timeout=None):
                if self.response is None:
                    self.response=json.dumps(transaction(test.store,42,test.hid.ioctl,IDENTITY))
                    test.fail_refresh=True
                return self.response,None
        return Process()

    async def test_bad_journal_then_real_adapter_worker_route_apply_restore(self):
        journal=Path(self.temp.name)/'manual-operation.json'
        damage=b'{old invalid journal'
        journal.write_bytes(damage)
        state=await self.plugin.read_calibration()
        self.assertTrue(state['manual_apply_enabled'],state['error'])
        archived=self.store.load('manual-operation.json')['preserved_file']
        self.assertEqual((Path(self.temp.name)/archived).read_bytes(),damage)
        selected=await self.plugin.select_values(1820,1820)
        self.assertTrue(selected['manual_apply_enabled'])
        self.assertFalse(any(packet[1]==0xDF for packet in self.hid.commands))
        with patch('hardware.runtime_command',return_value=(['python','worker.py'],{})), \
             patch('hardware.subprocess.Popen',side_effect=self.process):
            applied=await self.plugin.apply_selected(state['current'],1820,1820,True)
            self.assertTrue(applied['ok'],applied['error'])
            self.assertIn('Calibration Applied',applied['message'])
            self.assertEqual(applied['selected'],{'lmax':1820,'rmax':1820})
            self.assertEqual(applied['current'],self.original.proposal(1820,1820).as_dict())
            self.assertTrue(applied['current_is_stale'])
            self.assertTrue(applied['manual_apply_enabled'])
            extended=await self.plugin.apply_selected(applied['current'],1880,1880,True)
            self.assertTrue(extended['ok'],extended['error'])
            self.assertEqual(self.hid.values,self.original.proposal(1880,1880))
            restored=await self.plugin.restore_default(True)
        self.assertTrue(restored['ok'],restored['error'])
        self.assertEqual(restored['current'],self.original.as_dict())
        self.assertEqual([row['mode'] for row in self.launched],['apply','apply','restore'])
        self.assertEqual(self.launched[0]['target'],self.original.proposal(1820,1820).as_dict())
        self.assertEqual(self.launched[1]['target'],self.original.proposal(1880,1880).as_dict())
        self.assertEqual(sum(packet[1]==0xDF for packet in self.hid.commands),6)
        self.assertEqual((Path(self.temp.name)/'initial.json').read_bytes(),self.backup)

    async def test_no_applied_without_a_fresh_worker_receipt_or_matching_read(self):
        service=self.plugin.service
        read=service._read
        async def failed_post_read():
            if self.launched:
                row=self.store.load('manual-operation.json')
                row['readback']=self.original.as_dict()
                self.store.save('manual-operation.json',row)
                raise CalibrationError('Injected reader failure')
            return await read()
        with patch('hardware.runtime_command',return_value=(['python','worker.py'],{})), \
             patch('hardware.subprocess.Popen',side_effect=self.process), \
             patch.object(service,'_read',side_effect=failed_post_read):
            result=await self.plugin.apply_selected(self.original.as_dict(),1820,1820,True)
        self.assertFalse(result['ok'])
        self.assertNotIn('Applied',result['message'])
        self.assertEqual(result['current'],self.original.as_dict())
        self.assertEqual(result['selected'],{'lmax':1820,'rmax':1820})
        self.assertFalse(result['manual_apply_enabled'])
        self.assertEqual(len(self.launched),1)

    async def test_active_worker_prevents_competing_read_and_keeps_known_values(self):
        self.adapter.active_process=SimpleNamespace(poll=lambda:None)
        with patch.object(self.adapter,'read',wraps=self.adapter.read) as reader:
            state=await self.plugin.read_calibration()
            reader.assert_not_called()
        self.assertFalse(state['ok'])
        self.assertEqual(state['current'],self.original.as_dict())
        self.adapter.active_process=None
        self.assertTrue((await self.plugin.read_calibration())['manual_apply_enabled'])
