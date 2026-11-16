import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import stat
from storage import Store
from trigger_engine import CalibrationError, Values, check_proposal, marker_position, slider_window
from simulated import INITIAL


class ModelTests(unittest.TestCase):
    def test_complete_legacy_read(self):
        data = 'Current Left Trigger Cal: (1814, 518)\nCurrent Right Trigger Cal: (1730, 506)'
        self.assertEqual(Values.parse(data), Values(1814, 518, 1730, 506))

    def test_incomplete_duplicate_and_malformed_readings(self):
        valid = 'Current Left Trigger Cal: (1814, 518)\nCurrent Right Trigger Cal: (1730, 506)'
        for text in ('', valid.split('\n')[0], valid+valid, valid.replace('1814','-1'), valid.replace('518','65536')):
            with self.subTest(text=text), self.assertRaises(CalibrationError):
                Values.parse(text)

    def test_exact_fields_and_strict_integer_validation(self):
        for field in INITIAL.as_dict():
            doc = INITIAL.as_dict()
            del doc[field]
            with self.assertRaises(CalibrationError):
                Values.from_dict(doc)
        for invalid in (True, '1814', 1814.0, -1, 65536, None):
            doc = INITIAL.as_dict()
            doc['lmax'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(CalibrationError):
                Values.from_dict(doc)

    def test_min_max_order_and_negative_field_bounds(self):
        for change in ({'lmax': 518}, {'rmin': 1730}, {'lneg': 256}, {'rneg': -1}):
            with self.subTest(change=change), self.assertRaises(CalibrationError):
                Values.from_dict(INITIAL.as_dict() | change)

    def test_independent_max_adjustments_preserve_all_other_fields(self):
        for left, right in ((1834,1730),(1814,1710),(1815,1729)):
            proposal = INITIAL.proposal(left,right)
            check_proposal(INITIAL,proposal)
            self.assertEqual((proposal.lmin,proposal.rmin,proposal.lneg,proposal.rneg),(518,506,12,13))

    def test_delta_policy_and_opaque_fields_locked(self):
        for change in ({'lmax':1835},{'rmax':1751},{'lmin':517},{'rneg':14}):
            with self.subTest(change=change), self.assertRaises(CalibrationError):
                check_proposal(INITIAL, Values.from_dict(INITIAL.as_dict() | change))

    def test_dynamic_viewport_and_wire_endpoints(self):
        self.assertEqual(slider_window(1814,1814,518),{'min':1794,'max':1834})
        self.assertEqual(slider_window(1834,1814,518),{'min':1794,'max':1854})
        self.assertEqual(slider_window(65535,65535,65534),{'min':65535,'max':65535})
        self.assertEqual(slider_window(519,519,518)['min'],519)

    def test_default_and_caution_markers_above_caution(self):
        self.assertEqual(marker_position(1840,1820,1920),20)
        self.assertEqual(marker_position(1900,1820,1920),80)
        self.assertIsNone(marker_position(1840,1880,1920))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.record = {'identity':'a'*64,'values':INITIAL.as_dict()}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_initial_is_atomic_and_never_overwritten(self):
        self.store.create_initial(self.record)
        before = (self.store.root/'initial.json').read_bytes()
        with self.assertRaises(CalibrationError):
            self.store.create_initial(self.record | {'identity':'b'*64})
        with self.assertRaises(CalibrationError):
            self.store.save('initial.json',self.record | {'identity':'b'*64})
        with self.assertRaises(CalibrationError):
            self.store.set_current_initial(self.record | {'identity':'b'*64},confirmed=True)
        self.assertEqual(before,(self.store.root/'initial.json').read_bytes())
        self.assertEqual(Store(self.temp.name).load('initial.json'),self.record)

    def test_corruption_preserved_and_rejected(self):
        self.store.create_initial(self.record)
        path = self.store.root/'initial.json'
        document = json.loads(path.read_text())
        document['payload']['values']['lmax'] = 1999
        path.write_text(json.dumps(document))
        damaged = path.read_bytes()
        self.assertEqual(self.store.load('initial.json'),self.record)
        self.assertEqual(self.store.original_status['source'],'initial-reserve.json')
        self.assertEqual(path.read_bytes(),damaged)

    def test_incomplete_json_is_rejected(self):
        (self.store.root/'initial.json').write_text('{')
        with self.assertRaises(CalibrationError):
            self.store.load('initial.json')

    def test_decky_host_ownership_is_reclaimed_without_rewriting_backup(self):
        self.store.create_initial(self.record)
        self.store.acquire()
        self.store.previous_owner=1000
        path=self.store.root/'initial.json'
        before=path.read_bytes()
        real_stat=Path.stat
        def host_owned(candidate,*args,**kwargs):
            metadata=real_stat(candidate,*args,**kwargs)
            return SimpleNamespace(st_uid=1000,st_mode=stat.S_IFREG|0o600,st_size=metadata.st_size)
        from unittest.mock import Mock
        platform=SimpleNamespace(name='posix',geteuid=lambda:0,getegid=lambda:0,chown=Mock(),chmod=Mock())
        with patch('storage.os',platform),patch.object(Path,'stat',host_owned):
            self.assertEqual(self.store.load('initial.json'),self.record)
        platform.chown.assert_called_once_with(path,0,0)
        platform.chmod.assert_called_once_with(path,0o600)
        self.assertEqual(path.read_bytes(),before)

    def test_atomic_replace_failure_preserves_previous_profile(self):
        self.store.save('profiles.json',{'old':True})
        with patch('storage.os.replace',side_effect=PermissionError('simulated permission denial')):
            with self.assertRaises(PermissionError):
                self.store.save('profiles.json',{'new':True})
        self.assertEqual(self.store.load('profiles.json'),{'old':True})
        self.assertEqual(list(self.store.root.glob('.atomic-*')),[])

    def test_cross_process_style_lease_blocks_second_instance(self):
        self.store.acquire()
        other = Store(self.temp.name)
        try:
            with self.assertRaises(CalibrationError):
                other.acquire()
        finally:
            other.close()

    def test_only_allowed_storage_names(self):
        for name in ('../initial.json','arbitrary.json','/etc/passwd'):
            with self.assertRaises(CalibrationError):
                self.store.save(name,{})

    def test_global_lease_blocks_duplicate_installations_with_different_storage(self):
        lock = self.store.root/'global.lock'
        first = Store(self.store.root/'first',global_lock=lock)
        second = Store(self.store.root/'second',global_lock=lock)
        try:
            first.acquire()
            with self.assertRaises(CalibrationError):
                second.acquire()
        finally:
            first.close()
            second.close()

    def test_legacy_folder_import_is_read_only_and_ambiguous_sources_are_refused(self):
        first = self.store.root/'old-one'; first.mkdir()
        second = self.store.root/'old-two'; second.mkdir()
        old = {'factory_candidate':{'lmax':1814,'lmin':518,'rmax':1730,'rmin':506}}
        (first/'calibration.json').write_text(json.dumps(old))
        imported = Store(self.store.root/'new',[first,second])
        self.assertEqual(imported.legacy(),old)
        self.assertEqual(json.loads((first/'calibration.json').read_text()),old)
        (second/'calibration.json').write_text(json.dumps(old))
        with self.assertRaises(CalibrationError):
            imported.legacy()

    def test_protected_posix_modes_when_available(self):
        self.store.create_initial(self.record)
        if os.name == 'posix':
            self.assertEqual((self.store.root/'initial.json').stat().st_mode & 0o777,0o600)
            self.assertEqual(self.store.root.stat().st_mode & 0o777,0o700)
        else:
            # Windows cannot validate POSIX ownership/modes; verify file survives
            # reopening. Actual SteamOS modes remain a device acceptance check.
            self.assertEqual(self.store.load('initial.json'),self.record)
