# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""Wolfx HTTP 源用例：成功、HTTP 错误、非 JSON、非对象。"""

import asyncio
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from disaster_sources import SourceError, WolfxSource


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class WolfxSourceTest(unittest.TestCase):
    def test_cenc_eew_success(self):
        async def run():
            requests = []

            def handler(request):
                requests.append(str(request.url))
                return httpx.Response(200, json={'EventID': 'x', 'Magnitude': 4.2})

            client = client_for(handler)
            source = WolfxSource(client)
            try:
                data = await source.cenc_eew()
                self.assertEqual(data['EventID'], 'x')
                self.assertIn('cenc_eew.json', requests[0])
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_http_error_becomes_source_error(self):
        async def run():
            client = client_for(lambda request: httpx.Response(500, text='upstream down'))
            source = WolfxSource(client)
            try:
                with self.assertRaises(SourceError) as ctx:
                    await source.jma_eew()
                self.assertEqual(ctx.exception.kind, 'jma_eew')
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_non_json_becomes_source_error(self):
        async def run():
            client = client_for(lambda request: httpx.Response(200, text='not-json'))
            source = WolfxSource(client)
            try:
                with self.assertRaises(SourceError):
                    await source.cenc_list()
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_non_object_becomes_source_error(self):
        async def run():
            client = client_for(lambda request: httpx.Response(200, json=[1, 2]))
            source = WolfxSource(client)
            try:
                with self.assertRaises(SourceError):
                    await source.jma_list()
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_empty_connect_error_keeps_type(self):
        async def run():
            def handler(request):
                raise httpx.ConnectError('', request=request)

            client = client_for(handler)
            source = WolfxSource(client)
            try:
                with self.assertRaises(SourceError) as ctx:
                    await source.cenc_eew()
                self.assertIn('ConnectError', str(ctx.exception))
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_unknown_kind(self):
        async def run():
            source = WolfxSource(client_for(lambda request: httpx.Response(200, json={})))
            try:
                with self.assertRaises(SourceError):
                    await source.fetch('nope')
            finally:
                await source.client.aclose()

        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
