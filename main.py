"""Decky v1 backend. All privileged operations stay in Python."""
import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import decky
from manual_service import ManualService
from conflicts import Scanner
from hardware import Hardware, runtime_command
from live_input import InputMonitor
from diagnostics import payload, export_archive, VERSION
from storage import Store
from trigger_engine import CalibrationError, public_error


class Plugin:
    async def _main(self):
        self.service = None
        self.startup_error = None
        self.started = asyncio.Event()
        try:
            # Current Decky uses DECKY_PLUGIN_SETTINGS_DIR. v0.1.0 used a
            # non-current DECKY_SETTINGS_DIR symbol, which failed at startup.
            root = getattr(decky, 'DECKY_PLUGIN_SETTINGS_DIR', '')
            if not root:
                raise RuntimeError('Decky did not provide its plugin settings directory. Update Decky Loader.')
            if Path(root).resolve().is_relative_to(Path(__file__).resolve().parent):
                raise CalibrationError('Original Calibration requires persistent settings outside the plugin directory.')
            # Known earlier folder spellings only. Read and preserve legacy
            # snapshots; never modify or delete an older installation's data.
            settings_root = Path(root).parent
            legacy_roots = [settings_root / name for name in (
                'decky-trigger-calibration', 'Decky_Trigger_Calibration',
                'DeckyTriggerCalibration', 'Decky_Trigger_Calibration_v0.1.0_SOURCE')]
            global_lock = Path('/run/lock/decky-trigger-calibration.lock') if sys.platform == 'linux' else None
            store = Store(root,legacy_roots,global_lock)
            adapter = Hardware()
            if hasattr(adapter,'configure'):
                adapter.configure(store)
            self.service = ManualService(adapter,store,Scanner())
            self.input_monitor = InputMonitor(runtime_command)
            await self.service.initialize()
        except Exception as exc:
            self.startup_error = public_error(exc)
            decky.logger.exception('Trigger Calibration initialization failed')
        finally:
            self.started.set()

    async def _call(self, operation, *args):
        # RPC can arrive while _main is performing its first bounded HID read.
        for _ in range(100):
            if hasattr(self, 'started'):
                break
            await asyncio.sleep(.05)
        if not hasattr(self, 'started'):
            return {'ok': False, 'error': 'Backend initialization has not started.'}
        await self.started.wait()
        if self.startup_error or self.service is None:
            return {'ok': False, 'error': self.startup_error or 'Backend unavailable.'}
        try:
            return await operation(*args)
        except Exception as exc:
            decky.logger.warning('Calibration operation refused or failed: %s', exc)
            self.service.event('backend_error',public_error(exc))
            result = self.service.status()
            result.update(ok=False, error=public_error(exc))
            return result

    async def get_status(self):
        async def state():
            async with self.service.lock:
                return self.service.status()
        return await self._call(state)

    async def read_calibration(self):
        if hasattr(self,'started'):
            await self.started.wait()
        # An inherited lock can outlive a crashed parent while its independent
        # worker finishes. Read Calibration must be able to retry initialization
        # afterward, without requiring a Steam restart or a terminal.
        if getattr(self,'startup_error',None) and getattr(self,'service',None):
            try:
                result = await self.service.read()
                self.startup_error = None
                return result
            except Exception as exc:
                self.startup_error = public_error(exc)
                return {'ok':False,'error':self.startup_error}
        async def read():
            return await self.service.read()
        return await self._call(read)

    async def select_values(self,lmax:int,rmax:int):
        async def select():
            return await self.service.select_values(lmax,rmax)
        return await self._call(select)

    async def set_current_as_default(self,confirmed:bool):
        async def establish():
            return await self.service.set_current_as_default(confirmed)
        return await self._call(establish)

    async def apply_selected(self,expected:dict,lmax:int,rmax:int,confirmed:bool):
        async def apply():
            return await self.service.apply_selected(expected,lmax,rmax,confirmed)
        return await self._call(apply)

    async def confirm_working(self,expected:dict,confirmed:bool):
        async def confirm():
            return await self.service.confirm_working(expected,confirmed)
        return await self._call(confirm)

    async def restore_default(self, confirmed: bool):
        async def restore():
            return await self.service.restore(confirmed)
        return await self._call(restore)

    async def restore_last_good(self, confirmed: bool):
        async def restore():
            return await self.service.restore(confirmed, last_good=True)
        return await self._call(restore)

    async def start_input(self):
        async def start():
            return await self.input_monitor.start()
        return await self._call(start)

    async def input_state(self,token:str):
        async def state():
            return self.input_monitor.state(token)
        return await self._call(state)

    async def stop_input(self,token:str):
        async def stop():
            return await self.input_monitor.stop(token)
        return await self._call(stop)

    async def test_idle_input(self,token:str):
        async def idle():
            return self.input_monitor.idle(token)
        return await self._call(idle)

    async def reset_input_test(self,token:str):
        async def reset():
            return self.input_monitor.reset(token)
        return await self._call(reset)

    async def export_diagnostics(self):
        if hasattr(self,'started'):
            await self.started.wait()
        if getattr(self,'startup_error',None) or getattr(self,'service',None) is None:
            document = {'plugin_version':VERSION,'decky_version':getattr(decky,'DECKY_VERSION','Unavailable'),
                        'device_detection':'Unavailable: backend startup failed',
                        'calibration_read_status':{'ok':False,'error':getattr(self,'startup_error','Backend not initialized')},
                        'initial_calibration':None,'current_calibration':None,'selected_values':None,'apply_result':None,
                        'trigger_test':{'available':False},'dependency_errors':[getattr(self,'startup_error','Backend not initialized')],
                        'conflicting_services':None,'backend_error_log':[]}
            try:
                home = getattr(decky,'DECKY_USER_HOME','')
                if not home:
                    raise CalibrationError('Decky did not provide a diagnostics export location.')
                return await asyncio.to_thread(export_archive,document,Path(home)/'Downloads/TriggerCalibrationDiagnostics',getattr(decky,'DECKY_USER',''))
            except Exception as exc:
                return {'ok':False,'error':public_error(exc)}
        async def export():
            async with self.service.lock:
                document = payload(self.service,self.input_monitor,getattr(decky,'DECKY_VERSION',''))
            user_home = getattr(decky,'DECKY_USER_HOME','')
            user = getattr(decky,'DECKY_USER','')
            if not user_home:
                raise CalibrationError('Decky did not provide the user Downloads location.')
            return await self.service._io(export_archive,document,Path(user_home)/'Downloads/TriggerCalibrationDiagnostics',user)
        return await self._call(export)

    async def export_original_backup(self):
        async def export():
            async with self.service.lock:
                home = getattr(decky,'DECKY_USER_HOME','')
                if not home:
                    raise CalibrationError('Decky did not provide the user Downloads location.')
                return await self.service._io(self.service.store.export_original,
                    Path(home)/'Downloads/TriggerCalibrationBackups',getattr(decky,'DECKY_USER',''))
        return await self._call(export)

    async def _unload(self):
        if getattr(self,'input_monitor',None):
            await self.input_monitor.stop()
        if getattr(self, 'service', None):
            try:
                await self.service.close()
            except Exception:
                decky.logger.exception('Unload recovery failed; rollback journal retained')

    async def _uninstall(self):
        # This release creates no system service or external executable. Keep
        # protected settings/backups for recovery. Decky's removal UI may offer
        # an explicit option to delete settings; the README warns about this.
        pass
