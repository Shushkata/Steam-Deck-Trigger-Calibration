import asyncio
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
from diagnostics import sanitize,export_archive,payload
from live_input import parse_input,Measurements,InputMonitor
from manual_service import ManualService
from storage import Store
from simulated import SimulatedHardware,SimulatedScanner


class InputMeasurementTests(unittest.TestCase):
    def test_exact_hid_offsets_and_header_validation(self):
        report=bytearray(64);report[:4]=b'\x01\x00\x09\x40'
        struct.pack_into('<I',report,4,123)
        struct.pack_into('<HH',report,44,1234,32767)
        self.assertEqual(parse_input(report),{'left':1234,'right':32767,'sequence':123})
        for bad in (report[:63],bytes(64),b'\x00'+report[1:]):
            self.assertIsNone(parse_input(bad))

    def test_report_percent_is_marked_unverified_and_raw_values_preserved(self):
        values=Measurements();values.observe({'left':0,'right':32767,'sequence':1},now=0)
        state=values.snapshot(now=.1)
        self.assertEqual(state['percent'],[0,100])
        self.assertEqual(state['values'],[0,32767])
        self.assertEqual(state['full_analog_travel'],'Inconclusive')
        self.assertIn('unverified',state['semantics'])

    def test_idle_lasts_five_seconds_and_never_claims_unverified_success(self):
        values=Measurements();values.start_idle(now=10)
        for n in range(51):
            values.observe({'left':0,'right':0,'sequence':n},now=10+n*.1)
        state=values.snapshot(now=14.99)
        self.assertEqual(state['idle']['state'],'measuring')
        state=values.snapshot(now=15)
        self.assertEqual(state['idle']['result'],'Inconclusive')
        self.assertEqual(state['idle']['maximum'],[0,0])
        self.assertEqual(state['idle']['samples'],51)

    def test_noise_and_one_sided_nonzero_are_reported_without_false_activation_claim(self):
        values=Measurements();values.start_idle(now=0)
        for n,(left,right) in enumerate(((0,0),(1,0),(2,0),(1,0))):
            values.observe({'left':left,'right':right,'sequence':n},now=n)
        result=values.snapshot(now=5)['idle']
        self.assertEqual(result['maximum'],[2,0])
        self.assertEqual(result['nonzero_samples'],[3,0])
        self.assertEqual(result['result'],'Inconclusive')

    def test_missing_stream_is_inconclusive_not_zero_input_success(self):
        values=Measurements();values.start_idle(now=0)
        state=values.snapshot(now=5)
        self.assertTrue(state['stale'])
        self.assertEqual(state['idle']['samples'],0)
        self.assertEqual(state['idle']['result'],'Inconclusive')

    def test_full_report_range_returned_zero_and_reset_are_measurements_only(self):
        values=Measurements()
        for n,left in enumerate((0,16000,32767,0)):
            values.observe({'left':left,'right':0,'sequence':n},now=n)
        state=values.snapshot(now=3)
        self.assertEqual(state['maximum'],[32767,0])
        self.assertEqual(state['minimum'],[0,0])
        self.assertEqual(state['returned_to_report_zero'],[True,False])
        self.assertEqual(state['full_analog_travel'],'Inconclusive')
        values.reset()
        self.assertIsNone(values.snapshot()['values'])
        self.assertEqual(values.count,0)

    def test_out_of_known_axis_scale_does_not_fake_a_percentage(self):
        values=Measurements();values.observe({'left':65535,'right':10,'sequence':1})
        self.assertIsNone(values.snapshot()['percent'])
        self.assertEqual(values.snapshot()['values'],[65535,10])


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_stops_on_explicit_close_and_lease_expiry(self):
        program='import time,json\nfor n in range(1000):\n print(json.dumps(dict(left=n%20,right=0,sequence=n)),flush=True);time.sleep(.01)'
        monitor=InputMonitor(lambda:([sys.executable,'-u','-c',program],None),lease_seconds=.08)
        self.assertIsNone(monitor.process)
        self.assertIsNone(monitor.task)
        state=await monitor.start()
        token=state['token']
        await asyncio.sleep(.04)
        self.assertTrue(monitor.state(token)['active'])
        await monitor.stop(token)
        self.assertIsNone(monitor.process)
        state=await monitor.start()
        await asyncio.sleep(.4)
        self.assertIsNotNone(monitor.process.returncode)
        await monitor.stop(state['token'])

    async def test_reader_errors_are_visible_and_no_fake_sample_is_produced(self):
        program='import json;print(json.dumps(dict(error="Permission denied opening input reader")),flush=True)'
        monitor=InputMonitor(lambda:([sys.executable,'-u','-c',program],None))
        state=await monitor.start()
        await asyncio.wait_for(asyncio.shield(monitor.task),2)
        result=monitor.state(state['token'])
        self.assertIn('Permission denied',result['error'])
        self.assertIsNone(result['values'])
        await monitor.stop()


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_contains_required_state_without_identity_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=SimulatedHardware()
            service=ManualService(adapter,Store(Path(directory)/'state'),SimulatedScanner())
            await service.initialize()
            await service.set_current_as_default(True)
            await service.select_values(1834,1730)
            service.event('backend_error','token=hidden password=hidden /home/test-person/private')
            monitor=InputMonitor(lambda:([],{}))
            document=payload(service,monitor,'test-decky-version')
            result=export_archive(document,Path(directory)/'export')
            with zipfile.ZipFile(result['path']) as archive:
                self.assertEqual(set(archive.namelist()),{'diagnostics.json','README.txt'})
                text=archive.read('diagnostics.json').decode()
                parsed=json.loads(text)
                for key in ('plugin_version','decky_version','device_detection','calibration_read_status',
                            'initial_calibration','current_calibration','selected_values','apply_result',
                            'trigger_test','dependency_errors','conflicting_services','backend_error_log'):
                    self.assertIn(key,parsed)
                self.assertNotIn('hidden',text)
                self.assertNotIn('test-person',text)
                self.assertNotIn('"identity"',text)
                self.assertNotIn('"token"',text)
                self.assertEqual(parsed['selected_values'],{'lmax':1834,'rmax':1730})
            self.assertEqual(adapter.writes,[])
            await service.close()

    async def test_sanitize_removes_nested_auth_fields(self):
        clean=sanitize({'token':'x','identity':'y','nested':[{'access_token':'z','value':123}]})
        self.assertEqual(clean,{'nested':[{'value':123}]})
