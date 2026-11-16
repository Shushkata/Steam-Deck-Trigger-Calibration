"""Native calibration adapter with an explicitly confirmed hardware-test path."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from trigger_engine import CalibrationError, Values

WRITE_LIMITATION = ('Hardware-validation build: Apply requires explicit per-operation confirmation. '
                    'No 120-second automatic rollback is promised. No Boot/Resume profiles are applied.')


def runtime_command():
    if sys.platform != 'linux':
        raise CalibrationError('Supported hardware requires SteamOS on a Steam Deck. No simulated readings are used.')
    interpreter = '/usr/bin/python3' if getattr(sys, 'frozen', False) else sys.executable
    if getattr(sys, 'frozen', False) and not os.access(interpreter, os.X_OK):
        raise CalibrationError('SteamOS Python 3 is missing at /usr/bin/python3. The native helper is unavailable.')
    environment = dict(os.environ)
    for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONINSPECT', 'PYTHONSTARTUP'):
        environment.pop(name, None)
    environment['PYTHONNOUSERSITE'] = '1'
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    if getattr(sys, 'frozen', False):
        original = environment.pop('LD_LIBRARY_PATH_ORIG', None)
        environment.pop('LD_LIBRARY_PATH', None)
        if original is not None:
            environment['LD_LIBRARY_PATH'] = original
    return [interpreter,'-I',str(Path(__file__).with_name('hardware_worker.py'))],environment


class Hardware:
    can_write = False
    independent_rollback = False
    automatic_events = False
    limitation = WRITE_LIMITATION
    manual_apply_supported = True

    def __init__(self):
        self.store = None
        self.active_process = None
        self.operation_mode = 'apply'

    def configure(self, store):
        self.store = store

    def cancel_pending(self):
        if not self.active_process or self.active_process.poll() is not None:
            return
        record = self.store.load('manual-operation.json')
        if record:
            record['cancel_requested'] = True
            self.store.save('manual-operation.json',record)
        try:
            self.active_process.communicate(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise CalibrationError('Recovery cancellation was requested. The independent worker is still finishing or rolling back; wait and retry Restore Default.') from exc

    @contextmanager
    def authorized_operation(self, mode):
        if mode not in ('apply','restore'):
            raise CalibrationError('Unsupported hardware operation.')
        if self.active_process and self.active_process.poll() is None:
            raise CalibrationError('An independent Apply worker is still running. Wait and read calibration before another operation.')
        self.operation_mode = mode
        self.can_write = True
        try:
            yield
        finally:
            self.can_write = False

    def read(self):
        try:
            # Decky may be a frozen PyInstaller application: sys.executable is
            # then PluginLoader itself, not a Python CLI. Never spawn a second
            # loader as a supposed helper interpreter.
            command,environment = runtime_command()
            result = subprocess.run(
                [*command, '--read'],
                capture_output=True, text=True, timeout=8, check=False, env=environment)
            if result.returncode != 0:
                try:
                    message = json.loads(result.stdout)['error']
                except (ValueError, KeyError):
                    message = 'The hardware reader failed. See the plugin log.'
                raise CalibrationError(message)
            data = json.loads(result.stdout)
            values = Values.from_dict(data['values'])
            if not isinstance(data['identity'], str) or len(data['identity']) != 64:
                raise ValueError('Invalid hardware identity')
            self.last_read = (values,data['identity'])
            self.detection = data.get('detection',{'status':'identified; model detail unavailable'})
            return values, data['identity']
        except subprocess.TimeoutExpired as exc:
            raise CalibrationError('Controller read timed out. No calibration values were written. Retry after initialization.') from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CalibrationError(f'Cannot read controller calibration ({type(exc).__name__}). Check the plugin log.') from exc

    def identify(self):
        """Resolve the same supported built-in device for backup-only recovery."""
        command,environment = runtime_command()
        try:
            result = subprocess.run([*command,'--identify'],capture_output=True,text=True,timeout=8,check=False,env=environment)
            data = json.loads(result.stdout)
            if result.returncode or type(data.get('identity')) is not str or len(data['identity']) != 64:
                raise CalibrationError(data.get('error','Cannot identify the built-in controller for recovery.'))
            return data['identity']
        except (OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as exc:
            raise CalibrationError('Controller identification failed. Restore was not attempted.') from exc

    def write(self, values, identity):
        if not self.can_write or self.store is None or not self.store.lease:
            raise CalibrationError('Explicit hardware-validation confirmation and the controller lock are required.')
        Values.from_dict(values.as_dict())
        command,environment = runtime_command()
        try:
            old = self.store.load('manual-operation.json')
        except CalibrationError:
            if self.operation_mode != 'restore':
                raise
            # Only an explicit Restore may supersede a damaged operation log.
            # No active worker can hold the inherited writer locks here.
            self.store.preserve('manual-operation.json')
            old = None
        if old is not None and type(old) is not dict:
            if self.operation_mode != 'restore':
                raise CalibrationError('manual-operation.json: invalid journal. Use Restore Default.')
            self.store.preserve('manual-operation.json')
            old = None
        if old and old.get('status') in ('prepared','running','rollback_failed') and self.operation_mode != 'restore':
            raise CalibrationError('A previous operation needs recovery. Read Calibration and use Restore Default.')
        if self.active_process:
            if self.active_process.poll() is None:
                raise CalibrationError('The independent Apply worker is still running.')
            self.active_process.communicate()
            self.active_process = None
        record = {'operation_id':uuid.uuid4().hex,'identity':identity,'target':values.as_dict(),
                  'mode':self.operation_mode,'status':'prepared','started_at':time.time()}
        if self.operation_mode == 'apply':
            if not hasattr(self,'last_read') or self.last_read[1] != identity:
                raise CalibrationError('Read Calibration before applying.')
            record['expected'] = self.last_read[0].as_dict()
        self.store.save('manual-operation.json',record)
        # Inherit the SAME locked open-file descriptions. If Decky's backend
        # disappears, this one-shot worker retains the locks until it finishes.
        try:
            self.active_process = subprocess.Popen(
                [*command,'--apply','--state-dir',str(self.store.root),'--lock-fds',
                 ','.join(map(str,self.store.lease))],env=environment,
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                text=True,start_new_session=True,pass_fds=tuple(self.store.lease))
        except OSError:
            record.update(status='failed',error='The independent worker could not be started; no write was attempted.')
            self.store.save('manual-operation.json',record)
            raise
        try:
            stdout,_ = self.active_process.communicate(timeout=45)
        except subprocess.TimeoutExpired as exc:
            # Killing a writer here could interrupt its rollback. Its locks and
            # durable journal keep new writers out while it completes.
            raise CalibrationError('The independent Apply worker is still completing. Do not repeat Apply; wait, then Read Calibration. No automatic rollback deadline is promised.') from exc
        result = self.store.load('manual-operation.json')
        if not result or result.get('status') != 'verified' or self.active_process.returncode != 0:
            detail = result.get('error','Independent Apply failed.') if result else 'Apply journal is missing.'
            raise CalibrationError(detail)
        try:
            Values.from_dict(json.loads(stdout)['values'])
        except (ValueError,KeyError,TypeError) as exc:
            raise CalibrationError('Apply completed but its response was invalid. Read Calibration before continuing.') from exc
