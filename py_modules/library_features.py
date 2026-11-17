"""Decky callables for the installed library and emulator workspace."""
import asyncio
import hashlib
import os
from pathlib import Path
import time
import uuid

from emulator_manager import EmulatorManager
from emulator_registry import EMULATOR_PLATFORMS
from installed_games import load_installed_games, add_installed_game, remove_installed_game
from library_state import LibraryState


def ok(data): return {'status': 'success', 'data': data}
def error(exc): return {'status': 'error', 'message': str(exc)}


class LibraryFeatures:
    def _library_history(self):
        if not hasattr(self, '_history'):
            self._history = LibraryState(Path(self._settings_path()).parent / 'library_state.json')
        return self._history

    def _emulator_manager(self):
        return EmulatorManager(self._install_base(), Path(self._settings_path()).parent)

    async def library_dashboard(self):
        try:
            result = await self.list_installed_games()
            if result.get('status') != 'success': return result
            games = self._library_history().enrich(result['data']['games'])
            return ok({'games': games, 'show_playtime': bool(self._load_settings_dict().get('show_playtime_widget', True))})
        except Exception as exc: return error(exc)

    async def library_favorite(self, payload=None):
        try:
            payload = payload or {}
            gid = str(payload.get('game_id', ''))
            if not any(str(g.get('game_id')) == gid for g in load_installed_games()): raise ValueError('游戏记录不存在')
            self._library_history().patch(gid, payload.get('favorite'))
            return ok({'ok': True})
        except Exception as exc: return error(exc)

    async def library_forget(self, payload=None):
        try:
            gid = str((payload or {}).get('game_id', ''))
            if await self._library_game_running(gid): raise ValueError('游戏运行中，请先退出')
            if not remove_installed_game(gid): raise ValueError('游戏记录不存在')
            return ok({'ok': True})
        except Exception as exc: return error(exc)

    async def library_launch_info(self, payload=None):
        try:
            gid = str((payload or {}).get('game_id', ''))
            game = next((g for g in load_installed_games() if str(g.get('game_id')) == gid), None)
            if not game: raise ValueError('游戏记录不存在')
            if game.get('rom_path'):
                if not Path(game['rom_path']).is_file(): raise ValueError('ROM 已移动或存储设备未连接，请重新导入')
                emulator = self._emulator_manager().executable(str(game.get('platform', '')))
                if not emulator: raise ValueError('请先在模拟器中心安装或关联该平台模拟器')
                if game.get('steam_exe') != emulator:
                    raise ValueError('模拟器位置已改变，请在模拟器中心重新导入此 ROM 以更新快捷方式')
            if game.get('steam_exe') and not Path(game['steam_exe']).is_file(): raise ValueError('启动程序已移动或存储设备未连接')
            return ok({'game_id': gid, 'title': game.get('title') or game.get('title_zh') or gid,
                       'appid': int(game.get('steam_appid', 0)) & 0xffffffff,
                       'executable': game.get('steam_exe', ''), 'start_dir': game.get('start_dir') or game.get('install_dir', ''),
                       'launch_options': game.get('launch_options', ''), 'compat_tool': game.get('compat_tool', '')})
        except Exception as exc: return error(exc)

    async def library_link_shortcut(self, payload=None):
        try:
            payload = payload or {}
            gid = str(payload.get('game_id', ''))
            appid = int(payload.get('appid', 0)) & 0xffffffff
            if not appid: raise ValueError('Steam 快捷方式编号无效')
            game = next((g for g in load_installed_games() if str(g.get('game_id')) == gid), None)
            if not game: raise ValueError('游戏记录不存在')
            game['steam_appid'] = appid
            add_installed_game(game)
            return ok({'ok': True})
        except Exception as exc: return error(exc)

    async def emulator_inventory(self):
        try:
            manager = self._emulator_manager()
            rows = await asyncio.to_thread(lambda: [manager.status(p) for p in EMULATOR_PLATFORMS])
            return ok({'emulators': rows})
        except Exception as exc: return error(exc)

    async def emulator_register_local(self, payload=None):
        try:
            payload = payload or {}
            platform = str(payload.get('platform', ''))
            async with self._emulator_lock(platform):
                await self._check_emulator_idle(platform)
                return ok(await asyncio.to_thread(self._emulator_manager().register, platform, str(payload.get('path', ''))))
        except Exception as exc: return error(exc)

    async def emulator_configure(self, payload=None):
        try:
            platform = str((payload or {}).get('platform', ''))
            async with self._emulator_lock(platform):
                await self._check_emulator_idle(platform)
                return ok(await asyncio.to_thread(self._emulator_manager().configure, platform))
        except Exception as exc: return error(exc)

    async def emulator_import_firmware(self, payload=None):
        try:
            payload = payload or {}; platform = str(payload.get('platform', ''))
            async with self._emulator_lock(platform):
                await self._check_emulator_idle(platform)
                return ok(await asyncio.to_thread(self._emulator_manager().import_firmware, platform, str(payload.get('path', ''))))
        except Exception as exc: return error(exc)

    async def emulator_restore_config(self, payload=None):
        try:
            payload = payload or {}; platform = str(payload.get('platform', ''))
            async with self._emulator_lock(platform):
                await self._check_emulator_idle(platform)
                return ok(await asyncio.to_thread(self._emulator_manager().restore, platform, str(payload.get('backup', ''))))
        except Exception as exc: return error(exc)

    async def _emulator_running(self, platform):
        from gamewatch import process_snapshot
        executable = self._emulator_manager().executable(platform)
        if not executable: return False
        records = await asyncio.to_thread(process_snapshot)
        return any(executable in r['args'] or r['exe'] == executable for r in records)

    def _emulator_lock(self, platform):
        self._emulator_manager().info(platform)
        if not hasattr(self, '_emulator_locks'): self._emulator_locks = {}
        return self._emulator_locks.setdefault(platform, asyncio.Lock())

    async def _check_emulator_idle(self, platform):
        if await self._emulator_running(platform): raise ValueError('请先关闭模拟器和使用它的游戏')
        if any(t.get('game_id') == platform + '_emulator' and t.get('import_status') not in ('complete', 'failed')
               and t.get('extract_status') != 'failed' and t.get('status') not in ('failed', 'cancelled')
               for t in getattr(self, '_downloads', {}).values()):
            raise ValueError('模拟器正在安装，请等待任务结束或取消下载')

    async def _library_game_running(self, gid):
        from gamewatch import process_snapshot, game_is_running
        if gid in self._library_history().active: return True
        game = next((g for g in load_installed_games() if g.get('game_id') == gid), None)
        return bool(game and game_is_running(game, await asyncio.to_thread(process_snapshot)))

    async def emulator_remove(self, payload=None):
        try:
            platform = str((payload or {}).get('platform', ''))
            async with self._emulator_lock(platform):
                await self._check_emulator_idle(platform)
                result = await asyncio.to_thread(self._emulator_manager().remove, platform)
            removed = [g for g in load_installed_games() if g.get('game_id') == platform + '_emulator']
            remove_installed_game(platform + '_emulator')
            result['appids'] = [int(g.get('steam_appid', 0)) & 0xffffffff for g in removed if g.get('steam_appid')]
            return ok(result)
        except Exception as exc: return error(exc)

    async def emulator_prepare_shortcut(self, payload=None):
        try:
            platform = str((payload or {}).get('platform', ''))
            manager = self._emulator_manager(); info = manager.info(platform)
            executable = manager.executable(platform)
            if not executable: raise ValueError('请先安装或关联模拟器')
            gid = platform + '_emulator'
            from install_pipeline import emulator_launch_options
            existing = next((g for g in load_installed_games() if g.get('game_id') == gid and g.get('steam_exe') == executable), {})
            return ok(self._prepare_library_import({'game_id': gid, 'title': info['display'] + ' 模拟器', 'platform': platform,
                'steam_exe': executable, 'start_dir': str(Path(executable).parent), 'install_dir': str(Path(executable).parent),
                'launch_options': emulator_launch_options(platform, executable), 'game_type': 'emulator', 'managed_files': False, 'steam_appid': existing.get('steam_appid', 0)}))
        except Exception as exc: return error(exc)

    def _prepare_library_import(self, game):
        if not hasattr(self, '_library_imports'): self._library_imports = {}
        now = time.monotonic()
        self._library_imports = {k: v for k, v in self._library_imports.items() if now - v['created'] < 600}
        token = uuid.uuid4().hex
        self._library_imports[token] = {'game': game, 'created': now}
        return {'token': token, 'game_id': game['game_id'], 'title': game['title'], 'appid': int(game.get('steam_appid', 0)) & 0xffffffff,
                'executable': game['steam_exe'], 'start_dir': game['start_dir'], 'launch_options': game['launch_options']}

    async def library_prepare_rom(self, payload=None):
        try:
            payload = payload or {}; platform = str(payload.get('platform', ''))
            manager = self._emulator_manager(); manager.info(platform)
            path = Path(str(payload.get('path', ''))).expanduser().resolve(strict=True)
            if not path.is_file(): raise ValueError('请选择 ROM 文件')
            from install_pipeline import ROM_EXTENSIONS, emulator_launch_options
            if platform == 'ps3':
                if path.name.upper() != 'EBOOT.BIN': raise ValueError('RPCS3 请选择已解包游戏的 EBOOT.BIN；PKG 和固件请先在模拟器中安装')
            elif path.suffix.lower() not in ROM_EXTENSIONS[platform] or path.suffix.lower() in ('.cia', '.pkg', '.nsz', '.xcz', '.32x'):
                raise ValueError('该文件不能直接启动，请先在模拟器中安装或转换为支持的 ROM 格式')
            if any(c in str(path) for c in ('"', '\n', '\r', '\x00')): raise ValueError('文件路径包含不支持的引号或换行符，请重命名')
            executable = manager.executable(platform)
            if not executable: raise ValueError('请先安装或关联该平台的模拟器')
            gid = 'local_' + platform + '_' + hashlib.sha256(str(path).encode()).hexdigest()[:20]
            existing = next((g for g in load_installed_games() if g.get('game_id') == gid and g.get('steam_exe') == executable), {})
            title = str(payload.get('title') or path.stem).strip()[:200]
            return ok(self._prepare_library_import({'game_id': gid, 'title': title, 'platform': platform, 'game_type': 'emulator_rom',
                'steam_exe': executable, 'rom_path': str(path), 'launch_options': emulator_launch_options(platform, executable, str(path)),
                'start_dir': str(Path(executable).parent), 'install_dir': str(path.parent), 'size_bytes': path.stat().st_size,
                'managed_files': False, 'steam_appid': existing.get('steam_appid', 0)}))
        except Exception as exc: return error(exc)

    async def library_commit_import(self, payload=None):
        try:
            payload = payload or {}; token = str(payload.get('token', ''))
            pending = getattr(self, '_library_imports', {}).get(token)
            if not pending or time.monotonic() - pending['created'] >= 600: raise ValueError('导入确认已过期，请重新选择文件')
            appid = int(payload.get('appid', 0)) & 0xffffffff
            if not appid: raise ValueError('Steam 快捷方式编号无效')
            game = dict(pending['game'])
            if not Path(game['steam_exe']).is_file() or (game.get('rom_path') and not Path(game['rom_path']).is_file()): raise ValueError('模拟器或 ROM 文件已移动，请重新导入')
            game['steam_appid'] = appid
            add_installed_game(game)
            self._library_imports.pop(token)
            return ok({'ok': True, 'game_id': game['game_id']})
        except Exception as exc: return error(exc)
