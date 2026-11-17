"""Offline regression tests: no Steam changes, cloud access, or real downloads."""
import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'py_modules'))
sys.modules['decky'] = types.SimpleNamespace(logger=logging.getLogger('test'))
spec = importlib.util.spec_from_file_location('plugin_under_test', ROOT / 'main.py')
plugin = importlib.util.module_from_spec(spec)
# FFI is checked separately in a subprocess, not initialized during mocked tests.
with patch('ctypes.CDLL', side_effect=OSError('test isolation')):
    spec.loader.exec_module(plugin)
import installed_games


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_defaults_without_saved_file(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOME': temp}), patch.object(plugin, '_state_dir', return_value=temp):
            result = await self.p.get_settings()
            self.assertEqual(result['status'], 'success')
            settings = result['data']
            self.assertEqual(settings['install_path'], str(Path(temp) / 'FreedeckGames'))
            self.assertEqual(settings['download_path'], str(Path(temp) / 'FreedeckGames/freedeckInstall'))
            self.assertTrue(settings['show_playtime_widget'])
            self.assertTrue(settings['emulator_crt_enabled'])
            self.assertEqual(self.p._load_settings_dict(), settings)
            self.assertEqual(self.p._install_base(), settings['install_path'])

    async def test_saved_settings_are_merged_and_legacy_paths_migrate(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOME': temp}), patch.object(plugin, '_state_dir', return_value=temp):
            path = Path(temp) / 'settings.json'
            saved = {'install_path': temp, 'download_path': str(Path(temp) / 'Downloads'), 'show_playtime_widget': False, 'future_setting': 'preserve'}
            path.write_text(json.dumps(saved))
            merged = self.p._load_settings_dict()
            self.assertEqual(merged['install_path'], temp)
            self.assertEqual(merged['download_path'], str(Path(temp) / 'Downloads'))
            self.assertFalse(merged['show_playtime_widget'])
            self.assertEqual(merged['future_setting'], 'preserve')
            self.assertIn('cloud_save_auto_upload', merged)
            result = await self.p.get_settings()
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['data']['install_path'], str(Path(temp) / 'FreedeckGames'))
            self.assertEqual(json.loads(path.read_text()), result['data'])
            path.write_text('{broken')
            result = await self.p.get_settings()
            self.assertEqual(result['status'], 'success')
            self.assertEqual(self.p._load_settings_dict(), result['data'])
            self.assertEqual(path.read_text(), '{broken')

    async def test_settings_roundtrip_preserves_custom_paths_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOME': temp}), patch.object(plugin, '_state_dir', return_value=temp):
            path = Path(temp) / 'settings.json'
            saved = {'install_path': str(Path(temp) / 'custom-games'), 'download_path': str(Path(temp) / 'downloads'), 'show_playtime_widget': False, 'future_setting': 'preserve'}
            path.write_text(json.dumps(saved))
            result = await self.p.get_settings()
            self.assertEqual(result['status'], 'success')
            for key, value in saved.items():
                self.assertEqual(result['data'][key], value)
            self.assertIn('cloud_save_auto_upload', result['data'])
            result = await self.p.set_settings({'emulator_crt_enabled': False})
            self.assertEqual(result['status'], 'success', result)
            self.assertFalse(result['data']['emulator_crt_enabled'])
            for key, value in saved.items():
                self.assertEqual(result['data'][key], value)
            self.assertEqual((await self.p.get_settings())['data'], result['data'])
            self.assertEqual(json.loads(path.read_text()), result['data'])

    async def test_real_library_dashboard_reads_default_and_saved_settings(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOME': temp}), patch.object(plugin, '_state_dir', return_value=temp), patch.object(installed_games, 'is_migrated', return_value=True), patch.object(installed_games, 'load_installed_games', return_value=[]):
            result = await self.p.library_dashboard()
            self.assertEqual(result['status'], 'success', result)
            self.assertEqual(result['data']['games'], [])
            self.assertTrue(result['data']['show_playtime'])
            (Path(temp) / 'settings.json').write_text(json.dumps({'show_playtime_widget': False}))
            result = await self.p.library_dashboard()
            self.assertEqual(result['status'], 'success', result)
            self.assertFalse(result['data']['show_playtime'])

    async def test_missing_cover_key_skips_network(self):
        with patch.object(plugin, 'SGDB_API_KEY', ''), patch('aiohttp.ClientSession') as session:
            self.assertIsNone(await plugin.Plugin()._sgdb_search('Example'))
            session.assert_not_called()

    async def test_missing_baidu_app_key_returns_configuration_error(self):
        with patch('cloud.baidu.openapi.FREEDECK_BAIDU_APP_KEY', ''):
            result = await plugin.Plugin().baidu_get_auth_url()
        self.assertEqual(result['status'], 'error')
        self.assertIn('App Key', result['message'])

    def setUp(self):
        self.p = plugin.Plugin()
        self.p._enrich_cover = lambda items: items
        self.p._catalog_loaded = True
        self.p._catalog_items = []
        self.rpc = types.SimpleNamespace(tell_status=AsyncMock(), pause=AsyncMock(), resume=AsyncMock(), remove=AsyncMock())
        self.p._engine = types.SimpleNamespace(ready=True, rpc=self.rpc)
        self.task = {'task_id': 'test', 'gids': ['a', 'b'], 'game_id': 'game', 'status': 'downloading'}
        self.p._downloads = {'test': self.task}

    async def test_emulator_direct_download_deploys_only_its_own_files(self):
        from emulator_manager import EmulatorManager
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); downloads = root / 'downloads'; downloads.mkdir()
            binary = downloads / 'mGBA.AppImage'; binary.write_bytes(b'\x7fELF')
            other = downloads / 'unrelated.zip'; other.write_bytes(b'keep me')
            manager = EmulatorManager(root / 'games', root / 'state')
            self.p._get_dl_dir = lambda: str(downloads)
            self.p._emulator_manager = lambda: manager
            task = {'game_id': 'gba_emulator', 'extract_output': str(downloads), 'file_path': str(binary), 'file_names': [binary.name]}
            with patch('emulator_deploy.mark_deployed'):
                await self.p._auto_import_after_extract('emu', task)
            self.assertEqual(task['import_status'], 'complete', task)
            self.assertTrue(Path(task['steam_exe']).is_file())
            self.assertEqual(other.read_bytes(), b'keep me'); self.assertTrue(binary.exists())
            self.assertFalse((root / 'games/Emulators/gba/unrelated.zip').exists())

    async def test_running_game_uninstall_rejected_before_deleting(self):
        with patch.object(self.p, '_library_game_running', new_callable=AsyncMock, return_value=True), patch('installed_games.handle_uninstall_game', new_callable=AsyncMock) as remove:
            result = await self.p.uninstall_game({'game_id': 'running'})
            self.assertEqual(result['status'], 'error'); remove.assert_not_called()

    async def test_filter_before_pagination_and_real_facets(self):
        self.p._catalog_items = [dict(game_id=str(i), title=f'Unique title {chr(65+i//26)}{chr(65+i%26)}', platform='pc', genre='动作冒险' if i < 70 else '角色扮演', server='t') for i in range(125)]
        result = await self.p.list_catalog({'genre': '角色扮演', 'source': 't', 'page_size': 50})
        data = result['data']
        self.assertEqual(data['total'], 55)
        self.assertEqual(len(data['items']), 50)
        self.assertTrue(data['has_more'])
        self.assertEqual(data['items'][0]['game_id'], '70')
        self.assertEqual(data['sources'], ['t'])
        result = await self.p.list_catalog({'genre': '角色扮演', 'page': 2, 'page_size': 50})
        self.assertEqual(len(result['data']['items']), 5)
        self.assertFalse(result['data']['has_more'])

    async def test_failed_volume_never_extracts(self):
        self.rpc.tell_status.side_effect = [{'status': 'complete', 'completedLength': '10', 'totalLength': '10'}, {'status': 'error', 'errorMessage': 'network error'}]
        with patch.object(self.p, '_delayed_extract', new_callable=AsyncMock) as extract:
            result = await self.p.download_poll({'task_id': 'test'})
            self.assertEqual(result['data']['status'], 'failed')
            self.assertIn('network error', result['data']['error'])
            extract.assert_not_called()

    async def test_completed_poll_schedules_extraction_only_once(self):
        self.rpc.tell_status.return_value = {'status': 'complete'}
        event = asyncio.Event()
        async def extract(*args): await event.wait()
        with patch.object(self.p, '_delayed_extract', side_effect=extract) as mocked:
            await self.p.download_poll({'task_id': 'test'})
            await self.p.download_poll({'task_id': 'test'})
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(self.task['extract_status'], 'queued')
            event.set()
            await asyncio.sleep(0)

    async def test_pause_resume_cancel_all_volumes(self):
        self.rpc.tell_status.return_value = {'status': 'active'}
        self.assertEqual((await self.p.download_pause({'task_id': 'test'}))['status'], 'success')
        self.assertEqual(self.rpc.pause.await_count, 2)
        self.rpc.tell_status.return_value = {'status': 'paused'}
        await self.p.download_resume({'task_id': 'test'})
        self.assertEqual(self.rpc.resume.await_count, 2)
        await self.p.download_cancel({'task_id': 'test'})
        self.assertEqual(self.rpc.remove.await_count, 2)
        self.assertNotIn('test', self.p._downloads)

    async def test_control_error_preserves_task(self):
        self.rpc.tell_status.return_value = {'status': 'active'}
        self.rpc.remove.side_effect = RuntimeError('RPC unavailable')
        result = await self.p.download_cancel({'task_id': 'test'})
        self.assertEqual(result['status'], 'error')
        self.assertIn('test', self.p._downloads)
        self.assertEqual(self.rpc.remove.await_count, 2)

    async def test_cannot_cancel_active_installation(self):
        self.task['extract_status'] = 'running'
        self.assertEqual((await self.p.download_cancel({'task_id': 'test'}))['status'], 'error')
        self.rpc.remove.assert_not_called()

    async def test_import_failure_never_records_installed_game(self):
        with patch.object(installed_games, 'add_installed_game') as add:
            result = await self.p.import_applied({'task_id': 'test', 'error': 'Steam unavailable'})
            self.assertFalse(result['data']['ok'])
            self.assertEqual(self.task['import_status'], 'failed')
            add.assert_not_called()
            result = await self.p.import_applied({'task_id': 'test'})
            self.assertEqual(result['status'], 'error')
            add.assert_not_called()

    async def test_archive_payload_and_directory_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads = Path(temp) / 'downloads'; downloads.mkdir()
            archive = downloads / 'game.zip'; archive.write_bytes(b'test')
            outside = Path(temp) / 'other.zip'; outside.write_bytes(b'test')
            rom = downloads / 'game.gba'; rom.write_bytes(b'test')
            self.p._get_dl_dir = lambda: str(downloads)
            self.assertEqual((await self.p.delete_archive({'file_path': str(outside)}))['status'], 'error')
            self.assertEqual((await self.p.delete_archive({'file_path': str(rom)}))['status'], 'error')
            self.assertEqual((await self.p.delete_archive({'file_path': str(archive)}))['status'], 'success')
            self.assertFalse(archive.exists()); self.assertTrue(outside.exists()); self.assertTrue(rom.exists())

    async def test_extraction_respects_install_path_and_preserves_old_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); downloads = root / 'downloads'; downloads.mkdir()
            archive = downloads / 'game.zip'; archive.write_bytes(b'archive')
            installs = root / 'sdcard'; installs.mkdir()
            self.task.update(file_names=['game.zip'], file_path=str(archive), game_id='')
            self.p._install_base = lambda: str(installs)
            self.p._get_platform_for_game = lambda _: 'pc'
            old = installs / plugin._build_extract_dir_name(self.task, str(archive)); old.mkdir()
            save = old / 'save.dat'; save.write_text('keep me')
            process = types.SimpleNamespace(stdout=types.SimpleNamespace(read=AsyncMock(return_value=b'')), wait=AsyncMock(), returncode=0)
            binary = root / 'defaults/bin/7zz'; binary.parent.mkdir(parents=True); binary.write_bytes(b'test stub')
            with patch.object(plugin, '__file__', str(root / 'main.py')), patch.object(plugin.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
                await self.p._auto_extract('test', self.task)
            self.assertEqual(save.read_text(), 'keep me')
            self.assertEqual(Path(self.task['extract_output']).parent, installs)
            self.assertNotEqual(Path(self.task['extract_output']), old)
            self.assertEqual(self.task['extract_status'], 'complete')

    async def test_extraction_exception_becomes_visible_failure(self):
        with patch.object(self.p, '_auto_extract_impl', side_effect=PermissionError('install path denied')):
            await self.p._auto_extract('test', self.task)
        self.assertEqual(self.task['extract_status'], 'failed')
        self.assertIn('install path denied', self.task['extract_error'])

    async def test_rpc_cancel_propagates_failure(self):
        from download_engine import Aria2Rpc
        rpc = Aria2Rpc('http://unused', 'test')
        with patch.object(rpc, 'call', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):
                await rpc.remove('gid')

    async def test_missing_download_is_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            self.task.update(file_names=['missing.zip'], file_path=str(Path(temp) / 'missing.zip'))
            self.p._get_platform_for_game = lambda _: 'pc'
            await self.p._auto_extract('test', self.task)
            self.assertEqual(self.task['extract_status'], 'failed')

    async def test_uninstall_protects_shared_root_and_preserves_record_on_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); game_dir = root / 'game'; game_dir.mkdir()
            target = dict(game_id='game', install_dir=str(root))
            with patch.object(installed_games, 'load_installed_games', return_value=[target]), patch.object(installed_games, 'remove_installed_game') as remove:
                result = await installed_games.handle_uninstall_game('game', protected_dirs=[str(root)])
                self.assertEqual(result['status'], 'error'); self.assertTrue(game_dir.exists()); remove.assert_not_called()
                target['install_dir'] = str(game_dir)
                with patch('shutil.rmtree', side_effect=PermissionError('denied')):
                    result = await installed_games.handle_uninstall_game('game', protected_dirs=[str(root)])
                self.assertEqual(result['status'], 'error'); remove.assert_not_called()

    async def test_uninstall_independent_game(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); game_dir = root / 'game'; game_dir.mkdir()
            target = dict(game_id='game', install_dir=str(game_dir))
            with patch.object(installed_games, 'load_installed_games', return_value=[target]), patch.object(installed_games, 'remove_installed_game') as remove:
                result = await installed_games.handle_uninstall_game('game', protected_dirs=[str(root)])
                self.assertEqual(result['status'], 'success'); self.assertFalse(game_dir.exists()); remove.assert_called_once_with('game')

if __name__ == '__main__': unittest.main()
