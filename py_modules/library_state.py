"""Local library preferences and observed play time. No network or Steam writes."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import threading
import time


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LibraryState:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.active = set()
        self.last_tick = None

    def read(self):
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('游戏库状态文件格式异常')
        return data

    def patch(self, game_id, favorite):
        if not isinstance(favorite, bool):
            raise ValueError('收藏状态必须为布尔值')
        with self.lock:
            data = self.read()
            data.setdefault(game_id, {})['favorite'] = favorite
            atomic_json(self.path, data)

    def observe(self, running, *, monotonic=None, wall_time=None):
        """Count observed running intervals only; suspend gaps are not play time."""
        now = time.monotonic() if monotonic is None else monotonic
        wall = time.time() if wall_time is None else wall_time
        running = set(running)
        with self.lock:
            data = self.read()
            elapsed = 0 if self.last_tick is None else max(0, now - self.last_tick)
            if elapsed > 30:
                elapsed = 0
            for gid in self.active | running:
                item = data.setdefault(gid, {})
                if gid in self.active:
                    item['playtime_seconds'] = round(float(item.get('playtime_seconds', 0)) + elapsed, 2)
                if gid in running:
                    item['last_played'] = wall
                    if gid not in self.active:
                        item['sessions'] = int(item.get('sessions', 0)) + 1
            if self.active or running:
                atomic_json(self.path, data)
            self.active = running
            self.last_tick = now

    def enrich(self, games):
        with self.lock:
            data = self.read()
            return [dict(game, **{key: data.get(str(game.get('game_id', '')), {}).get(key, default)
                                  for key, default in [('favorite', False), ('playtime_seconds', 0), ('last_played', 0), ('sessions', 0)]},
                         running=str(game.get('game_id', '')) in self.active) for game in games]
