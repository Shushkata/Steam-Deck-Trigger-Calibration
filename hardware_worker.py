"""Native helpers: bounded reads, read-only input stream, independent Apply."""
from __future__ import annotations
import hashlib
import errno
import json
import os
from pathlib import Path
import struct
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trigger_engine import CalibrationError, InvalidCalibration, Values, check_manual_proposal, public_error

VID, PID = '28de', '1205'
REPORT_SIZE = 65
GET_CAL = 0xDE
SET_CAL = 0xDF


def text_file(path):
    return path.read_text(encoding='utf-8').strip()


def discover(sys_root=Path('/sys'), machine_id_path=Path('/etc/machine-id')):
    dmi = sys_root / 'class/dmi/id'
    try:
        vendor = text_file(dmi / 'sys_vendor').lower()
        board = text_file(dmi / 'board_name')
        if vendor not in ('valve', 'valve corporation') or board not in ('Jupiter', 'Galileo'):
            raise CalibrationError('Unsupported hardware. Only an identified built-in Steam Deck controller is eligible.')
        candidates = []
        for entry in (sys_root / 'class/hidraw').glob('hidraw*'):
            hid_device = (entry / 'device').resolve()
            interface = next((p for p in hid_device.parents if (p / 'bInterfaceNumber').is_file()), None)
            if interface is None or text_file(interface / 'bInterfaceNumber') != '02':
                continue
            usb = interface.parent
            if not all((usb / name).is_file() for name in ('idVendor', 'idProduct', 'removable')):
                continue
            if (text_file(usb / 'idVendor').lower(), text_file(usb / 'idProduct').lower()) != (VID, PID):
                continue
            # Refuse external or unknown ports rather than guessing from VID/PID.
            if text_file(usb / 'removable') != 'fixed':
                continue
            serial = text_file(usb / 'serial') if (usb / 'serial').is_file() else ''
            machine = text_file(machine_id_path)
            if not machine:
                raise CalibrationError('Cannot bind the backup to this Steam Deck: machine identity is missing.')
            identity = hashlib.sha256(f'{machine}|{board}|{VID}:{PID}|{serial}'.encode()).hexdigest()
            candidates.append((Path('/dev') / entry.name, identity))
        if len(candidates) != 1:
            raise CalibrationError('No unique built-in controller on a fixed USB port was found. External controllers are ignored.')
        return candidates[0]
    except OSError as exc:
        raise CalibrationError('Unable to identify the built-in Steam Deck controller from sysfs.') from exc


def request(side):
    if type(side) is not int or side not in (0, 1):
        raise CalibrationError('Invalid trigger side.')
    return bytearray(bytes((0, GET_CAL, 1, side)).ljust(REPORT_SIZE, b'\0'))


def parse_report(report, side):
    if len(report) != REPORT_SIZE or report[0] != 0 or report[1] != GET_CAL or report[2] != 6:
        raise CalibrationError('Unexpected calibration reply. Another client may own the feature channel; writes remain disabled.')
    actual_side, maximum, minimum, negative = struct.unpack('<BHHB', bytes(report[3:9]))
    if actual_side != side:
        raise CalibrationError('Controller reply is for the wrong trigger. Retry with other calibration tools stopped.')
    return maximum, minimum, negative


def read_fd(fd, ioctl):
    results = []
    for side in (0, 1):
        data = request(side)
        sent = ioctl(fd, 0xC0000000 | (REPORT_SIZE << 16) | (ord('H') << 8) | 6, data, True)
        if sent != REPORT_SIZE:
            raise CalibrationError('Incomplete calibration query.')
        reply = bytearray(REPORT_SIZE)
        received = ioctl(fd, 0xC0000000 | (REPORT_SIZE << 16) | (ord('H') << 8) | 7, reply, True)
        if not 9 <= received <= REPORT_SIZE:
            raise CalibrationError('Incomplete calibration response.')
        results.append(parse_report(reply, side))
    left, right = results
    return Values(left[0], left[1], right[0], right[1], left[2], right[2])


def read_native():
    if sys.platform != 'linux':
        raise CalibrationError('The native reader requires SteamOS/Linux.')
    import fcntl
    device, identity = discover()
    try:
        fd = os.open(device, os.O_RDWR | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0))
        try:
            # No mappings/settings/calibration-reset operations precede these queries.
            values = stable_read(fd,fcntl.ioctl)
            return {'values': values.as_dict(), 'identity': identity,
                    'detection':{'board':text_file(Path('/sys/class/dmi/id/board_name')),
                                 'usb':'28de:1205','interface':2,'port':'fixed'}}
        finally:
            os.close(fd)
    except PermissionError as exc:
        raise CalibrationError('Permission denied opening the built-in controller. Check Decky backend privileges.') from exc
    except OSError as exc:
        code = errno.errorcode.get(exc.errno, 'UNKNOWN')
        raise CalibrationError(f'Controller access failed ({code}); no calibration values were written.') from exc


def stable_read(fd, ioctl):
    samples = [read_fd(fd,ioctl) for _ in range(3)]
    if samples[1:] != samples[:-1]:
        raise CalibrationError('Calibration changed during reading. No stable complete snapshot is available.')
    return samples[0]


def write_fd(fd, ioctl, values, checkpoint=None):
    for side,maximum,minimum,negative in ((0,values.lmax,values.lmin,values.lneg),
                                        (1,values.rmax,values.rmin,values.rneg)):
        if checkpoint:
            checkpoint()
        packet = bytearray((bytes((0,SET_CAL,6))+struct.pack('<BHHB',side,maximum,minimum,negative)).ljust(REPORT_SIZE,b'\0'))
        sent = ioctl(fd,0xC0000000 | (REPORT_SIZE<<16) | (ord('H')<<8) | 6,packet,True)
        if sent != REPORT_SIZE:
            raise CalibrationError('A trigger calibration write was incomplete.')


def transaction(store, fd, ioctl, identity):
    record = store.load('manual-operation.json')
    if not record or record.get('status') != 'prepared' or record.get('identity') != identity:
        raise CalibrationError('The prepared Apply journal does not match this controller.')
    target = Values.from_dict(record['target'])
    initial = store.load('initial.json')
    if not initial or initial.get('identity') != identity:
        raise CalibrationError('The protected initial backup is unavailable; no write will be attempted.')
    Values.from_dict(initial['values'])
    previous = None
    touched = False
    try:
        mode = record.get('mode')
        try:
            previous = stable_read(fd,ioctl)
        except InvalidCalibration:
            if mode != 'restore':
                raise
            # A valid bound backup can repair invalid current MAX/MIN ordering.
            # Transport/protocol failures still abort; never invent a rollback.
        if mode == 'apply':
            if previous != Values.from_dict(record.get('expected')):
                raise CalibrationError('Calibration changed before the independent worker started. Read Calibration again.')
            profiles = store.load('profiles.json')
            lkg = profiles.get('lkg') if profiles else None
            confirmed_target = bool(lkg and lkg.get('confirmed') is True and
                                    lkg.get('identity') == identity and lkg.get('values') == target.as_dict())
            if not confirmed_target:
                check_manual_proposal(previous,target)
            elif (previous.lmin,previous.rmin,previous.lneg,previous.rneg) != (target.lmin,target.rmin,target.lneg,target.rneg):
                raise CalibrationError('Confirmed MAX preset has incompatible MIN or negative-range fields.')
        elif mode == 'restore':
            initial = store.load('initial.json')
            profiles = store.load('profiles.json')
            lkg = profiles.get('lkg') if profiles else None
            allowed = [initial] + ([lkg] if lkg and lkg.get('confirmed') is True else [])
            if not any(row and row.get('identity')==identity and row.get('values')==target.as_dict() for row in allowed):
                raise CalibrationError('Recovery target is not the protected initial or confirmed Last Known Good profile.')
        else:
            raise CalibrationError('Unsupported hardware transaction.')
        record.update(status='running',previous=previous.as_dict() if previous else None)
        store.save('manual-operation.json',record)
        touched = True  # The first ioctl can fail after device-side effects.
        def checkpoint():
            latest = store.load('manual-operation.json')
            if not latest or latest.get('operation_id') != record.get('operation_id'):
                raise CalibrationError('The hardware-operation journal changed unexpectedly.')
            if latest.get('cancel_requested') is True:
                raise CalibrationError('Apply was cancelled for recovery.')
        write_fd(fd,ioctl,target,checkpoint)
        checkpoint()
        actual = stable_read(fd,ioctl)
        if actual != target:
            raise CalibrationError('Hardware read-back did not match both requested trigger calibrations.')
        record.update(status='verified',readback=actual.as_dict(),finished_at=time.time())
        store.save('manual-operation.json',record)
        return {'values':actual.as_dict(),'identity':identity}
    except BaseException as exc:
        if isinstance(exc,(SystemExit,KeyboardInterrupt)):
            message = 'Apply was interrupted.'
        else:
            message = public_error(exc)
        record.update(status='failed',error=message,finished_at=time.time())
        if touched and previous is not None:
            try:
                write_fd(fd,ioctl,previous)
                if stable_read(fd,ioctl) != previous:
                    raise CalibrationError('Rollback read-back mismatch.')
                record.update(rollback='verified',readback=previous.as_dict(),error=message+' Previous calibration was restored and verified.')
            except BaseException:
                record.update(status='rollback_failed',rollback='failed',
                              error=message+' Rollback could not be verified. Read Calibration and use Restore Default; preserve diagnostics.')
        elif touched:
            record.update(status='rollback_failed',rollback='unavailable',
                          error=message+' Previous calibration was invalid. Retry Restore Default.')
        else:
            record['rollback'] = 'not_needed'
        record['finished_at']=time.time()
        store.save('manual-operation.json',record)
        raise CalibrationError(record['error']) from exc


def apply_native(root, descriptors):
    import fcntl
    import signal
    import stat
    from storage import Store
    root = Path(root)
    if not root.is_absolute():
        raise CalibrationError('The operation requires an absolute protected state directory.')
    expected_locks = [Path('/run/lock/decky-trigger-calibration.lock'),root/'.writer.lock']
    if len(descriptors) != len(expected_locks):
        raise CalibrationError('Inherited controller locks are missing.')
    for inherited,path in zip(descriptors,expected_locks):
        actual,expected = os.fstat(inherited),path.stat()
        if not stat.S_ISREG(actual.st_mode) or (actual.st_dev,actual.st_ino)!=(expected.st_dev,expected.st_ino):
            raise CalibrationError('Inherited controller lock does not match the protected operation.')
        fcntl.flock(inherited,fcntl.LOCK_EX | fcntl.LOCK_NB)
    def interrupted(*_):
        raise CalibrationError('The independent worker received a termination request.')
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    store = Store(root)
    device,identity = discover()
    fd = os.open(device,os.O_RDWR | os.O_CLOEXEC | getattr(os,'O_NOFOLLOW',0))
    try:
        return transaction(store,fd,fcntl.ioctl,identity)
    finally:
        os.close(fd)


def stream_native():
    from live_input import parse_input
    import select
    device,_ = discover()
    fd = os.open(device,os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | getattr(os,'O_NOFOLLOW',0))
    last_valid = time.monotonic()
    try:
        while True:
            readable,_,_ = select.select([fd],[],[],.25)
            if readable:
                data = os.read(fd,64)
                if not data:
                    raise CalibrationError('Controller input stream disconnected.')
                sample = parse_input(data)
                if sample is not None:
                    last_valid = time.monotonic()
                    print(json.dumps(sample,separators=(',',':')),flush=True)
            if time.monotonic()-last_valid > 3:
                raise CalibrationError('No supported Steam Deck input reports were received for 3 seconds.')
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--read']:
            result = read_native()
        elif sys.argv[1:] == ['--identify']:
            _,identity = discover()
            result = {'identity':identity}
        elif sys.argv[1:] == ['--input']:
            stream_native()
            result = None
        elif len(sys.argv)==6 and sys.argv[1]=='--apply' and sys.argv[2]=='--state-dir' and sys.argv[4]=='--lock-fds':
            result = apply_native(sys.argv[3],[int(item) for item in sys.argv[5].split(',')])
        else:
            raise CalibrationError('Unsupported native operation.')
        if result is not None:
            print(json.dumps(result),flush=True)
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
