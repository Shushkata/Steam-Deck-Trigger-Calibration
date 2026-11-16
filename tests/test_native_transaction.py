import json
import struct
import tempfile
import unittest
from unittest.mock import patch,MagicMock
import subprocess
from hardware_worker import transaction
from hardware import Hardware
from storage import Store
from trigger_engine import Values,CalibrationError
from simulated import INITIAL,IDENTITY


class FakeHID:
    def __init__(self):
        self.values=INITIAL
        self.side=0
        self.commands=[]
        self.fail_right_once=False
        self.fail_rollback=False
        self.ignore=False
        self.after_left=None

    def ioctl(self,fd,op,buffer,mutate):
        if op==0xC0414806:
            self.commands.append(bytes(buffer))
            if buffer[1]==0xDE:
                self.side=buffer[3]
            elif buffer[1]==0xDF:
                side,maximum,minimum,negative=struct.unpack('<BHHB',buffer[3:9])
                if side==1 and self.fail_right_once:
                    self.fail_right_once=False
                    if self.fail_rollback:
                        self.fail_rollback='active'
                    raise OSError('simulated right-trigger failure')
                if self.fail_rollback=='active':
                    raise OSError('simulated rollback failure')
                if not self.ignore:
                    change=({'lmax':maximum,'lmin':minimum,'lneg':negative} if side==0 else
                            {'rmax':maximum,'rmin':minimum,'rneg':negative})
                    self.values=Values.from_dict(self.values.as_dict()|change)
                if side==0 and self.after_left:
                    self.after_left()
            else:
                raise AssertionError('Unexpected firmware command')
            return 65
        assert op==0xC0414807
        side=self.side
        values=self.values
        maximum,minimum,negative=(values.lmax,values.lmin,values.lneg) if side==0 else (values.rmax,values.rmin,values.rneg)
        buffer[:]=bytearray((bytes([0,0xDE,6])+struct.pack('<BHHB',side,maximum,minimum,negative)).ljust(65,b'\0'))
        return 65


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(self.temp.name)
        self.store.create_initial({'identity':IDENTITY,'values':INITIAL.as_dict()})
        self.hid=FakeHID()
        self.target=INITIAL.proposal(1854,1750)
        self.prepare()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def prepare(self,**changes):
        self.store.save('manual-operation.json',dict(operation_id='a'*32,identity=IDENTITY,
            status='prepared',mode='apply',expected=INITIAL.as_dict(),target=self.target.as_dict())|changes)

    def run_transaction(self):
        return transaction(self.store,42,self.hid.ioctl,IDENTITY)

    def test_both_sides_and_opaque_bytes_written_then_verified(self):
        result=self.run_transaction()
        self.assertEqual(result['values'],self.target.as_dict())
        writes=[data for data in self.hid.commands if data[1]==0xDF]
        self.assertEqual(len(writes),2)
        self.assertEqual(writes[0][2],6)
        self.assertEqual(struct.unpack('<BHHB',writes[0][3:9]),(0,1854,518,12))
        self.assertEqual(struct.unpack('<BHHB',writes[1][3:9]),(1,1750,506,13))
        self.assertTrue(all(data[1] in (0xDE,0xDF) for data in self.hid.commands))
        self.assertEqual(self.store.load('manual-operation.json')['status'],'verified')

    def test_partial_write_failure_restores_both_triggers(self):
        self.hid.fail_right_once=True
        with self.assertRaises(CalibrationError):
            self.run_transaction()
        self.assertEqual(self.hid.values,INITIAL)
        record=self.store.load('manual-operation.json')
        self.assertEqual(record['status'],'failed')
        self.assertEqual(record['rollback'],'verified')

    def test_failed_rollback_is_explicit_and_journal_preserved(self):
        self.hid.fail_right_once=True
        self.hid.fail_rollback=True
        with self.assertRaises(CalibrationError):
            self.run_transaction()
        self.assertEqual(self.store.load('manual-operation.json')['status'],'rollback_failed')

    def test_stale_expected_or_invalid_profile_never_sets_calibration(self):
        for changes in ({'expected':self.target.as_dict()},{'target':INITIAL.as_dict() | {'lmin':519}}):
            self.prepare(**changes)
            with self.assertRaises(CalibrationError):
                self.run_transaction()
        self.assertFalse(any(data[1]==0xDF for data in self.hid.commands))

    def test_read_back_mismatch_rolls_back(self):
        self.hid.ignore=True
        with self.assertRaises(CalibrationError):
            self.run_transaction()
        self.assertEqual(self.store.load('manual-operation.json')['rollback'],'verified')

    def test_recovery_target_must_be_protected_record(self):
        self.prepare(mode='restore')
        with self.assertRaises(CalibrationError):
            self.run_transaction()
        self.assertFalse(any(data[1]==0xDF for data in self.hid.commands))
        self.hid.values=self.target
        self.prepare(mode='restore',target=INITIAL.as_dict())
        self.run_transaction()
        self.assertEqual(self.hid.values,INITIAL)

    def test_restore_can_repair_invalid_current_order(self):
        from trigger_engine import InvalidCalibration
        self.prepare(mode='restore',target=INITIAL.as_dict())
        from hardware_worker import stable_read
        reads=0
        def read(fd,ioctl):
            nonlocal reads
            reads+=1
            if reads==1:
                raise InvalidCalibration('Current MAX <= MIN')
            return stable_read(fd,ioctl)
        with patch('hardware_worker.stable_read',side_effect=read):
            self.run_transaction()
        self.assertEqual(self.hid.values,INITIAL)
        self.assertEqual(self.store.load('manual-operation.json')['status'],'verified')

    def test_cancel_requested_between_trigger_writes_rolls_back(self):
        def cancel():
            self.hid.after_left=None
            record=self.store.load('manual-operation.json')
            record['cancel_requested']=True
            self.store.save('manual-operation.json',record)
        self.hid.after_left=cancel
        with self.assertRaisesRegex(CalibrationError,'cancelled'):
            self.run_transaction()
        self.assertEqual(self.hid.values,INITIAL)

    def test_adapter_inherits_lock_descriptors_and_requires_scoped_authorization(self):
        self.prepare(status='verified')
        adapter=Hardware();adapter.configure(self.store)
        self.store.lease=[42,43]  # Stubbed Popen only; never passed to an OS call.
        adapter.last_read=(INITIAL,IDENTITY)
        with self.assertRaises(CalibrationError):
            adapter.write(self.target,IDENTITY)
        process=MagicMock();process.returncode=0
        def complete(timeout=None):
            record=self.store.load('manual-operation.json');record['status']='verified'
            self.store.save('manual-operation.json',record)
            return json.dumps({'values':self.target.as_dict()}),None
        process.communicate.side_effect=complete
        try:
            with patch('hardware.runtime_command',return_value=(['python','worker'],{})), \
                 patch('hardware.subprocess.Popen',return_value=process) as popen:
                with adapter.authorized_operation('apply'):
                    adapter.write(self.target,IDENTITY)
                self.assertEqual(popen.call_args.kwargs['pass_fds'],(42,43))
                self.assertTrue(popen.call_args.kwargs['start_new_session'])
                self.assertFalse(adapter.can_write)
        finally:
            self.store.lease=None

    def test_timeout_never_kills_independent_writer(self):
        self.prepare(status='verified')
        adapter=Hardware();adapter.configure(self.store);adapter.last_read=(INITIAL,IDENTITY)
        self.store.lease=[42,43]
        process=MagicMock();process.poll.return_value=None
        process.communicate.side_effect=subprocess.TimeoutExpired('apply',45)
        try:
            with patch('hardware.runtime_command',return_value=(['python','worker'],{})), \
                 patch('hardware.subprocess.Popen',return_value=process):
                with adapter.authorized_operation('apply'):
                    with self.assertRaisesRegex(CalibrationError,'still completing'):
                        adapter.write(self.target,IDENTITY)
            process.kill.assert_not_called()
            process.terminate.assert_not_called()
            with self.assertRaises(CalibrationError):
                with adapter.authorized_operation('apply'):
                    pass
        finally:
            self.store.lease=None
