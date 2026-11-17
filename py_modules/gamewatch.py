"""Observe Steam game processes once per tick; shared ROM directories are not identities."""
from __future__ import annotations
import asyncio
import os
from pathlib import Path


def process_snapshot(proc_root='/proc'):
    records = []
    for proc in Path(proc_root).iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid(): continue
        try:
            args = [a.decode(errors='replace') for a in (proc / 'cmdline').read_bytes().split(b'\0') if a]
            if not args: continue
            try: exe = os.readlink(proc / 'exe')
            except OSError: exe = ''
            appids = set()
            try:
                for entry in (proc / 'environ').read_bytes().split(b'\0'):
                    key, _, value = entry.partition(b'=')
                    if key in (b'SteamAppId', b'SteamGameId', b'SteamOverlayGameId') and value.isdigit():
                        number = int(value)
                        appids.add((number >> 32 if number > 0xffffffff else number) & 0xffffffff)
            except (OSError, PermissionError): pass
            records.append({'args': args, 'exe': exe, 'appids': appids})
        except (OSError, PermissionError): continue
    return records


def game_is_running(game, processes):
    appid = int(game.get('steam_appid', 0) or 0) & 0xffffffff
    rom = str(game.get('rom_path') or '')
    exe = str(game.get('steam_exe') or '').strip('"')
    for proc in processes:
        if appid and appid in proc['appids']: return True
        if rom and rom in proc['args']: return True
        if not rom and exe and (exe == proc['exe'] or exe in proc['args']): return True
    return False


class GameWatch:
    def __init__(self, on_start=None, on_end=None, interval=10.0, on_observe=None):
        self._on_start, self._on_end = on_start, on_end
        self._interval, self._on_observe = interval, on_observe
        self._prev = {}
        self._task = None
        self._callbacks = {}

    async def start(self):
        if self._task is None: self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task = None
        if self._on_observe: self._on_observe(set())
        pending = list(self._callbacks.values())
        for task in pending: task.cancel()
        if pending: await asyncio.gather(*pending, return_exceptions=True)
        self._callbacks.clear()

    async def _loop(self):
        while True:
            try: await self._tick()
            except asyncio.CancelledError: raise
            except Exception:
                import logging
                logging.getLogger('NewFreedeck').exception('gamewatch tick failed')
            await asyncio.sleep(self._interval)

    async def _tick(self):
        from installed_games import load_installed_games
        games = load_installed_games()
        processes = await asyncio.to_thread(process_snapshot)
        current = {str(g['game_id']): g for g in games if g.get('game_id') and not str(g['game_id']).endswith('_emulator') and game_is_running(g, processes)}
        if self._on_observe: self._on_observe(set(current))
        previous, self._prev = self._prev, current
        for group, callback in ((set(current) - set(previous), self._on_start), (set(previous) - set(current), self._on_end)):
            if not callback: continue
            for gid in group:
                game = current.get(gid) or previous[gid]
                previous_task = self._callbacks.get(gid)
                async def notify(callback=callback, game=game, gid=gid, previous_task=previous_task):
                    try:
                        if previous_task: await previous_task
                        await callback(gid, int(game.get('steam_appid', 0)), str(game.get('install_dir', '')))
                    except asyncio.CancelledError: raise
                    except Exception:
                        import logging
                        logging.getLogger('NewFreedeck').exception('gamewatch lifecycle callback failed')
                    finally:
                        if self._callbacks.get(gid) is asyncio.current_task(): self._callbacks.pop(gid, None)
                self._callbacks[gid] = asyncio.create_task(notify())
        # Cloud work can be slow; never delay process observations/playtime recording.
        await asyncio.sleep(0)
