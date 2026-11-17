"""Persistent stale-while-revalidate cache with bounded, deduplicated refreshes."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import time

LOG = logging.getLogger('NewFreedeck')


class CatalogCache:
    def __init__(self, directory, fetcher, on_update=None, ttl=1800, retry_after=60, clock=time.time):
        self.directory = Path(directory)
        self.fetcher = fetcher
        self.on_update = on_update
        self.ttl = ttl
        self.retry_after = retry_after
        self.clock = clock
        self.entries = {}
        self.tasks = {}
        self.locks = {}
        self.semaphore = asyncio.Semaphore(4)
        self.closed = False
        self.revision = 0

    def _path(self, key):
        if not re.fullmatch(r'[a-z0-9_-]+', key):
            raise ValueError('Invalid catalog key')
        return self.directory / (key + '.json')

    async def _load(self, key):
        path = self._path(key)
        async with self.locks.setdefault(key, asyncio.Lock()):
            if key in self.entries:
                return
            def read():
                try:
                    raw = json.loads(path.read_text(encoding='utf-8'))
                    if not isinstance(raw, dict) or not isinstance(raw.get('items'), list):
                        raise ValueError('Invalid cache')
                    return {'items': [i for i in raw['items'] if isinstance(i, dict)],
                            'updated_at': float(raw.get('updated_at', 0))}
                except (OSError, ValueError, TypeError):
                    return {'items': None, 'updated_at': 0}
            entry = await asyncio.to_thread(read)
            entry.update(attempted_at=0, error='')
            self.entries[key] = entry

    async def get(self, key, fallback=None, refresh=True):
        """Returns local data even on a cold miss; never waits on the network."""
        await self._load(key)
        if self.entries[key]['items'] is None and fallback:
            self.entries[key]['items'] = list(fallback)
        if refresh:
            self.refresh(key)
        items = self.entries[key]['items']
        return items if items is not None else list(fallback or [])

    def refresh(self, key, force=False):
        self._path(key)
        if self.closed or key in self.tasks:
            return
        entry = self.entries.get(key)
        if entry is None:
            return
        now = self.clock()
        if not force:
            if entry['updated_at'] and now - entry['updated_at'] < self.ttl:
                return
            if entry['attempted_at'] and now - entry['attempted_at'] < self.retry_after:
                return
        entry['attempted_at'] = now
        task = asyncio.create_task(self._refresh(key))
        self.tasks[key] = task
        task.add_done_callback(lambda done: self.tasks.pop(key, None))

    async def _refresh(self, key):
        entry = self.entries[key]
        try:
            async with self.semaphore:
                items = await self.fetcher(key)
            if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
                raise ValueError('目录数据格式错误')
            # A temporary empty feed must not wipe a populated cache.
            if not items and entry['items']:
                raise ValueError('来源返回空目录，保留已缓存内容')
            updated = self.clock()
            def write():
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self._path(key)
                tmp = path.with_suffix('.tmp')
                tmp.write_text(json.dumps({'updated_at': updated, 'items': items}, ensure_ascii=False), encoding='utf-8')
                os.replace(tmp, path)
            await asyncio.to_thread(write)
            entry.update(items=items, updated_at=updated, error='')
            self.revision += 1
            if self.on_update:
                self.on_update(key, items)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            entry['error'] = str(error)[:250]
            LOG.warning('catalog refresh %s: %s', key, type(error).__name__)

    def status(self, key):
        entry = self.entries.get(key, {})
        return {'refreshing': key in self.tasks, 'updated_at': entry.get('updated_at', 0),
                'error': entry.get('error', ''), 'cached': entry.get('items') is not None,
                'revision': self.revision}

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
