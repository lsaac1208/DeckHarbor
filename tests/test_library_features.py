import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'py_modules'))
from library_state import LibraryState
from emulator_manager import EmulatorManager, CONFIGS
from library_features import LibraryFeatures
from gamewatch import game_is_running, GameWatch, process_snapshot
import installed_games


class TempWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = EmulatorManager(self.root / 'games', self.root / 'state')
        self.env = patch.dict(os.environ, {'DECKY_USER_HOME': str(self.root)})
        self.env.start()
    def tearDown(self):
        self.env.stop(); self.temp.cleanup()
    def executable(self, platform='ps1', name='Emulator.AppImage'):
        path = self.root / 'games' / 'Emulators' / platform / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'\x7fELF' + b'fake executable for offline tests')
        path.chmod(0o755)
        return path


class LibraryStateTests(TempWorkspace):
    def test_observed_time_idempotence_suspend_and_restart(self):
        state = LibraryState(self.root / 'history.json')
        state.patch('game', True)
        state.observe({'game'}, monotonic=0, wall_time=100)
        state.observe({'game'}, monotonic=10, wall_time=110)
        state.observe({'game'}, monotonic=10, wall_time=110)
        state.observe({'game'}, monotonic=400, wall_time=500)  # sleep gap
        state.observe(set(), monotonic=410, wall_time=510)
        saved = LibraryState(self.root / 'history.json').enrich([{'game_id': 'game'}])[0]
        self.assertEqual(saved['playtime_seconds'], 20)
        self.assertEqual(saved['sessions'], 1)
        self.assertTrue(saved['favorite'])
        self.assertFalse(saved['running'])

    def test_different_roms_in_same_directory_are_not_running_together(self):
        processes = [{'args': ['/emu', '/games/one.gba'], 'exe': '/emu', 'appids': set()}]
        self.assertTrue(game_is_running({'rom_path': '/games/one.gba', 'steam_exe': '/emu'}, processes))
        self.assertFalse(game_is_running({'rom_path': '/games/two.gba', 'steam_exe': '/emu'}, processes))
        self.assertFalse(game_is_running({'install_dir': '/games'}, processes))

    def test_signed_steam_appid_matches_environment(self):
        self.assertTrue(game_is_running({'steam_appid': -1}, [{'args': [], 'exe': '', 'appids': {0xffffffff}}]))

    def test_proc_snapshot_reads_appid_and_literal_paths(self):
        proc = self.root / 'proc' / '123'; proc.mkdir(parents=True)
        (proc / 'cmdline').write_bytes(b'/games/[A]/game\0argument\0')
        (proc / 'environ').write_bytes(b'SteamAppId=2147483666\0')
        (proc / 'exe').symlink_to('/games/[A]/game')
        rows = process_snapshot(proc.parent)
        self.assertEqual(rows[0]['appids'], {2147483666})
        self.assertTrue(game_is_running({'steam_exe': '/games/[A]/game'}, rows))

    def test_empty_library_does_not_remigrate_deleted_games(self):
        installed_games.save_installed_games([])
        self.assertTrue(installed_games.is_migrated())

    def test_shortcut_repair_preserves_install_date_and_fields(self):
        installed_games.add_installed_game({'game_id': 'x', 'title': 'title', 'rom_path': '/games/x.gba'})
        before = installed_games.load_installed_games()[0]
        installed_games.add_installed_game({'game_id': 'x', 'steam_appid': 123})
        after = installed_games.load_installed_games()[0]
        self.assertEqual(after['installed_at'], before['installed_at'])
        self.assertEqual(after['rom_path'], '/games/x.gba')
        self.assertEqual(after['title'], 'title')


class EmulatorTests(TempWorkspace):
    def test_inventory_does_not_create_empty_install_directories(self):
        row = self.manager.status('ps1')
        self.assertFalse(row['installed']); self.assertFalse((self.root / 'games').exists())

    def test_no_cross_platform_or_server_executable_fallback(self):
        self.executable('3ds', 'lime3ds-room.AppImage')
        self.assertIsNone(self.manager.executable('3ds'))
        self.executable('ps2', 'pcsx2.AppImage')
        self.assertIsNone(self.manager.executable('ps1'))
        from install_pipeline import find_emulator_appimage
        self.assertIsNone(find_emulator_appimage('ps1', str(self.root / 'games')))

    def test_nested_executable_configuration_location(self):
        exe = self.executable('ps2', 'nested/pcsx2.AppImage')
        self.assertEqual(self.manager.runtime_root('ps2'), exe.parent)
        result = self.manager.configure('ps2')
        self.assertTrue(result['ok'])
        ini = exe.parent / 'inis/PCSX2.ini'
        self.assertIn('[Pad1]', ini.read_text())
        self.assertIn('SDL = true', ini.read_text())
        self.assertIn('Cross = SDL-0/A', ini.read_text())
        self.assertTrue(all(c['ok'] for c in self.manager.status('ps2')['checks']))

    def test_config_backup_and_restore_preserve_bytes(self):
        exe = self.executable()
        cfg = exe.parent / CONFIGS['ps1'][0]; cfg.parent.mkdir(parents=True, exist_ok=True)
        original = b'[Custom]\nSomething = user setting\n'
        cfg.write_bytes(original)
        result = self.manager.configure('ps1')
        self.assertTrue(result['ok']); self.assertIn('[Pad1]', cfg.read_text())
        self.manager.restore('ps1', result['backup'])
        self.assertEqual(cfg.read_bytes(), original)
        self.assertEqual(self.manager.status('ps1')['backup_count'], 2)

    def test_restore_rejects_changed_root_and_path_traversal(self):
        self.executable()
        backup = self.manager.backup('ps1')
        outside = self.root / 'outside' / 'Other.AppImage'; outside.parent.mkdir(); outside.write_bytes(b'\x7fELF')
        self.manager.register('ps1', str(outside))
        with self.assertRaises(ValueError): self.manager.restore('ps1', backup)
        with self.assertRaises(ValueError): self.manager.restore('ps1', '../bad')

    def test_config_symlink_cannot_escape_root(self):
        exe = self.executable(); external = self.root / 'external'; external.mkdir()
        (exe.parent / 'settings.ini').symlink_to(external / 'settings.ini')
        with self.assertRaises(ValueError): self.manager.backup('ps1')

    def test_local_registration_validation_and_remove_preserve_original(self):
        exe = self.executable()
        self.manager.register('ps1', str(exe))
        self.assertTrue(self.manager.status('ps1')['external'])
        self.manager.remove('ps1'); self.assertTrue(exe.exists())
        bad = self.root / 'text.AppImage'; bad.write_text('not ELF')
        with self.assertRaises(ValueError): self.manager.register('ps1', str(bad))
        with self.assertRaises(ValueError): self.manager.status('../ps1')

    def test_remove_managed_emulator_preserves_saves_in_backup(self):
        exe = self.executable(); save = exe.parent / 'memcard.dat'; save.write_bytes(b'precious save')
        with patch('emulator_deploy.mark_user_removed', return_value=True):
            result = self.manager.remove('ps1')
        self.assertFalse(exe.exists()); self.assertEqual((Path(result['backup']) / 'memcard.dat').read_bytes(), b'precious save')

    def test_deploy_does_not_overwrite_existing_directory(self):
        source = self.root / 'download'; source.mkdir(); (source / 'Emu.AppImage').write_bytes(b'\x7fELF')
        with patch('emulator_deploy.mark_deployed'):
            row = self.manager.deploy('gba', source)
        self.assertTrue(row['installed']); self.assertFalse(source.exists())
        another = self.root / 'another'; another.mkdir(); (another / 'Emu.AppImage').write_bytes(b'\x7fELF')
        with self.assertRaises(ValueError): self.manager.deploy('gba', another)
        self.assertTrue(another.exists())

    def test_firmware_copy_backup_and_invalid_file(self):
        exe = self.executable(); source = self.root / 'scph.bin'; source.write_bytes(b'A' * 524288)
        self.manager.import_firmware('ps1', str(source))
        source.write_bytes(b'B' * 524288)
        self.manager.import_firmware('ps1', str(source))
        self.assertEqual((exe.parent / 'bios/scph.bin').read_bytes(), b'B' * 524288)
        backups = list((self.root / 'state/firmware_backups/ps1').rglob('scph.bin'))
        self.assertEqual(backups[0].read_bytes(), b'A' * 524288)
        self.assertTrue(source.exists())
        bad = self.root / 'tiny.bin'; bad.write_bytes(b'x')
        with self.assertRaises(ValueError): self.manager.import_firmware('ps1', str(bad))

    def test_all_platform_presets_and_restore_generated_files(self):
        for platform in CONFIGS:
            with self.subTest(platform=platform):
                exe = self.executable(platform)
                result = self.manager.configure(platform)
                self.assertTrue(result['ok'], result)
                self.assertTrue(all(row['ok'] for row in self.manager.status(platform)['checks']))
                self.manager.restore(platform, result['backup'])
                for rel in CONFIGS[platform]: self.assertFalse((exe.parent / rel).exists(), rel)

    def test_preset_formats_match_emulator_parsers(self):
        import configparser
        for platform in ('ps1', 'psp', 'gba', 'fc', 'md', 'nds'):
            exe = self.executable(platform)
            self.assertTrue(self.manager.configure(platform)['ok'])
            data = (exe.parent / CONFIGS[platform][0]).read_text()
            if platform == 'md':
                self.assertIn('bindings {', data); self.assertNotIn('[video]', data)
            elif platform == 'nds':
                self.assertIn('Joy_Up=257', data); self.assertNotIn('[Input]', data)
            else:
                cfg = configparser.ConfigParser(); cfg.read_string(data)
                if platform == 'ps1': self.assertEqual(cfg['Pad1']['LUp'], 'SDL-0/-LeftY')
                if platform == 'psp': self.assertEqual(cfg['Graphics']['GraphicsBackend'], '3')
                if platform == 'gba': self.assertEqual(cfg['ports.qt']['fullscreen'], '1')
                if platform == 'fc': self.assertEqual(cfg['frontend']['v_fullscreen'], '1')

    def test_rom_paths_escape_shell_expansion_and_keep_spaces(self):
        import subprocess
        from install_pipeline import build_rom_launch_args, emulator_launch_options
        rom = '/tmp/中文 $HOME `id` "title" \\ game.gba'
        args = build_rom_launch_args('gba', rom)
        # Ask bash to parse only, without executing a game or expanding filename syntax.
        result = subprocess.run(['bash', '-c', 'printf "%s" ' + args.removeprefix('-f ')], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, rom)
        options = emulator_launch_options('ps3', '/emulators/PS 3/app', '/games/game/EBOOT.BIN')
        self.assertIn("XDG_CONFIG_HOME='/emulators/PS 3/config' %command%", options)
        self.assertIn('--memstick=', emulator_launch_options('psp', '/emu/app'))
        with self.assertRaises(ValueError): build_rom_launch_args('gba', 'bad\npath')


class FeatureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'DECKY_USER_HOME': str(self.root)}); self.env.start()
        self.plugin = LibraryFeatures()
        self.plugin._install_base = lambda: str(self.root / 'games')
        self.plugin._settings_path = lambda: str(self.root / 'state/settings.json')
        self.plugin._downloads = {}
        self.exe = self.root / 'games/Emulators/gba/mGBA.AppImage'; self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b'\x7fELF'); self.exe.chmod(0o755)
        self.rom = self.root / 'My game.gba'; self.rom.write_bytes(b'ROM')
    def tearDown(self): self.env.stop(); self.temp.cleanup()

    async def test_prepare_does_not_record_game_until_steam_commit(self):
        prepared = await self.plugin.library_prepare_rom({'platform': 'gba', 'path': str(self.rom)})
        self.assertEqual(prepared['status'], 'success'); self.assertEqual(installed_games.load_installed_games(), [])
        token = prepared['data']['token']
        bad = await self.plugin.library_commit_import({'token': token, 'appid': 0})
        self.assertEqual(bad['status'], 'error')
        result = await self.plugin.library_commit_import({'token': token, 'appid': -123})
        self.assertEqual(result['status'], 'success')
        record = installed_games.load_installed_games()[0]
        self.assertEqual(record['steam_appid'], (-123 & 0xffffffff)); self.assertFalse(record['managed_files'])
        self.assertEqual(record['rom_path'], str(self.rom)); self.assertTrue(self.rom.exists())
        repeat = await self.plugin.library_prepare_rom({'platform': 'gba', 'path': str(self.rom)})
        self.assertEqual(repeat['data']['appid'], record['steam_appid'])
        self.assertEqual((await self.plugin.library_commit_import({'token': token, 'appid': 1}))['status'], 'error')

    async def test_prepare_checks_platform_file_and_emulator(self):
        for payload in ({'platform': '../bad', 'path': str(self.rom)}, {'platform': 'ps1', 'path': str(self.rom)}, {'platform': 'gba', 'path': str(self.root / 'missing')}):
            self.assertEqual((await self.plugin.library_prepare_rom(payload))['status'], 'error')
        self.exe.unlink()
        self.assertEqual((await self.plugin.library_prepare_rom({'platform': 'gba', 'path': str(self.rom)}))['status'], 'error')

    async def test_commit_rechecks_files_and_expiry(self):
        result = await self.plugin.library_prepare_rom({'platform': 'gba', 'path': str(self.rom)})
        token = result['data']['token']; self.rom.unlink()
        self.assertEqual((await self.plugin.library_commit_import({'token': token, 'appid': 123}))['status'], 'error')
        self.assertEqual(installed_games.load_installed_games(), [])
        self.plugin._library_imports[token]['created'] -= 601
        self.assertEqual((await self.plugin.library_commit_import({'token': token, 'appid': 123}))['status'], 'error')

    async def test_favorite_persistence_forget_does_not_delete_rom(self):
        result = await self.plugin.library_prepare_rom({'platform': 'gba', 'path': str(self.rom)})
        await self.plugin.library_commit_import({'token': result['data']['token'], 'appid': 123})
        gid = result['data']['game_id']
        self.assertEqual((await self.plugin.library_favorite({'game_id': gid, 'favorite': True}))['status'], 'success')
        self.assertTrue(self.plugin._library_history().read()[gid]['favorite'])
        self.assertEqual((await self.plugin.library_forget({'game_id': gid}))['status'], 'success')
        self.assertTrue(self.rom.exists()); self.assertTrue(installed_games.is_migrated())

    async def test_running_rom_cannot_be_forgotten(self):
        installed_games.add_installed_game({'game_id': 'g', 'install_dir': str(self.root)})
        self.plugin._library_history().observe({'g'})
        self.assertEqual((await self.plugin.library_forget({'game_id': 'g'}))['status'], 'error')

    async def test_gamewatch_reports_transitions_even_when_record_removed(self):
        start = AsyncMock(); end = AsyncMock(); observations = []
        watch = GameWatch(start, end, on_observe=observations.append)
        game = {'game_id': 'g', 'rom_path': str(self.rom), 'steam_appid': 123, 'install_dir': str(self.root)}
        with patch('installed_games.load_installed_games', return_value=[game]), patch('gamewatch.process_snapshot', return_value=[{'args': [str(self.rom)], 'exe': str(self.exe), 'appids': {123}}]):
            await watch._tick(); await watch._tick()
        with patch('installed_games.load_installed_games', return_value=[]), patch('gamewatch.process_snapshot', return_value=[]):
            await watch._tick()
        self.assertEqual(start.await_count, 1); self.assertEqual(end.await_count, 1)
        self.assertEqual(observations[-1], set())

    async def test_slow_cloud_callback_does_not_block_observations(self):
        blocker = asyncio.Event(); observations = []
        async def slow(*args): await blocker.wait()
        end = AsyncMock()
        watch = GameWatch(slow, end, on_observe=observations.append)
        game = {'game_id': 'g', 'rom_path': str(self.rom)}
        with patch('installed_games.load_installed_games', return_value=[game]), patch('gamewatch.process_snapshot', return_value=[{'args': [str(self.rom)], 'exe': '', 'appids': set()}]):
            await asyncio.wait_for(watch._tick(), 1)
            await asyncio.wait_for(watch._tick(), 1)
        with patch('installed_games.load_installed_games', return_value=[]), patch('gamewatch.process_snapshot', return_value=[]):
            await asyncio.wait_for(watch._tick(), 1)
        self.assertEqual(observations, [{'g'}, {'g'}, set()])
        self.assertEqual(end.await_count, 0)
        blocker.set()
        await asyncio.gather(*watch._callbacks.values())
        self.assertEqual(end.await_count, 1)
        await watch.stop()

    async def test_active_download_blocks_configuration_and_reassociation(self):
        self.plugin._downloads = {'task': {'game_id': 'gba_emulator', 'status': 'downloading'}}
        for method, payload in ((self.plugin.emulator_configure, {'platform': 'gba'}),
                                (self.plugin.emulator_register_local, {'platform': 'gba', 'path': str(self.exe)}),
                                (self.plugin.emulator_remove, {'platform': 'gba'})):
            result = await method(payload)
            self.assertEqual(result['status'], 'error'); self.assertIn('正在安装', result['message'])
        self.assertTrue(self.exe.exists())

if __name__ == '__main__': unittest.main()
