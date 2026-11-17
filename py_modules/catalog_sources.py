"""Catalog normalization and maintained public-source adapters.

New providers link to their upstream installation pages. They never enter the
cloud archive downloader, and homebrew compatibility is not assumed.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from urllib.parse import quote, urlparse

LEGACY_BASE = 'http://panyiwei.xyz/freedeck-catalog'
HOMEBREW_URL = 'https://switch.cdn.fortheusers.org/repo.json'
SOURCE_LABELS = {'legacy': '原有游戏目录', 'steam': 'Steam · 官方免费游戏', 'flathub': 'Flathub · Linux 游戏', 'homebrew': '4TU · Switch 自制游戏'}
PLATFORMS = {'pc', 'switch', 'ps1', 'ps2', 'ps3', 'psp', '3ds', 'gba', 'nds', 'fc', 'md'}
ALIASES = {'ns': 'switch', 'nintendo switch': 'switch', 'windows': 'pc', 'linux': 'pc', 'nes': 'fc', 'genesis': 'md', 'n3ds': '3ds'}


def normalize_platform(value):
    name = str(value or '').strip().lower()
    return ALIASES.get(name, name)


def normalize_items(data):
    if isinstance(data, dict):
        data = data.get('games', data.get('items'))
    if not isinstance(data, list):
        raise ValueError('来源没有返回游戏列表')
    result = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item['platform'] = normalize_platform(item.get('platform'))
        if not item.get('game_id') or not any(item.get(k) for k in ('title', 'title_zh', 'title_en')):
            continue
        result.append(item)
    return result


def merge_items(*groups):
    result = {}
    for group in groups:
        for item in normalize_items(group):
            # Versions can share IDs in legacy catalogs; keep distinct download targets.
            key = (item['platform'], str(item['game_id']), str(item.get('down_url', '')), str(item.get('version', '')))
            result.setdefault(key, item)
    return list(result.values())


def https_url(value):
    value = str(value or '').strip()
    parsed = urlparse(value)
    return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else ''


def flathub_item(data, seed):
    if not isinstance(data, dict) or data.get('id') != seed['source_item_id']:
        raise ValueError('Flathub 应用标识不匹配')
    if data.get('is_eol') or not data.get('is_free_license') or 'Game' not in (data.get('categories') or []):
        raise ValueError('Flathub 条目不再符合精选游戏范围')
    item = dict(seed)
    item.update(title=str(data.get('name') or seed['title']), title_en=str(data.get('name') or seed['title']),
                cover_url=https_url(data.get('icon')) or seed.get('cover_url', ''),
                license=str(data.get('project_license') or seed.get('license', '')),
                summary=str(data.get('summary') or seed.get('summary', '')),
                creator=str(data.get('developer_name') or seed.get('creator', '')))
    return item


def steam_item(data, seed):
    import re
    import html
    entry = data.get(seed['source_item_id'], {}) if isinstance(data, dict) else {}
    raw = entry.get('data', {})
    if not entry.get('success') or str(raw.get('steam_appid')) != seed['source_item_id'] or raw.get('type') != 'game':
        raise ValueError('Steam 商店条目暂不可用')
    item = dict(seed)
    item.update(title=str(raw.get('name') or seed['title']),
                summary=html.unescape(re.sub('<[^>]+>', '', str(raw.get('short_description') or ''))),
                cover_url=https_url(raw.get('header_image')) or seed.get('cover_url',''),
                size_text='Steam · 免费' if raw.get('is_free') else 'Steam · 商店')
    if not raw.get('is_free'):
        item['acquisition_note'] = '当前商店未标为免费，请在 Steam 商店确认价格、地区可用性和 Deck 兼容性。'
    return item


def homebrew_items(data, seeds):
    if not isinstance(data, dict) or not isinstance(data.get('packages'), list):
        raise ValueError('4TU 目录格式错误')
    by_name = {p.get('name'): p for p in data['packages'] if isinstance(p, dict) and p.get('category') == 'game'}
    result = []
    for seed in seeds:
        raw = by_name.get(seed['source_item_id'])
        if not raw:
            result.append(seed)
            continue
        item = dict(seed)
        item.update(title=str(raw.get('title') or seed['title']).strip(), version=str(raw.get('version') or ''),
                    license=str(raw.get('license') or seed.get('license', '')),
                    creator=str(raw.get('author') or seed.get('creator', '')))
        result.append(item)
    return result


class CatalogSources:
    def __init__(self, seed_path, request=None):
        self.seed_path = Path(seed_path)
        self.seeds = []
        self.request = request or self._request
        self.network_limit = asyncio.Semaphore(4)

    async def load(self):
        def read():
            data = json.loads(self.seed_path.read_text(encoding='utf-8'))
            return normalize_items(data['games'])
        self.seeds = await asyncio.to_thread(read)
        return self.seeds

    async def _request(self, url):
        import aiohttp
        async with self.network_limit:
            timeout = aiohttp.ClientTimeout(total=7, connect=3)
            async with aiohttp.ClientSession(timeout=timeout, headers={'User-Agent': 'DeckHarbor/0.4.3'}) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        chunks.extend(chunk)
                        if len(chunks) > 32 * 1024 * 1024:
                            raise ValueError('目录超过大小限制')
                    return json.loads(chunks)

    async def fetch(self, key):
        if key == 'source-homebrew':
            return homebrew_items(await self.request(HOMEBREW_URL), [s for s in self.seeds if s['source_id'] == 'homebrew'])
        if key == 'source-steam':
            seeds = [s for s in self.seeds if s['source_id'] == 'steam']
            async def one(seed):
                return steam_item(await self.request('https://store.steampowered.com/api/appdetails?appids='+seed['source_item_id']+'&l=schinese'), seed)
            results = await asyncio.gather(*(one(seed) for seed in seeds), return_exceptions=True)
            if any(isinstance(result, Exception) for result in results):
                raise ValueError('部分 Steam 条目暂时无法更新，保留本地目录')
            return results
        if key == 'source-flathub':
            seeds = [s for s in self.seeds if s['source_id'] == 'flathub']
            async def one(seed):
                return flathub_item(await self.request('https://flathub.org/api/v2/appstream/' + quote(seed['source_item_id'], safe='.')), seed)
            results = await asyncio.gather(*(one(seed) for seed in seeds), return_exceptions=True)
            failures = sum(isinstance(r, Exception) for r in results)
            # Keep the last complete snapshot on partial outages; retry later.
            if failures:
                raise ValueError(f'Flathub 有 {failures} 个条目暂时无法更新，保留本地目录')
            return results
        if key == 'all':
            return normalize_items(await self.request(LEGACY_BASE + '/all.json'))
        kind, _, platform = key.partition('-')
        platform = normalize_platform(platform)
        if kind not in ('platform', 'curated') or platform not in PLATFORMS:
            raise ValueError('未知目录')
        # Both spellings occur upstream; a failure/empty response must try the alias.
        names = ([platform, 'ns'] if kind == 'platform' else ['ns', platform]) if platform == 'switch' else [platform]
        last_error = None
        for name in names:
            try:
                items = normalize_items(await self.request(f'{LEGACY_BASE}/{kind}/{name}.json'))
                if items:
                    for item in items:
                        if not item['platform']:
                            item['platform'] = platform
                    return [item for item in items if item['platform'] == platform]
            except Exception as error:
                last_error = error
        if last_error:
            raise last_error
        return []
