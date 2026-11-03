# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。
import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import epic_free_service as service

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def payload(name):
    return json.loads((FIXTURES / name).read_text('utf-8'))


def run_fetch(response, country='CN'):
    async def run():
        transport = httpx.MockTransport(lambda request: response)
        async with httpx.AsyncClient(transport=transport) as client:
            return await service.fetch(country=country, client=client)
    return asyncio.run(run())


class EpicFreeServiceTest(unittest.TestCase):
    def test_fetch_cn_returns_elements(self):
        elements = run_fetch(httpx.Response(200, json=payload('epic_free_cn.json')))
        self.assertEqual(len(elements), 12)

    def test_fetch_sends_country_and_locale(self):
        seen = {}

        def handler(request):
            seen['params'] = dict(request.url.params)
            seen['ua'] = request.headers.get('user-agent')
            return httpx.Response(200, json=payload('epic_free_us.json'))

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                await service.fetch(country='US', client=client)

        asyncio.run(run())
        self.assertEqual(seen['params']['country'], 'US')
        self.assertEqual(seen['params']['locale'], 'zh-CN')
        self.assertTrue(seen['ua'].startswith('denia-bot'))

    def test_http_error(self):
        with patch.object(service.asyncio, 'sleep', new=AsyncMock()):
            with self.assertRaises(service.EpicHTTPError):
                run_fetch(httpx.Response(503, text='down'))

    def test_error_body_is_api_error(self):
        body = {'errors': [{'serviceResponse': 'invalid_country_code'}], 'data': {'Catalog': {'searchStore': None}}}
        with self.assertRaises(service.EpicAPIError):
            run_fetch(httpx.Response(200, json=body))

    def test_empty_elements_is_empty_error(self):
        body = {'data': {'Catalog': {'searchStore': {'elements': []}}}}
        with self.assertRaises(service.EpicEmptyError):
            run_fetch(httpx.Response(200, json=body))

    def test_invalid_json_is_api_error(self):
        with self.assertRaises(service.EpicAPIError):
            run_fetch(httpx.Response(200, content=b'not json'))

    def test_network_error_is_epic_error(self):
        def handler(request):
            raise httpx.ConnectError('boom', request=request)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                await service.fetch(client=client)

        with patch.object(service.asyncio, 'sleep', new=AsyncMock()):
            with self.assertRaises(service.EpicError):
                asyncio.run(run())

    def test_connect_error_falls_back_to_second_host(self):
        calls = []

        def handler(request):
            calls.append(request.url.host)
            if len(calls) <= service.NETWORK_ATTEMPTS:
                raise httpx.ConnectError('boom', request=request)
            return httpx.Response(200, json=payload('epic_free_cn.json'))

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.object(service.asyncio, 'sleep', new=AsyncMock()):
                    return await service.fetch(client=client)

        elements = asyncio.run(run())
        self.assertEqual(len(elements), 12)
        self.assertEqual(calls[0], service.API_HOSTS[0])
        self.assertEqual(calls[-1], service.API_HOSTS[1])

    def test_fetch_view(self):
        async def run():
            transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload('epic_free_cn.json')))
            async with httpx.AsyncClient(transport=transport) as client:
                return await service.fetch_view(client=client, now=datetime(2026, 10, 31, 3, 14, tzinfo=timezone.utc))

        view = asyncio.run(run())
        self.assertEqual(len(view['free']), 1)
        self.assertEqual(len(view['upcoming']), 2)

    def test_state_store_round_trip_and_targets(self):
        games = [{'id': 'g1', 'start_iso': '2026-10-29T15:00:00+00:00'}]
        with tempfile.TemporaryDirectory() as folder:
            store = service.StateStore(Path(folder) / 'state.json')
            state = store.load()
            self.assertEqual(state['targets'], {})
            store.mark(state, 'group:1', 'key-1', games=games, when=datetime(2026, 10, 31, tzinfo=timezone.utc))
            store.save(state)
            loaded = store.load()
            self.assertEqual(store.last_key(loaded, 'group:1'), 'key-1')
            self.assertEqual(store.last_games(loaded, 'group:1'), games)
            self.assertIsNone(store.last_key(loaded, 'group:2'))
            self.assertFalse(store.should_push(loaded, 'group:1', games))
            self.assertTrue(store.should_push(loaded, 'group:1', games + [{'id': 'g2', 'start_iso': '2026-10-29T15:00:00+00:00'}]))

    def test_state_store_ignores_pure_removal(self):
        games = [
            {'id': 'g1', 'start_iso': '2026-10-29T15:00:00+00:00'},
            {'id': 'g2', 'start_iso': '2026-10-29T15:00:00+00:00'},
        ]
        with tempfile.TemporaryDirectory() as folder:
            store = service.StateStore(Path(folder) / 'state.json')
            state = store.load()
            store.mark(state, 'group:1', 'key', games=games, when=datetime(2026, 10, 31, tzinfo=timezone.utc))
            store.save(state)
            loaded = store.load()
            self.assertFalse(store.should_push(loaded, 'group:1', games[:1]))
            self.assertTrue(store.should_push(loaded, 'group:1', games[:1] + [{'id': 'g3', 'start_iso': '2026-10-29T15:00:00+00:00'}]))

    def test_state_store_corrupt_file_is_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text('{broken', encoding='utf-8')
            store = service.StateStore(path)
            self.assertEqual(store.load()['targets'], {})

    def test_state_store_claim_persists(self):
        games = [{'id': 'g1', 'start_iso': '2026-10-29T15:00:00+00:00'}]
        with tempfile.TemporaryDirectory() as folder:
            store = service.StateStore(Path(folder) / 'state.json')
            state = store.load()
            store.claim(state, 'private:7', 'key', games=games, when=datetime(2026, 10, 31, tzinfo=timezone.utc))
            self.assertEqual(store.last_key(store.load(), 'private:7'), 'key')


if __name__ == '__main__':
    unittest.main()
