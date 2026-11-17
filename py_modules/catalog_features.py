"""Fast local catalog views and independent public catalog sources."""
import asyncio
import inspect
import json
from pathlib import Path
import random
import time

import decky
from catalog_cache import CatalogCache
from catalog_sources import CatalogSources, PLATFORMS, SOURCE_LABELS, merge_items, normalize_items, normalize_platform


def ok(data):
    return {'status': 'success', 'data': data}


class CatalogFeatures:
    async def _ensure_catalog_ready(self):
        if getattr(self, '_catalog_ready', False):
            return
        if not hasattr(self, '_catalog_init_lock'):
            self._catalog_init_lock = asyncio.Lock()
        async with self._catalog_init_lock:
            if getattr(self, '_catalog_ready', False):
                return
            self._catalog_sources = CatalogSources(Path(__file__).resolve().parents[1] / 'defaults/community_games.json')
            seeds = await self._catalog_sources.load()
            state = Path(self._catalog_state_dir())
            self._catalog_refresh_cache = CatalogCache(state/'catalog-v2', self._catalog_sources.fetch, self._catalog_changed)
            self._catalog_grouped = {}
            self._catalog_group_tasks = {}
            self._catalog_event_tasks = set()
            def old_cache():
                try:
                    return normalize_items(json.loads((state/'catalog_cache.json').read_text(encoding='utf-8')))
                except (OSError, ValueError, TypeError):
                    return []
            old = await asyncio.to_thread(old_cache)
            self._catalog_legacy = await self._catalog_refresh_cache.get('all', fallback=old or self._catalog_items)
            self._catalog_public = {}
            for source in ('flathub', 'homebrew', 'steam'):
                self._catalog_public[source] = await self._catalog_refresh_cache.get('source-'+source, fallback=[s for s in seeds if s['source_id']==source])
            self._rebuild_catalog()
            self._catalog_ready = True

    def _rebuild_catalog(self):
        self._catalog_items = merge_items(self._catalog_legacy, *self._catalog_public.values())
        self._catalog_loaded = True
        self._catalog_grouped.clear()

    def _catalog_changed(self, key, items):
        if key == 'all':
            self._catalog_legacy = items
        elif key.startswith('source-'):
            # Source refresh can complete while initialization is still reading other caches.
            if not hasattr(self, '_catalog_public'):
                self._catalog_public = {}
            self._catalog_public[key[7:]] = items
        elif key.startswith('platform-'):
            self._platform_cache[key[9:]] = items
        if hasattr(self, '_catalog_public') and hasattr(self, '_catalog_legacy'):
            self._rebuild_catalog()
        try:
            emit = getattr(decky, 'emit', None) or getattr(decky, 'emit_event', None)
            if emit:
                result = emit('catalog_updated', key)
                if inspect.isawaitable(result):
                    task = asyncio.create_task(result)
                    self._catalog_event_tasks.add(task)
                    task.add_done_callback(self._catalog_event_tasks.discard)
        except Exception:
            pass

    async def _load_catalog_async(self):
        await self._ensure_catalog_ready()

    async def _warm_platform_cache(self):
        await self._ensure_catalog_ready()
        # Local reads only; the cache bounds and deduplicates network work.
        await asyncio.gather(*(self._catalog_refresh_cache.get('platform-'+p) for p in sorted(PLATFORMS)))

    async def _catalog_view(self, kind, platform=''):
        await self._ensure_catalog_ready()
        platform = normalize_platform(platform)
        key = kind + ('-'+platform if platform else '')
        baseline = [i for i in self._catalog_items if not platform or normalize_platform(i.get('platform')) == platform]
        items = await self._catalog_refresh_cache.get(key)
        merged = merge_items(items, baseline)
        revision = self._catalog_refresh_cache.revision
        group_key = (key, revision)
        if group_key not in self._catalog_grouped:
            if group_key not in self._catalog_group_tasks:
                self._catalog_group_tasks[group_key] = asyncio.create_task(asyncio.to_thread(self._catalog_grouper, merged))
            task = self._catalog_group_tasks[group_key]
            try:
                grouped = await asyncio.shield(task)
                self._catalog_grouped[group_key] = grouped
            finally:
                if task.done():
                    self._catalog_group_tasks.pop(group_key, None)
        return self._catalog_grouped[group_key], self._catalog_refresh_cache.status(key)

    async def get_shelf_games(self, payload=None, limit=24):
        try:
            n = max(1, min(100, int((payload or {}).get('limit', limit))))
        except (TypeError, ValueError, AttributeError):
            n = 24
        grouped, status = await self._catalog_view('all')
        # Sample only the required cards, not a shuffle/copy of the entire catalog.
        selected = random.sample(grouped, min(n, len(grouped)))
        return ok({'games':self._enrich_cover(selected), 'total':len(grouped), 'loaded':True, **status})

    async def get_platform_games(self, payload=None, platform='', page=1, page_size=50):
        try:
            data = payload if isinstance(payload, dict) else {}
            platform = normalize_platform(data.get('platform', platform))
            if platform not in PLATFORMS:
                raise ValueError('未知平台')
            page = max(1, int(data.get('page', page) or 1))
            page_size = max(1, min(200, int(data.get('page_size', data.get('pageSize', page_size)) or 50)))
            grouped, status = await self._catalog_view('platform', platform)
            start = (page-1)*page_size
            return ok({'games':self._enrich_cover(grouped[start:start+page_size]), 'total':len(grouped),
                       'page':page, 'page_size':page_size, 'has_more':start+page_size<len(grouped), **status})
        except (TypeError, ValueError) as error:
            return {'status':'error', 'message':str(error)}

    async def get_curated_shelf(self, payload=None, platform='', limit=20):
        data = payload if isinstance(payload, dict) else {}
        platform = normalize_platform(data.get('platform', platform))
        if platform not in PLATFORMS:
            return {'status':'error', 'message':'未知平台'}
        try:
            limit = max(1,min(50,int(data.get('limit',limit))))
        except (TypeError, ValueError):
            limit = 20
        grouped, status = await self._catalog_view('curated', platform)
        return ok({'games':self._enrich_cover(grouped[:limit]), 'total':len(grouped), 'platform':platform, **status})

    async def get_catalog_sources(self):
        await self._ensure_catalog_ready()
        cache = self._catalog_refresh_cache
        rows=[]
        for source, name in SOURCE_LABELS.items():
            keys = [k for k in cache.entries if k=='all' or k.startswith(('platform-','curated-'))] if source=='legacy' else ['source-'+source]
            states = [cache.status(k) for k in keys]
            items = self._catalog_legacy if source=='legacy' else self._catalog_public.get(source,[])
            rows.append({'id':source, 'name':name, 'count':len(items), 'refreshing':any(s['refreshing'] for s in states),
                         'error':'部分目录暂时不可用，继续显示本地内容' if any(s['error'] for s in states) else '',
                         'updated_at':max((s['updated_at'] for s in states),default=0),
                         'description':{'steam':'Steam 官方商店获取；免费状态和 Deck 兼容性以商店信息为准。', 'legacy':'保留已有目录与下载方式。', 'flathub':'Linux 免费游戏，打开 Flathub 安装页面。',
                         'homebrew':'Switch 自制游戏，打开 4TU 项目页面；NRO 模拟器兼容性未验证。'}[source]})
        return ok({'sources':rows, 'total':len(self._catalog_items)})

    async def update_catalog(self, payload=None):
        await self._ensure_catalog_ready()
        source = payload.get('source','') if isinstance(payload,dict) else ''
        if source and source not in SOURCE_LABELS:
            return {'status':'error','message':'未知来源'}
        cache=self._catalog_refresh_cache
        keys=[k for k in cache.entries if not source or (source=='legacy' and not k.startswith('source-')) or k=='source-'+source]
        for key in keys:
            cache.refresh(key,force=True)
        return ok({'total':len(self._catalog_items), 'queued':True})

    async def get_catalog_version(self):
        return ok({'date':'本地缓存 + 后台更新', 'total':len(self._catalog_items)})

    async def _close_catalog(self):
        cache=getattr(self,'_catalog_refresh_cache',None)
        if cache:
            await cache.close()
        tasks=list(getattr(self,'_catalog_group_tasks',{}).values())+list(getattr(self,'_catalog_event_tasks',set()))
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)
