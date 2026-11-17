"""Offline checks for catalog latency, refresh isolation and acquisition metadata."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'py_modules'))
from catalog_cache import CatalogCache
from catalog_sources import CatalogSources, normalize_items, merge_items, homebrew_items, steam_item

GAME={'game_id':'one','title':'One','platform':'pc'}

class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.gate=asyncio.Event()
        self.calls=[]
        self.now=1000
        async def fetch(key):
            self.calls.append(key)
            await self.gate.wait()
            return [GAME]
        self.cache=CatalogCache(self.tmp.name,fetch,clock=lambda:self.now)
    async def asyncTearDown(self):
        await self.cache.close()
        self.tmp.cleanup()
    async def settle(self):
        await asyncio.gather(*list(self.cache.tasks.values()))
        await asyncio.sleep(0)
    async def test_cold_and_concurrent_reads_never_wait_for_network(self):
        results=await asyncio.wait_for(asyncio.gather(*(self.cache.get('platform-pc',[GAME]) for _ in range(20))),1)
        self.assertTrue(all(result==[GAME] for result in results))
        await asyncio.sleep(0)
        self.assertEqual(self.calls,['platform-pc'])
        self.assertTrue(self.cache.status('platform-pc')['refreshing'])
    async def test_persistence_stale_reads_and_failed_refresh_keep_previous_data(self):
        self.gate.set();await self.cache.get('all');await self.settle()
        offline=AsyncMock(side_effect=TimeoutError('offline'))
        second=CatalogCache(self.tmp.name,offline,clock=lambda:self.now)
        try:
            self.assertEqual(await second.get('all'),[GAME]);offline.assert_not_called()
            self.now+=2000
            self.assertEqual(await second.get('all'),[GAME])
            await asyncio.gather(*list(second.tasks.values()));await asyncio.sleep(0)
            self.assertTrue(second.status('all')['error'])
            for _ in range(10):self.assertEqual(await second.get('all'),[GAME])
            self.assertEqual(offline.await_count,1)
        finally:await second.close()
    async def test_empty_is_cached_and_manual_refresh_is_deduplicated(self):
        self.cache.fetcher=AsyncMock(return_value=[])
        await self.cache.get('all');await self.settle()
        self.assertEqual(await self.cache.get('all',[GAME]),[])
        self.assertEqual(self.cache.fetcher.await_count,1)
        for _ in range(20):self.cache.refresh('all',force=True)
        await self.settle();self.assertEqual(self.cache.fetcher.await_count,2)
    async def test_empty_refresh_does_not_erase_populated_cache(self):
        self.gate.set();await self.cache.get('all');await self.settle()
        self.cache.fetcher=AsyncMock(return_value=[])
        self.cache.refresh('all',True);await self.settle()
        self.assertEqual(await self.cache.get('all'),[GAME])
        self.assertTrue(self.cache.status('all')['error'])
    async def test_corrupt_cache_and_path_traversal(self):
        Path(self.tmp.name,'all.json').write_text('not json')
        self.assertEqual(await self.cache.get('all',[GAME]),[GAME])
        with self.assertRaises(ValueError):await self.cache.get('../outside')
    async def test_network_concurrency_is_bounded_and_unload_cancels(self):
        await asyncio.gather(*(self.cache.get('platform-'+str(i)) for i in range(12)))
        await asyncio.sleep(0)
        self.assertEqual(len(self.calls),4)
        await self.cache.close()
        self.assertFalse(self.cache.tasks)

class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_alias_after_timeout_and_empty_feed(self):
        for failure in [TimeoutError('unreachable'),[]]:
            request=AsyncMock(side_effect=[failure,[dict(GAME,platform='NS')]])
            src=CatalogSources('unused',request)
            self.assertEqual((await src.fetch('platform-switch'))[0]['platform'],'switch')
            self.assertTrue(request.call_args_list[1].args[0].endswith('/ns.json'))
    async def test_normalization_and_source_ids_preserve_distinct_acquisition(self):
        a=dict(GAME,platform=' NS ')
        self.assertEqual(normalize_items({'games':[a,None]})[0]['platform'],'switch')
        self.assertEqual(len(merge_items([a],[a])),1)
        with self.assertRaises(ValueError):normalize_items({'error':'bad'})
    async def test_homebrew_updates_only_curated_games(self):
        seeds=[dict(GAME,source_item_id='2048',acquisition='external',action_url='https://example.com/')]
        result=homebrew_items({'packages':[{'name':'2048','title':'2048','category':'game','version':'new'},{'name':'unknown','category':'game'}]},seeds)
        self.assertEqual(len(result),1);self.assertEqual(result[0]['version'],'new')
        self.assertEqual(result[0]['acquisition'],'external')
    async def test_steam_price_change_does_not_remain_labelled_free(self):
        seed=dict(GAME,source_item_id='570',size_text='Steam · 免费',acquisition='external')
        result=steam_item({'570':{'success':True,'data':{'steam_appid':570,'type':'game','name':'Game','is_free':False}}},seed)
        self.assertNotIn('免费',result['size_text'])
        self.assertIn('确认价格',result['acquisition_note'])

    async def test_bundled_games_have_real_sources_and_no_cloud_download_links(self):
        src=CatalogSources(Path(__file__).resolve().parents[1]/'defaults/community_games.json')
        items=await src.load()
        self.assertGreaterEqual(len(items),46)
        self.assertEqual(len({i['game_id'] for i in items}),len(items))
        self.assertTrue(all(i['action_url'].startswith('https://') and i['acquisition']=='external' and not i['down_url'] for i in items))
        self.assertGreaterEqual(sum(i['platform']=='switch' for i in items),20)

class PluginCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_regressions import plugin
        self.plugin_module=plugin
        self.tmp=tempfile.TemporaryDirectory()
        self.p=plugin.Plugin();self.p._catalog_state_dir=lambda:self.tmp.name
        self.gate=asyncio.Event()
        async def offline(_self,_url):
            await self.gate.wait()
            raise TimeoutError('offline')
        self.patch=patch.object(CatalogSources,'_request',offline);self.patch.start()
    async def asyncTearDown(self):
        await self.p._close_catalog();self.patch.stop();self.tmp.cleanup()
    async def test_cold_pc_switch_and_discovery_available_while_network_hangs(self):
        async def read():
            pc=await self.p.get_platform_games({'platform':'pc'})
            switch=await self.p.get_platform_games({'platform':'ns'})
            home=await self.p.get_shelf_games()
            return pc,switch,home
        pc,switch,home=await asyncio.wait_for(read(),2)
        self.assertGreaterEqual(pc['data']['total'],26)
        self.assertEqual(switch['data']['total'],20)
        self.assertTrue(all(i['platform']=='switch' for i in switch['data']['games']))
        self.assertTrue(home['data']['games'])
        self.assertEqual(pc['data']['games'][0]['acquisition'],'external')
        self.assertFalse(pc['data']['games'][0]['down_url'])
    async def test_repeated_pages_reuse_grouping_and_source_filter_works(self):
        from unittest.mock import Mock
        self.p._catalog_grouper=Mock(wraps=self.plugin_module._group_catalog_items)
        await self.p.get_platform_games({'platform':'pc','page':1,'page_size':5})
        await self.p.get_platform_games({'platform':'pc','page':2,'page_size':5})
        self.assertEqual(self.p._catalog_grouper.call_count,1)
        result=await self.p.list_catalog({'source':'Flathub','platform':'pc','page_size':100})
        self.assertGreaterEqual(result['data']['total'],26)
        self.assertTrue(all(i['source_id']=='flathub' for i in result['data']['items']))
    async def test_existing_ns_cache_and_public_entries_both_survive_offline(self):
        Path(self.tmp.name,'catalog_cache.json').write_text(json.dumps([dict(GAME,platform='ns')]))
        result=await self.p.get_platform_games({'platform':'switch'})
        self.assertEqual(result['data']['total'],21)
        self.assertEqual((await self.p.get_platform_games({'platform':'../escape'}))['status'],'error')
    async def test_sources_and_manual_update_are_independent(self):
        result=await self.p.get_catalog_sources()
        self.assertEqual({row['id'] for row in result['data']['sources']},{'legacy','steam','flathub','homebrew'})
        self.assertEqual((await self.p.update_catalog({'source':'missing'}))['status'],'error')
        tasks=dict(self.p._catalog_refresh_cache.tasks)
        await self.p.update_catalog({'source':'flathub'})
        self.assertEqual(tasks,self.p._catalog_refresh_cache.tasks)

    async def test_play_later_preserves_external_acquisition(self):
        from play_later import normalize_play_later_payload
        item=dict(GAME,acquisition='external',action_url='https://flathub.org/apps/example',source_id='flathub')
        self.assertEqual(normalize_play_later_payload(item)['action_url'],item['action_url'])
        self.assertEqual(normalize_play_later_payload(item)['acquisition'],'external')
