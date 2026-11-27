"""Bilibili search must use WBI plus buvid, without leaking parser login."""
import hashlib
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bilibili.client import BiliClient
from bilibili.client import wbi_mixin_key, wbi_sign
from bilibili.model import BiliError

IMG = 'https://i0.hdslb.com/bfs/wbi/' + 'a' * 32 + '.png'
SUB = 'https://i0.hdslb.com/bfs/wbi/' + 'b' * 32 + '.png'


def reply(payload, status=200, headers=None):
    return httpx.Response(
        status,
        content=json.dumps(payload, ensure_ascii=False).encode(),
        headers={'Content-Type': 'application/json', **(headers or {})},
    )


class BilibiliSearchTest(unittest.IsolatedAsyncioTestCase):
    def transport(self, handler):
        return httpx.MockTransport(handler)

    async def test_search_uses_wbi_and_buvid_without_login_cookie(self):
        seen = {}

        def handler(request):
            if request.url.path == '/x/frontend/finger/spi':
                return reply({'code': 0, 'data': {'b_3': 'buvid3-value', 'b_4': 'buvid4-value'}})
            if request.url.path == '/x/web-interface/nav':
                seen['nav_cookie'] = request.headers.get('cookie', '')
                return reply({'code': -101, 'data': {'wbi_img': {'img_url': IMG, 'sub_url': SUB}}})
            if request.url.path == '/x/web-interface/wbi/search/type':
                query = parse_qs(request.url.query.decode())
                seen['query'] = query
                seen['search_cookie'] = request.headers.get('cookie', '')
                seen['ua'] = request.headers.get('user-agent', '')
                return reply({'code': 0, 'data': {'result': [
                    {'bvid': 'BV1xx411c7mD', 'title': '<em>守岸人</em>', 'author': '某UP',
                     'description': '简介', 'is_pay': 0},
                    {'bvid': 'BV1xx411c7mE', 'title': '付费', 'author': 'UP', 'is_pay': 1},
                    {'title': '付费无标识', 'author': 'UP', 'is_pay': 1},
                ]}})
            raise AssertionError(request.url.path)

        client = BiliClient(transport=self.transport(handler),
                            cookie_provider=lambda: {'SESSDATA': 'secret', 'bili_jct': 'jct'})
        try:
            rows = await client.search('守岸人')
        finally:
            await client.close()

        self.assertEqual(len(rows), 1)
        self.assertIn('守岸人', rows[0])
        self.assertIn('BV1xx411c7mD', rows[0])
        self.assertEqual(seen['query']['keyword'], ['守岸人'])
        self.assertEqual(seen['query']['search_type'], ['video'])
        self.assertRegex(seen['query']['w_rid'][0], r'^[0-9a-f]{32}$')
        self.assertTrue(seen['query']['wts'][0].isdigit())
        self.assertIn('buvid3-value', seen['nav_cookie'])
        self.assertIn('buvid3-value', seen['search_cookie'])
        self.assertNotIn('secret', seen['search_cookie'])
        self.assertNotIn('SESSDATA', seen['search_cookie'])
        self.assertTrue(seen['ua'].startswith('Mozilla/'))

    async def test_search_412_marks_source_restricted(self):
        def handler(request):
            if request.url.path == '/x/frontend/finger/spi':
                return reply({'code': 0, 'data': {'b_3': 'b3', 'b_4': 'b4'}})
            if request.url.path == '/x/web-interface/nav':
                return reply({'code': -101, 'data': {'wbi_img': {'img_url': IMG, 'sub_url': SUB}}})
            return httpx.Response(412, content=b'blocked')

        client = BiliClient(transport=self.transport(handler))
        try:
            with self.assertRaises(BiliError) as caught:
                await client.search('守岸人')
        finally:
            await client.close()
        self.assertEqual(caught.exception.code, 'REMOTE_RESTRICTED')
        self.assertTrue(client.restricted)

    async def test_wbi_sign_is_deterministic(self):
        key = '0123456789abcdef0123456789abcdef'
        signed = wbi_sign(
            {'search_type': 'video', 'keyword': '守岸人', 'page': 1, 'page_size': 5},
            key, now=1700000000,
        )
        expected = {
            'search_type': 'video', 'keyword': '守岸人', 'page': '1',
            'page_size': '5', 'wts': '1700000000',
        }
        query = urlencode(sorted(expected.items()))
        self.assertEqual(signed['w_rid'], hashlib.md5((query + key).encode()).hexdigest())
        self.assertEqual(wbi_mixin_key(IMG, SUB), ''.join(
            ('a' * 32 + 'b' * 32)[i] for i in
            (46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
             27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13)
        ))


if __name__ == '__main__':
    unittest.main()
