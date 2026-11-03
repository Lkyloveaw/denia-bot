# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""Jian / EQSC 网络客户端用例（MockTransport，不联系真实服务）。"""

import asyncio
import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disaster_china_sources as sources


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class JianAuthTest(unittest.TestCase):
    def test_login_key_to_refresh_to_access(self):
        async def run():
            calls = []

            def handler(request):
                calls.append(request.url.path)
                if request.url.path == '/api/refresh':
                    return httpx.Response(200, json={
                        'ok': True, 'token': 'rt_TEST', 'expires_after_min': 259200,
                        'max_connections': 3,
                    })
                if request.url.path == '/api/access':
                    return httpx.Response(200, json={
                        'ok': True, 'token': 'at_TEST', 'expires_after_sec': 3600,
                    })
                return httpx.Response(404)

            client = client_for(handler)
            auth = sources.JianAuth('lk_TEST', client=client)
            try:
                token = await auth.access()
                self.assertEqual(token, 'at_TEST')
                self.assertEqual(auth.refresh_token, 'rt_TEST')
                self.assertTrue(auth.refresh_expires_date)
                self.assertEqual(await auth.access(), 'at_TEST')
                self.assertEqual(calls.count('/api/access'), 1, '缓存内不应重复换 access')
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_refresh_token_only_calls_access(self):
        async def run():
            calls = []

            def handler(request):
                calls.append(request.url.path)
                return httpx.Response(200, json={'ok': True, 'token': 'at_2', 'expires_after_sec': 3600})

            client = client_for(handler)
            auth = sources.JianAuth('rt_TEST', client=client)
            try:
                self.assertEqual(await auth.access(), 'at_2')
                self.assertEqual(calls, ['/api/access'])
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_access_token_direct(self):
        async def run():
            auth = sources.JianAuth('at_DIRECT')
            self.assertEqual(await auth.access(), 'at_DIRECT')
            await auth.close()

        asyncio.run(run())

    def test_login_key_is_not_reused_after_exchange(self):
        async def run():
            calls = []

            def handler(request):
                calls.append(request.url.path)
                if request.url.path == '/api/refresh':
                    return httpx.Response(200, json={
                        'ok': True, 'token': 'rt_ONCE', 'expires_after_min': 60,
                    })
                return httpx.Response(200, json={
                    'ok': True, 'token': f'at_{len(calls)}', 'expires_after_sec': 3600,
                })

            client = client_for(handler)
            auth = sources.JianAuth('lk_ONCE', client=client)
            try:
                self.assertTrue((await auth.access()).startswith('at_'))
                auth.access_token = ''
                auth.access_expires_at = 0
                self.assertTrue((await auth.access()).startswith('at_'))
                self.assertEqual(calls.count('/api/refresh'), 1, '一次性登录密钥不能二次兑换')
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_refresh_callback_runs_before_access(self):
        async def run():
            saved = []

            def handler(request):
                if request.url.path == '/api/refresh':
                    return httpx.Response(200, json={
                        'ok': True, 'token': 'rt_CALLBACK', 'expires_after_min': 60,
                    })
                return httpx.Response(500, text='access down')

            client = client_for(handler)
            auth = sources.JianAuth(
                'lk_ONCE',
                client=client,
                on_refresh=lambda token, date: saved.append((token, date)),
            )
            try:
                with self.assertRaises(sources.ChinaSourceError):
                    await auth.access()
                self.assertEqual(saved[0][0], 'rt_CALLBACK')
                self.assertTrue(saved[0][1])
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_login_key_error(self):
        async def run():
            def handler(request):
                return httpx.Response(200, json={'ok': False, 'code': 4101, 'message': 'invalid_login_key'})

            client = client_for(handler)
            auth = sources.JianAuth('lk_BAD', client=client)
            try:
                with self.assertRaises(sources.ChinaSourceError) as context:
                    await auth.access()
                self.assertIn('4101', str(context.exception))
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_http_error_body_is_surfaced(self):
        async def run():
            def handler(request):
                return httpx.Response(401, json={
                    'ok': False, 'code': 4101, 'message': 'invalid_login_key',
                })

            client = client_for(handler)
            auth = sources.JianAuth('lk_BAD', client=client)
            try:
                with self.assertRaises(sources.ChinaSourceError) as context:
                    await auth.access()
                self.assertIn('HTTP 401', str(context.exception))
                self.assertIn('4101', str(context.exception))
                self.assertIn('invalid_login_key', str(context.exception))
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_empty_transport_error_keeps_type(self):
        async def run():
            def handler(request):
                raise httpx.ConnectError('', request=request)

            client = client_for(handler)
            auth = sources.JianAuth('lk_BAD', client=client)
            try:
                with self.assertRaises(sources.ChinaSourceError) as context:
                    await auth.access()
                self.assertIn('ConnectError', str(context.exception))
            finally:
                await client.aclose()

        asyncio.run(run())


class EqscSourceTest(unittest.TestCase):
    def test_whitespace_in_token_is_removed(self):
        source = sources.EqscSource('ARh.abc\n def\tghi')
        self.assertEqual(source.refresh_token, 'ARh.abcdefghi')

    def test_rejects_non_arh_prefix_without_network(self):
        async def run():
            source = sources.EqscSource('MTY.wrong-token')
            try:
                with self.assertRaises(sources.ChinaSourceError) as context:
                    await source.access()
                self.assertIn('ARh.', str(context.exception))
            finally:
                await source.close()

        asyncio.run(run())

    def test_token_and_typhoon_list(self):
        async def run():
            calls = {'access': 0, 'typhoon': 0}

            def handler(request):
                if request.url.path == '/createAccessToken':
                    calls['access'] += 1
                    return httpx.Response(200, text='ATn.TEST,3600')
                if request.url.path == '/typhoonNMC.json':
                    calls['typhoon'] += 1
                    return httpx.Response(200, json={'typhoon': [{'id': '2518'}]})
                return httpx.Response(404)

            client = client_for(handler)
            source = sources.EqscSource('ARh.TEST', client=client)
            try:
                items = await source.fetch_typhoons()
                self.assertEqual(items, [{'id': '2518'}])
                await source.fetch_typhoons()
                self.assertEqual(calls['access'], 1, 'access token 应缓存')
                self.assertEqual(calls['typhoon'], 2)
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_bad_token_response(self):
        async def run():
            client = client_for(lambda request: httpx.Response(200, text='ARh.BAD,3600'))
            source = sources.EqscSource('ARh.TEST', client=client)
            try:
                with self.assertRaises(sources.ChinaSourceError):
                    await source.access()
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_typhoon_http_error(self):
        async def run():
            def handler(request):
                if request.url.path == '/createAccessToken':
                    return httpx.Response(200, text='ATn.TEST,3600')
                return httpx.Response(500, text='down')

            client = client_for(handler)
            source = sources.EqscSource('ARh.TEST', client=client)
            try:
                with self.assertRaises(sources.ChinaSourceError):
                    await source.fetch_typhoons()
            finally:
                await client.aclose()

        asyncio.run(run())


class JianSourceTest(unittest.TestCase):
    def test_injected_connect(self):
        async def run():
            class FakeConnection:
                def __init__(self):
                    self.closed = False

                async def recv(self):
                    return {'type': 'heartbeat'}

                async def close(self):
                    self.closed = True

            connection = FakeConnection()

            async def fake_connect(token):
                self.assertEqual(token, 'at_DIRECT')
                return connection

            source = sources.JianSource('at_DIRECT', connect=fake_connect)
            returned = await source.connect()
            self.assertIs(returned, connection)
            await source.close()
            self.assertTrue(connection.closed)

        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
