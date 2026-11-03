"""Offline protocol and recovery tests for the game calendar sources."""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import game_event_sources as sources


def run(coro):
    return asyncio.run(coro)


def sra_body(**changes):
    body = {'version': '4.6', 'activities': [{'name': '测试活动', 'startTime': 100,
            'endTime': 200, 'description': '说明', 'kind': '限时活动', 'cover': 'https://cdn.example/cover.png'}]}
    body.update(changes)
    return body


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_sra_normalizes_all_eight_games_without_pools(tmp_path):
    seen = []
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=sra_body(pools=[{'name': '不引入卡池', 'endTime': 300}]))
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, client=http, clock=lambda: 1234)
            for game in ('sr', 'ys', 'zzz', 'ww', 'nte', 'ba-cn', 'ba-global', 'ba-jp'):
                result = await source.fetch(game)
                assert result['game_id'] == game
                assert result['source'] == 'sra'
                assert result['version'] == '4.6'
                assert result['complete'] and not result['stale'] and not result['error']
                assert result['checked_at'] == result['fetched_at'] == 1234
                assert len(result['activities']) == 1
                assert result['activities'][0]['tags'] == ['限时活动']
                assert result['activities'][0]['start'] == 100
            assert seen == [f'/api/v1/activity/{g}.json' for g in
                            ('sr', 'ys', 'zzz', 'ww', 'nte', 'ba-cn', 'ba-global', 'ba-jp')]
    run(exercise())


def test_conditional_cache_survives_restart_and_304_returns_entity(tmp_path):
    now = [1000]
    def first(request):
        return httpx.Response(200, json=sra_body(), headers={'ETag': '"v1"', 'Last-Modified': 'Sat, 31 Oct 2026 00:00:00 GMT'})
    def second(request):
        assert request.headers['If-None-Match'] == '"v1"'
        assert request.headers['If-Modified-Since'] == 'Sat, 31 Oct 2026 00:00:00 GMT'
        return httpx.Response(304)
    async def exercise():
        async with client_for(first) as http:
            a = await sources.SourceClient(tmp_path, http, lambda: now[0]).fetch('sr')
        now[0] = 1500
        async with client_for(second) as http:
            b = await sources.SourceClient(tmp_path, http, lambda: now[0]).fetch('sr')
        assert b['activities'] == a['activities']
        assert b['checked_at'] == 1500
        assert b['fetched_at'] == 1000
        assert not b['stale']
    run(exercise())


def test_uncached_304_retries_once_without_validators(tmp_path):
    count = [0]
    def handler(request):
        count[0] += 1
        assert 'If-None-Match' not in request.headers
        return httpx.Response(304) if count[0] == 1 else httpx.Response(200, json=sra_body())
    async def exercise():
        async with client_for(handler) as http:
            result = await sources.SourceClient(tmp_path, http).fetch('sr')
            assert len(result['activities']) == 1
        assert count[0] == 2
    run(exercise())


@pytest.mark.parametrize('bad', [{'error': 'maintenance'}, {'activities': {}},
                                  {'activities': [{'name': '坏时间', 'endTime': 'oops'}]}])
def test_bad_success_never_overwrites_last_complete_snapshot(tmp_path, bad):
    bodies = iter([sra_body(), bad])
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=next(bodies))) as http:
            source = sources.SourceClient(tmp_path, http, lambda: 500)
            good = await source.fetch('sr')
            stale = await source.fetch('sr', force=True)
            assert stale['stale'] and stale['error'] and stale['complete']
            assert stale['activities'] == good['activities']
        async with client_for(lambda r: httpx.Response(503)) as http:
            restored = await sources.SourceClient(tmp_path, http).fetch('sr')
            assert restored['activities'] == good['activities']
    run(exercise())


def test_source_failure_isolated_and_empty_list_is_valid(tmp_path):
    def handler(request):
        return httpx.Response(403) if request.url.path.endswith('/sr.json') else httpx.Response(200, json=sra_body(activities=[]))
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http)
            with pytest.raises(sources.SourceError):
                await source.fetch('sr')
            result = await source.fetch('ys')
            assert result['activities'] == [] and result['complete']
            with pytest.raises(sources.SourceError):
                await source.fetch('https://localhost/private')
    run(exercise())


def ake_tables(version='v1'):
    return {
        '/manifest.json': {'latest': version, 'versions': [{'id': version, 'gameVersion': '1.0',
                          'updatedAt': version, 'tableCfgPath': f'tables/{version}'}]},
        f'/tables/{version}/ActivityTable.json': {'act': {'name': 'name_act', 'desc': 'desc_act',
                'timeId': 't', 'tagId': 'tag', 'tabImg': 'images/banner.png'}},
        f'/tables/{version}/TimeRangeTable.json': {'t': {'timeRangeList': [
            {'openTime': 100, 'closeTime': 200}, {'openTime': 300, 'closeTime': 400},
            {'openTime': 100, 'closeTime': 200}, {'openTime': 400}]}},
        f'/tables/{version}/ActivityTagTable.json': {'tag': {'name': 'tag_name'}},
        f'/tables/{version}/I18nTextTable_CN.json': {'name_act': '活动标题', 'desc_act': '活动说明',
                                                  'tag_name': '限时', 'gacha_name': '寻访标题'},
        f'/tables/{version}/GachaCharPoolTable.json': {'pool': {'name': 'gacha_name',
                    'clientTopTimeId': 't', 'type': 1, 'upCharIds': ['char_a']}},
    }


def test_ake_joins_tables_emits_each_occurrence_and_honors_gacha_toggle(tmp_path):
    tables = ake_tables()
    def handler(request):
        assert request.url.host == 'data.akedata.wiki'
        assert 'CharacterTable' not in request.url.path
        return httpx.Response(200, json=tables[request.url.path], headers={'ETag': '"one"'})
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http)
            result = await source.fetch('endfield', include_gacha=False)
            assert len(result['activities']) == 2
            assert [e['start'] for e in result['activities']] == [100, 300]
            assert len({e['uid'] for e in result['activities']}) == 2
            assert result['activities'][0]['name'] == '活动标题'
            assert result['activities'][0]['description'] == '活动说明'
            assert result['activities'][0]['tags'] == ['限时']
            assert result['activities'][0]['cover'].endswith('/images/banner.png')
            with_gacha = await source.fetch('endfield', include_gacha=True)
            assert len(with_gacha['activities']) == 4
            assert any('寻访' in e['tags'] for e in with_gacha['activities'])
    run(exercise())


def test_ake_304_after_restart_and_hotfix_revalidates_text(tmp_path):
    tables = ake_tables()
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=tables[r.url.path], headers={'ETag': '"old"'})) as http:
            old = await sources.SourceClient(tmp_path, http).fetch('endfield')
        async with client_for(lambda r: httpx.Response(304)) as http:
            cached = await sources.SourceClient(tmp_path, http).fetch('endfield', force=True)
            assert cached['activities'] == old['activities'] and not cached['stale']
        newer = ake_tables('v2')
        newer['/tables/v2/I18nTextTable_CN.json']['name_act'] = '修订标题'
        requested = []
        def hotfix(request):
            requested.append(request.url.path)
            return httpx.Response(200, json=newer[request.url.path])
        async with client_for(hotfix) as http:
            new = await sources.SourceClient(tmp_path, http).fetch('endfield', force=True)
            assert new['activities'][0]['name'] == '修订标题'
            assert '/tables/v2/I18nTextTable_CN.json' in requested
            assert new['version'] != old['version']
    run(exercise())


@pytest.mark.parametrize('path', ['https://evil.example/tables', '../secret', '%2e%2e/secret', '//evil.example/tables'])
def test_ake_rejects_untrusted_manifest_paths(tmp_path, path):
    manifest = {'latest': 'v', 'versions': [{'id': 'v', 'gameVersion': '1', 'tableCfgPath': path}]}
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=manifest)) as http:
            with pytest.raises(sources.SourceError):
                await sources.SourceClient(tmp_path, http).fetch('endfield')
    run(exercise())


def test_ake_partial_version_keeps_whole_previous_snapshot(tmp_path):
    tables = ake_tables()
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=tables[r.url.path])) as http:
            previous = await sources.SourceClient(tmp_path, http).fetch('endfield')
        newer = ake_tables('v2')
        def partial(request):
            return httpx.Response(500) if request.url.path.endswith('/TimeRangeTable.json') else httpx.Response(200, json=newer[request.url.path])
        async with client_for(partial) as http:
            result = await sources.SourceClient(tmp_path, http).fetch('endfield', force=True)
            assert result['stale'] and result['complete']
            assert result['version'] == previous['version']
            assert result['activities'] == previous['activities']
    run(exercise())


def prts_page(name='页面', continuation=None, category='支线故事'):
    result = {'query': {'results': {name: {'fulltext': name, 'fullurl': f'https://prts.wiki/w/{name}',
        'printouts': {'活动名称': ['正式活动'], '活动开始时间': [{'timestamp': 100}],
            '活动结束时间': [{'timestamp': 200}], '兑换结束时间': [{'timestamp': 300}],
            '分类': [{'fulltext': '分类:' + category}], '标题图文件名': ['图.png']}}}}}
    if continuation is not None:
        result['query-continue-offset'] = continuation
    return result


def test_prts_follows_pages_with_limit_and_twenty_second_spacing(tmp_path, monkeypatch):
    now = [1000]
    sleeps, requests = [], []
    async def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(sources.asyncio, 'sleep', fake_sleep)
    def handler(request):
        query = request.url.params['query']
        assert '|limit=100' in query
        assert request.headers.get('Referer') == 'https://prts.wiki/'
        requests.append((now[0], query))
        return httpx.Response(200, json=prts_page('甲', 100) if len(requests) == 1 else prts_page('乙'))
    async def exercise():
        async with client_for(handler) as http:
            result = await sources.SourceClient(tmp_path, http, lambda: now[0]).fetch('arknights')
            assert len(result['activities']) == 2 and result['complete']
            assert result['activities'][0]['end'] == 200
            assert result['activities'][0]['extra_times'] == [{'label': '兑换结束', 'at': 300.0}]
        assert sleeps == [20]
        assert requests[1][0] - requests[0][0] >= 20
        assert '|offset=100' in requests[1][1]
    run(exercise())


def test_prts_truncation_does_not_replace_complete_cache(tmp_path, monkeypatch):
    async def no_wait(seconds):
        pass
    monkeypatch.setattr(sources.asyncio, 'sleep', no_wait)
    count = [0]
    def handler(request):
        count[0] += 1
        return httpx.Response(200, json=prts_page('旧活动') if count[0] == 1 else prts_page(str(count[0]), count[0] * 100))
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http, lambda: count[0] * 30 + 1000)
            good = await source.fetch('arknights')
            stale = await source.fetch('arknights', force=True)
            assert stale['stale'] and stale['complete'] and 'partial' in stale['error'].lower()
            assert stale['activities'] == good['activities']
        assert count[0] == 6
    run(exercise())


def test_prts_filters_maintenance_categories(tmp_path):
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=prts_page(category='需要清理的页面'))) as http:
            result = await sources.SourceClient(tmp_path, http).fetch('arknights')
            assert result['activities'] == [] and result['complete']
    run(exercise())


def test_json_size_limit_is_enforced_during_stream(tmp_path):
    class HugeStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(6):
                yield b' ' * (1024 * 1024)
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, stream=HugeStream())) as http:
            with pytest.raises(sources.SourceError, match='size|large|limit'):
                await sources.SourceClient(tmp_path, http).fetch('sr')
    run(exercise())

# Python 3.12's Windows mode=0o700 temp directories exclude the sandbox token.
# Use a uniquely named workspace directory with inherited ACLs instead.
@pytest.fixture
def tmp_path():
    import shutil
    import uuid
    root = Path(__file__).resolve().parents[2] / '.tmp' / 'game-source-tests'
    root.mkdir(parents=True, exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        assert path.resolve().parent == root.resolve()
        shutil.rmtree(path)



def test_failed_recheck_preserves_original_checked_and_fetched_times(tmp_path):
    now = [1000]
    status = [200]
    def handler(request):
        return httpx.Response(200, json=sra_body()) if status[0] == 200 else httpx.Response(429)
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http, lambda: now[0])
            await source.fetch('sr')
            now[0], status[0] = 50000, 429
            result = await source.fetch('sr')
            assert result['checked_at'] == 1000
            assert result['fetched_at'] == 1000
            assert result['complete'] and result['stale'] and result['error']
    run(exercise())


def test_repeated_uncached_304_stops_after_one_retry(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(304)
    async def exercise():
        async with client_for(handler) as http:
            with pytest.raises(sources.SourceError, match='304'):
                await sources.SourceClient(tmp_path, http).fetch('sr')
        assert len(calls) == 2
    run(exercise())


def test_bad_200_does_not_poison_persisted_validator(tmp_path):
    calls = [0]
    def handler(request):
        calls[0] += 1
        if calls[0] == 1:
            return httpx.Response(200, json=sra_body(), headers={'ETag': '"good"'})
        if calls[0] == 2:
            return httpx.Response(200, json={'error': 'broken'}, headers={'ETag': '"bad"'})
        assert request.headers['If-None-Match'] == '"good"'
        return httpx.Response(304)
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http)
            old = await source.fetch('sr')
            await source.fetch('sr', force=True)
            restored = await sources.SourceClient(tmp_path, http).fetch('sr', force=True)
            assert restored['activities'] == old['activities']
            assert not restored['stale']
    run(exercise())


def test_prts_failed_continuation_keeps_snapshot_and_does_not_retry_403(tmp_path, monkeypatch):
    now = [1000]
    calls = [0]
    async def fake_sleep(seconds):
        now[0] += seconds
    monkeypatch.setattr(sources.asyncio, 'sleep', fake_sleep)
    def handler(request):
        calls[0] += 1
        if calls[0] == 1:
            return httpx.Response(200, json=prts_page('旧活动'))
        if calls[0] == 2:
            return httpx.Response(200, json=prts_page('新活动', 100))
        return httpx.Response(403)
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http, lambda: now[0])
            old = await source.fetch('arknights')
            result = await source.fetch('arknights', force=True)
            assert result['activities'] == old['activities']
            assert result['stale'] and result['complete']
        assert calls[0] == 3
    run(exercise())


def test_ake_text_table_has_larger_budget_than_other_json(tmp_path):
    tables = ake_tables()
    tables['/tables/v1/I18nTextTable_CN.json']['unused_text'] = 'x' * (6 * 1024 * 1024)
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=tables[r.url.path])) as http:
            result = await sources.SourceClient(tmp_path, http).fetch('endfield')
            assert len(result['activities']) == 4 and result['complete']
    run(exercise())


def test_http_timeout_is_bounded_and_close_owns_only_created_client(tmp_path):
    def handler(request):
        assert request.extensions['timeout'] == {'connect': 20, 'read': 20, 'write': 20, 'pool': 20}
        return httpx.Response(200, json=sra_body())
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http)
            await source.fetch('sr')
            await source.close()
            assert not http.is_closed
        own = sources.SourceClient(tmp_path)
        await own.close()
        assert own._client.is_closed
    run(exercise())


def test_prts_uncached_304_retry_also_obeys_source_spacing(tmp_path, monkeypatch):
    now, requested = [1000], []
    async def fake_sleep(seconds):
        now[0] += seconds
    monkeypatch.setattr(sources.asyncio, 'sleep', fake_sleep)
    def handler(request):
        requested.append(now[0])
        return httpx.Response(304) if len(requested) == 1 else httpx.Response(200, json=prts_page())
    async def exercise():
        async with client_for(handler) as http:
            result = await sources.SourceClient(tmp_path, http, lambda: now[0]).fetch('arknights')
            assert result['complete']
        assert requested == [1000, 1020]
    run(exercise())


def test_sra_reports_discarded_invalid_rows_without_losing_good_rows(tmp_path, caplog):
    body = sra_body()
    body['activities'].extend([{'name': '损坏活动', 'endTime': 'invalid'}, None])
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=body)) as http:
            result = await sources.SourceClient(tmp_path, http).fetch('sr')
            assert len(result['activities']) == 1 and result['complete']
        assert 'discarded 2' in caplog.text
    run(exercise())


def test_ake_handles_verified_text_refs_slash_dates_and_default_pool_time(tmp_path):
    tables = ake_tables()
    tables['/tables/v1/ActivityTable.json']['act'].update(
        name={'id': 123, 'text': ''}, desc={'id': 456, 'text': ''}, tabImg='activity_tab_bg_stamina')
    tables['/tables/v1/TimeRangeTable.json']['t']['timeRangeList'] = [
        {'openTime': '2026/10/15 7:00:00', 'closeTime': '2026/11/5 12:00:00'}]
    tables['/tables/v1/TimeRangeTable.json']['time_pool'] = tables['/tables/v1/TimeRangeTable.json']['t']
    tables['/tables/v1/GachaCharPoolTable.json'] = {
        'pool': {'name': {'id': 789, 'text': ''}, 'type': 0, 'upCharIds': ['char_a']},
        'standard': {'name': {'id': 999, 'text': ''}, 'type': 2}}
    tables['/tables/v1/I18nTextTable_CN.json'].update({'123': '真实活动', '456': '真实说明', '789': '真实寻访'})
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=tables[r.url.path])) as http:
            result = await sources.SourceClient(tmp_path, http).fetch('endfield')
            assert not result['stale'] and len(result['activities']) == 2
            activity, pool = result['activities']
            assert activity['name'] == '真实活动' and activity['description'] == '真实说明'
            assert activity['start'] == 1792018800
            assert activity['cover'] == ('https://data.akedata.wiki/public/images/assets/beyond/'
                'dynamicassets/gameplay/ui/sprites/activity/activity_tab_bg_stamina.png')
            assert pool['name'] == '真实寻访' and '限定寻访' in pool['tags']
            assert 'char_a' in pool['description']
    run(exercise())


def test_ake_top_level_manifest_revision_revalidates_same_game_version(tmp_path):
    tables = ake_tables()
    now = [1000]
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=tables[r.url.path])) as http:
            source = sources.SourceClient(tmp_path, http, lambda: now[0])
            old = await source.fetch('endfield')
            now[0] += 301
            tables['/manifest.json']['sharedRevision'] = 'new-shared-data'
            tables['/tables/v1/I18nTextTable_CN.json']['name_act'] = '同版本修订名称'
            new = await source.fetch('endfield')
            assert new['activities'][0]['name'] == '同版本修订名称'
            assert new['version'] != old['version']
    run(exercise())


def test_prts_ask_uses_real_template_properties(tmp_path):
    def handler(request):
        query = request.url.params['query']
        assert '[[活动结束时间::>=' in query
        for name in ('活动开始时间', '活动结束时间', '兑换结束时间', '名称', '名称缩短'):
            assert '|?' + name in query
        page = prts_page()
        fields = page['query']['results']['页面']['printouts']
        fields['名称'] = fields.pop('活动名称')
        return httpx.Response(200, json=page)
    async def exercise():
        async with client_for(handler) as http:
            result = await sources.SourceClient(tmp_path, http).fetch('arknights')
            assert result['activities'][0]['name'] == '正式活动'
    run(exercise())


@pytest.mark.parametrize('game,body', [('sr', {'error': 'maintenance', 'activities': []}),
    ('arknights', {'error': {'code': 'internalerror'}, 'query': {'results': {}}})])
def test_http_200_error_body_is_not_an_empty_calendar(tmp_path, game, body):
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=body)) as http:
            with pytest.raises(sources.SourceError, match='error'):
                await sources.SourceClient(tmp_path, http).fetch(game)
    run(exercise())


def test_query_cache_reuses_complete_snapshot_for_300_seconds_after_restart(tmp_path):
    now, calls = [1000], [0]
    def handler(request):
        calls[0] += 1
        return httpx.Response(200, json=sra_body())
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http, lambda: now[0])
            await source.fetch('sr')
            now[0] = 1299
            restarted = sources.SourceClient(tmp_path, http, lambda: now[0])
            result = await restarted.fetch('sr')
            assert result['checked_at'] == 1000 and not result['stale']
            assert calls[0] == 1
            now[0] = 1300
            await restarted.fetch('sr')
            assert calls[0] == 2
    run(exercise())


def test_simultaneous_nonforce_fetches_share_one_complete_result(tmp_path):
    calls = [0]
    async def handler(request):
        calls[0] += 1
        await asyncio.sleep(0)
        return httpx.Response(200, json=sra_body())
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http, lambda: 1000)
            results = await asyncio.gather(source.fetch('sr'), source.fetch('sr'), source.fetch('sr'))
            assert results[0] == results[1] == results[2]
            assert calls[0] == 1
    run(exercise())


def test_failed_refresh_negative_cache_survives_restart_without_healthy_ttl(tmp_path):
    now, calls, status = [1000], [0], [200]
    def handler(request):
        calls[0] += 1
        return httpx.Response(200, json=sra_body()) if status[0] == 200 else httpx.Response(503)
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http, lambda: now[0])
            await source.fetch('sr')
            status[0], now[0] = 503, 1001
            failed = await source.fetch('sr', force=True)
            repeated = await source.fetch('sr')
            assert failed['stale'] and repeated['stale']
            assert repeated['checked_at'] == 1000 and calls[0] == 2
            restarted = sources.SourceClient(tmp_path, http, lambda: now[0])
            restored = await restarted.fetch('sr')
            assert restored['stale'] and restored['error'] and calls[0] == 2
            status[0], now[0] = 200, 1062
            refreshed = await restarted.fetch('sr')
            assert not refreshed['stale'] and refreshed['checked_at'] == 1062
            assert calls[0] == 3
    run(exercise())


def test_failure_without_snapshot_also_has_short_negative_cache(tmp_path):
    calls = [0]
    def handler(request):
        calls[0] += 1
        return httpx.Response(429)
    async def exercise():
        async with client_for(handler) as http:
            source = sources.SourceClient(tmp_path, http, lambda: 1000)
            for _ in range(2):
                with pytest.raises(sources.SourceError):
                    await source.fetch('sr')
            assert calls[0] == 1
            with pytest.raises(sources.SourceError):
                await source.fetch('sr', force=True)
            assert calls[0] == 2
    run(exercise())


def test_sra_source_id_keeps_identity_when_title_is_edited(tmp_path):
    body = sra_body()
    body['activities'][0]['id'] = 'announcement-42'
    async def exercise():
        async with client_for(lambda r: httpx.Response(200, json=body)) as http:
            source = sources.SourceClient(tmp_path, http)
            old = await source.fetch('sr')
            body['activities'][0]['name'] = '修订标题'
            edited = await source.fetch('sr', force=True)
            assert old['activities'][0]['uid'] == edited['activities'][0]['uid']
            assert edited['activities'][0]['name'] == '修订标题'
    run(exercise())
