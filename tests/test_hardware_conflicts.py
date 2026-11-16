import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from conflicts import Scanner
from hardware import Hardware
from hardware_worker import discover, parse_report, read_fd, request
from trigger_engine import CalibrationError
from simulated import INITIAL


class ProtocolTests(unittest.TestCase):
    def report(self, side=0, maximum=1814, minimum=518, negative=12):
        return bytearray((bytes([0,0xDE,6])+struct.pack('<BHHB',side,maximum,minimum,negative)).ljust(65,b'\0'))

    def test_only_get_cal_requests_and_exact_ioctl_direction(self):
        calls = []
        side = 0
        def ioctl(fd,operation,buffer,mutate):
            nonlocal side
            calls.append((operation,bytes(buffer)))
            self.assertEqual(fd,42)
            self.assertTrue(mutate)
            if operation == 0xC0414806:  # Linux HIDIOCSFEATURE, query payload only.
                self.assertEqual(buffer[:3],b'\0\xde\x01')
                side = buffer[3]
                return 65
            self.assertEqual(operation,0xC0414807)  # HIDIOCGFEATURE.
            buffer[:] = self.report(side,1814 if side==0 else 1730,518 if side==0 else 506,12+side)
            return 65
        self.assertEqual(read_fd(42,ioctl),INITIAL)
        self.assertEqual(len(calls),4)
        self.assertTrue(all(data[1] != 0xDF for op,data in calls if op == 0xC0414806))

    def test_wrong_side_opcode_length_and_truncated_replies_are_rejected(self):
        cases = [self.report(side=1),self.report()[:8]]
        wrong_opcode = self.report(); wrong_opcode[1] = 0xDF
        wrong_length = self.report(); wrong_length[2] = 5
        wrong_id = self.report(); wrong_id[0] = 1
        cases += [wrong_opcode,wrong_length,wrong_id]
        for report in cases:
            with self.subTest(report=report), self.assertRaises(CalibrationError):
                parse_report(report,0)

    def test_short_query_transfer_is_not_accepted(self):
        with self.assertRaises(CalibrationError):
            read_fd(42,lambda *args: 4)

    def test_invalid_trigger_side(self):
        for side in (-1,2,'0',True):
            with self.assertRaises(CalibrationError):
                request(side)

    def test_missing_runtime_or_permission_is_clear_not_simulated(self):
        with patch('hardware.sys.platform','win32'):
            with self.assertRaisesRegex(CalibrationError,'SteamOS'):
                Hardware().read()
        with patch('hardware.sys.platform','linux'), patch('hardware.subprocess.run',side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(CalibrationError,'Cannot read'):
                Hardware().read()

    def test_read_helper_has_bounded_timeout(self):
        with patch('hardware.sys.platform','linux'), patch('hardware.subprocess.run',side_effect=subprocess.TimeoutExpired('helper',8)):
            with self.assertRaisesRegex(CalibrationError,'timed out'):
                Hardware().read()

    def test_malformed_helper_result_rejected(self):
        for stdout in ('{}','{"values":{}}','not-json'):
            result = subprocess.CompletedProcess([],0,stdout,'')
            with patch('hardware.sys.platform','linux'), patch('hardware.subprocess.run',return_value=result):
                with self.assertRaises(CalibrationError):
                    Hardware().read()

    def test_permission_error_from_helper_is_understandable(self):
        result = subprocess.CompletedProcess([],1,json.dumps({'error':'Permission denied opening the built-in controller.'}),'')
        with patch('hardware.sys.platform','linux'), patch('hardware.subprocess.run',return_value=result):
            with self.assertRaisesRegex(CalibrationError,'Permission denied'):
                Hardware().read()

    def test_frozen_decky_uses_system_python_instead_of_restarting_loader(self):
        result = subprocess.CompletedProcess([],0,json.dumps({'values':INITIAL.as_dict(),'identity':'a'*64}),'')
        with patch('hardware.sys.platform','linux'), patch('hardware.sys.frozen',True,create=True), \
             patch('hardware.os.access',return_value=True), patch('hardware.subprocess.run',return_value=result) as run:
            self.assertEqual(Hardware().read()[0],INITIAL)
            command = run.call_args.args[0]
            self.assertEqual(command[:2],['/usr/bin/python3','-I'])
            self.assertEqual(command[-1],'--read')
            self.assertEqual(run.call_args.kwargs['timeout'],8)

    def test_frozen_decky_missing_system_python_fails_closed(self):
        with patch('hardware.sys.platform','linux'), patch('hardware.sys.frozen',True,create=True), \
             patch('hardware.os.access',return_value=False):
            with self.assertRaisesRegex(CalibrationError,'Python 3 is missing'):
                Hardware().read()

    def fake_sysfs(self,root,board='Jupiter',vendor='Valve',removable='fixed',product='1205'):
        dmi = root/'class/dmi/id'; dmi.mkdir(parents=True)
        (dmi/'sys_vendor').write_text(vendor); (dmi/'board_name').write_text(board)
        # Minimal synthetic parent hierarchy; no actual /sys or /dev access.
        usb = root/'class/hidraw'; usb.mkdir(parents=True)
        (usb/'idVendor').write_text('28de'); (usb/'idProduct').write_text(product)
        (usb/'removable').write_text(removable)
        interface = usb/'hidraw0'; (interface/'device').mkdir(parents=True)
        (interface/'bInterfaceNumber').write_text('02')
        machine = root/'machine-id'; machine.write_text('simulated-device-identity')
        return machine

    def test_lcd_oled_identity_and_external_device_exclusion(self):
        for board in ('Jupiter','Galileo'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); machine = self.fake_sysfs(root,board=board)
                path,identity = discover(root,machine)
                self.assertEqual(path,Path('/dev/hidraw0'))
                self.assertEqual(len(identity),64)
                self.assertEqual(discover(root,machine)[1],identity)
        for change in ({'vendor':'Other'},{'board':'Other'},{'removable':'removable'},
                       {'removable':'unknown'},{'product':'0ce6'}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); machine = self.fake_sysfs(root,**change)
                with self.assertRaises(CalibrationError):
                    discover(root,machine)

    def test_ambiguous_multiple_candidates_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); machine = self.fake_sysfs(root)
            other = root/'class/hidraw/hidraw1'
            (other/'device').mkdir(parents=True)
            (other/'bInterfaceNumber').write_text('02')
            with self.assertRaisesRegex(CalibrationError,'unique'):
                discover(root,machine)


class ConflictTests(unittest.TestCase):
    def test_complete_not_found_properties_are_not_a_conflict_inspection_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def run(command,**kwargs):
                units=[item for item in command if item.endswith('.service')]
                text='\n\n'.join(f'Id={unit}\nLoadState=not-found\nActiveState=inactive\nUnitFileState=' for unit in units)
                return subprocess.CompletedProcess(command,1,text,'Some units were not found')
            report=Scanner(root,run,[root],root/'proc').scan()
            self.assertTrue(report.verified)
            self.assertFalse(report.blockers)
    def test_active_enabled_and_custom_service_are_detected_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit = root/'custom.service'
            unit.write_text('ExecStart=/home/deck/auto_trigger_cal.sh')
            commands = []
            def run(command,**kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command,0,
                    'Id=custom.service\nLoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\n\n'+
                    'Id=trigger-cal-resume.service\nLoadState=loaded\nActiveState=active\nUnitFileState=disabled','')
            report = Scanner(root,run,system_dirs=[root],proc=root/'empty-proc').scan()
            self.assertEqual(len(report.blockers),2)
            self.assertTrue(report.suspected_initial)
            self.assertTrue(report.verified)
            self.assertTrue(all('show' in command for command in commands))
            self.assertEqual(unit.read_text(),'ExecStart=/home/deck/auto_trigger_cal.sh')

    def test_installed_tool_is_warning_not_active_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'overwrite_trigger_cal').write_bytes(b'not executed')
            run = lambda command,**kwargs:subprocess.CompletedProcess(command,0,'LoadState=not-found','')
            report = Scanner(root,run,[root],root/'empty-proc').scan()
            self.assertTrue(report.suspected_initial)
            self.assertTrue(report.warnings)
            self.assertFalse(report.blockers)

    def test_missing_dependency_and_inaccessible_user_bus_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for run in (lambda *a,**k: (_ for _ in ()).throw(FileNotFoundError('systemctl')),
                        lambda command,**kwargs:subprocess.CompletedProcess(command,1,'','No bus')):
                report = Scanner(root,run,[root],root/'empty-proc').scan()
                self.assertFalse(report.verified)
                self.assertTrue(report.warnings)

    def test_absent_user_bus_without_installed_tools_does_not_lock_manual_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def run(command,**kwargs):
                if '--user' in command:
                    return subprocess.CompletedProcess(command,1,'','No user bus')
                return subprocess.CompletedProcess(command,0,'LoadState=not-found','')
            scanner=Scanner(root,run,[root],root/'proc')
            self.assertTrue(scanner.scan().verified)
            (root/'trigger-cal-boot.service').write_text('ExecStart=/home/deck/auto_trigger_cal.sh')
            self.assertFalse(scanner.scan().verified)

    def test_running_external_tool_blocks_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); process = root/'proc/123'; process.mkdir(parents=True)
            (process/'cmdline').write_bytes(b'/home/deck/overwrite_trigger_cal\0')
            run = lambda command,**kwargs:subprocess.CompletedProcess(command,0,'LoadState=not-found','')
            report = Scanner(root,run,[root],root/'proc').scan()
            self.assertTrue(any('PID 123' in message for message in report.blockers))
