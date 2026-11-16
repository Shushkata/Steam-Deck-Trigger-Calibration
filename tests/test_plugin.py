import importlib.util
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,MagicMock
from simulated import SimulatedHardware, SimulatedScanner


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_decky_settings_api_and_all_user_actions(self):
        with tempfile.TemporaryDirectory() as root:
            decky = SimpleNamespace(DECKY_PLUGIN_SETTINGS_DIR=root,DECKY_USER_HOME=root,DECKY_USER='',logger=logging.getLogger('plugin-test'))
            spec = importlib.util.spec_from_file_location('plugin_under_test',Path(__file__).parents[1]/'main.py')
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules,{'decky':decky}):
                spec.loader.exec_module(module)
            adapter = SimulatedHardware()
            plugin = module.Plugin()
            with patch.object(module,'Hardware',return_value=adapter), patch.object(module,'Scanner',return_value=SimulatedScanner()):
                await plugin._main()
            try:
                state = await plugin.get_status()
                self.assertTrue(state['can_set_current_as_default'])
                self.assertFalse(state['ok'])
                self.assertFalse((await plugin.set_current_as_default(False))['ok'])
                state = await plugin.set_current_as_default(True)
                self.assertTrue(state['ok'])
                exported = await plugin.export_original_backup()
                self.assertTrue(exported['ok'])
                import json
                from simulated import INITIAL
                self.assertEqual(json.loads(Path(exported['path']).read_text())['payload']['values'],INITIAL.as_dict())
                self.assertEqual(adapter.writes,[])
                self.assertTrue((await plugin.read_calibration())['ok'])
                selected = await plugin.select_values(1834,1730)
                self.assertTrue(selected['ok'])
                self.assertEqual(adapter.writes,[])
                tested = await plugin.apply_selected(state['current'],1834,1730,True)
                self.assertTrue(tested['ok'])
                saved = await plugin.confirm_working(tested['current'],True)
                self.assertTrue(saved['ok'])
                self.assertFalse(hasattr(plugin,'set_automatic'))
                self.assertTrue((await plugin.restore_default(True))['ok'])
                self.assertTrue((await plugin.restore_last_good(True))['ok'])
                invalid = await plugin.apply_selected(saved['current'],'shell input',1730,True)
                self.assertFalse(invalid['ok'])
                self.assertIsInstance(invalid['error'],str)
                self.assertFalse(hasattr(plugin,'apply'))
            finally:
                await plugin._unload()
                await plugin._uninstall()

    async def test_install_metadata_requests_current_root_flag(self):
        import json
        metadata = json.loads((Path(__file__).parents[1]/'plugin.json').read_text())
        self.assertEqual(metadata['flags'],['root'])
        self.assertEqual(metadata['api_version'],1)

    async def test_diagnostics_can_be_exported_when_backend_initialization_fails(self):
        with tempfile.TemporaryDirectory() as root:
            decky=SimpleNamespace(DECKY_PLUGIN_SETTINGS_DIR='',DECKY_USER_HOME=root,DECKY_USER='',logger=MagicMock())
            spec=importlib.util.spec_from_file_location('failed_plugin',Path(__file__).parents[1]/'main.py')
            module=importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules,{'decky':decky}):
                spec.loader.exec_module(module)
            plugin=module.Plugin()
            await plugin._main()
            result=await plugin.export_diagnostics()
            self.assertTrue(result['ok'])
            self.assertTrue(Path(result['path']).is_file())

    async def test_read_can_retry_a_lock_held_by_a_finishing_independent_worker(self):
        from trigger_engine import CalibrationError
        with tempfile.TemporaryDirectory() as root:
            decky=SimpleNamespace(DECKY_PLUGIN_SETTINGS_DIR=root,logger=MagicMock())
            spec=importlib.util.spec_from_file_location('retry_plugin',Path(__file__).parents[1]/'main.py')
            module=importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules,{'decky':decky}):
                spec.loader.exec_module(module)
            plugin=module.Plugin()
            with patch.object(module,'Hardware',return_value=SimulatedHardware()), \
                 patch.object(module,'Scanner',return_value=SimulatedScanner()), \
                 patch.object(module.Store,'acquire',side_effect=CalibrationError('Another backend owns the lock')):
                await plugin._main()
            self.assertIsNotNone(plugin.startup_error)
            self.assertTrue((await plugin.read_calibration())['can_set_current_as_default'])
            self.assertTrue((await plugin.set_current_as_default(True))['ok'])
            self.assertIsNone(plugin.startup_error)
            await plugin._unload()
