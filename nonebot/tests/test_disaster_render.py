# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""远程渲染客户端与卡片 HTML 用例。"""

import asyncio
import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disaster_china_logic as china
import disaster_logic as logic
import disaster_render as render
from tests.test_disaster_china_logic import EQSC_TYPHOON, JIAN_TSUNAMI, JIAN_WEATHER


def event_with(place='样例地点'):
    return logic.EarthquakeEvent(
        source='cenc_eew', event_id='E1', report_num=2,
        title='中国地震预警', occurred_at='2026-10-31 12:00:00',
        place=place, latitude=30.0, longitude=120.0,
        magnitude=5.2, depth=10.0, intensity='5.0',
        timezone_label='北京时间',
    )


from io import BytesIO
from PIL import Image
_buffer = BytesIO()
Image.new('RGB', (900, 1200), 'white').save(_buffer, 'PNG')
PNG = _buffer.getvalue()

class CardTest(unittest.TestCase):
    def test_card_contains_fields_and_no_external_assets(self):
        text = render.card_html(event_with(), {'local': {'enabled': False}})
        self.assertIn('M5.2', text)
        self.assertIn('样例地点', text)
        self.assertIn('最大烈度', text)
        self.assertNotIn('<script', text)
        self.assertNotIn('http://', text)
        self.assertNotIn('https://', text)

    def test_card_escapes_untrusted_place(self):
        text = render.card_html(event_with('<script>alert(1)</script>'), {})
        self.assertIn('&lt;script&gt;', text)
        self.assertNotIn('<script>alert(1)</script>', text)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class RenderClientTest(unittest.TestCase):
    def test_render_png_success(self):
        async def run():
            captured = {}

            def handler(request):
                captured.update(json.loads(request.content.decode('utf-8')))
                return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})

            client = client_for(handler)
            try:
                png = await render.render_png('http://127.0.0.1:3000', '<html></html>',
                                              width=800, height=1000, timeout=10, client=client)
                self.assertTrue(png.startswith(b'\x89PNG'))
                self.assertEqual(captured['width'], 800)
                self.assertEqual(captured['height'], 1000)
                self.assertEqual(captured['timeout_ms'], 10000)
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_render_png_http_error(self):
        async def run():
            client = client_for(lambda request: httpx.Response(500, text='down'))
            try:
                with self.assertRaises(render.RenderError):
                    await render.render_png('http://127.0.0.1:3000', '<html></html>', client=client)
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_render_png_non_image(self):
        async def run():
            client = client_for(lambda request: httpx.Response(200, json={'ok': True}))
            try:
                with self.assertRaises(render.RenderError):
                    await render.render_png('http://127.0.0.1:3000', '<html></html>', client=client)
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_invalid_base_url(self):
        async def run():
            with self.assertRaises(render.RenderError):
                await render.render_png('ftp://example.com', '<html></html>')

        asyncio.run(run())

    def test_health(self):
        async def run():
            client = client_for(lambda request: httpx.Response(200, json={'ok': True, 'ready': True, 'renders': 2}))
            try:
                data = await render.health('http://127.0.0.1:3000', client=client)
                self.assertTrue(data['ready'])
                self.assertEqual(data['renders'], 2)
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_render_event_disabled_and_enabled(self):
        async def run():
            self.assertIsNone(await render.render_event(event_with(), {'render': {'enabled': False}}))

            client = client_for(lambda request: httpx.Response(200, content=PNG, headers={'content-type': 'image/png'}))
            try:
                self.assertEqual(await render.render_event(event_with(),
                    {'render': {'enabled': True, 'remote_url': ''}}, client=client), PNG)
                png = await render.render_event(event_with(), {
                    'render': {'enabled': True, 'remote_url': 'http://127.0.0.1:3000', 'timeout_seconds': 5},
                }, client=client)
                self.assertEqual(png, PNG)
            finally:
                await client.aclose()

        asyncio.run(run())


class BulletinTest(unittest.TestCase):
    def test_weather_type_and_inline_icon_replace_color_only_visual(self):
        alert = china.parse_jian_weather(dict(JIAN_WEATHER, title='铜山区发布雷电黄色预警', headline='', weather_type=''))
        text = render.bulletin_html('weather', alert)
        self.assertIn('aria-label="雷电类型示意"', text)
        self.assertIn('预警行政区域示意', text)
        self.assertIn('暂无行政边界', text)
        self.assertIn('2026-07-29 12:41:00', text)

    def test_weather_long_body_is_bounded_and_full_text_notice_shown(self):
        alert = china.parse_jian_weather(dict(JIAN_WEATHER, description='这是一条长预警内容。' * 300))
        text = render.bulletin_html('weather', alert)
        self.assertIn('完整内容见随附文字', text)
        self.assertNotIn(alert.description, text)

    def test_weather_bulletin_neutral_and_character(self):
        alert = china.parse_jian_weather(JIAN_WEATHER)
        neutral = render.bulletin_html('weather', alert, {}, 'neutral')
        character = render.bulletin_html('weather', alert, {}, 'character')
        self.assertIn('强对流', neutral)
        self.assertNotIn('达妮娅', neutral)
        self.assertIn('达妮娅', character)
        self.assertIn('公开数据', neutral)
        self.assertNotIn('http://', neutral)

    def test_tsunami_and_typhoon_bulletin(self):
        tsunami = china.parse_jian_tsunami(JIAN_TSUNAMI)
        typhoon = china.parse_eqsc_typhoon_item(EQSC_TYPHOON)
        self.assertIn('海啸黄色警报', render.bulletin_html('tsunami', tsunami, {}, 'neutral'))
        self.assertIn('达妮娅', render.bulletin_html('tsunami', tsunami, {}, 'character'))
        self.assertIn('桦加沙', render.bulletin_html('typhoon', typhoon, {}, 'neutral'))
        self.assertIn('达妮娅', render.bulletin_html('typhoon', typhoon, {}, 'character'))
        self.assertIn('停编', render.bulletin_html('typhoon', typhoon, {}, 'character', stopped=True))

    def test_earthquake_bulletin_character(self):
        event = logic.sample_event()
        neutral = render.card_html(event, {})
        character = render.card_html(event, {}, 'character')
        self.assertIn('M5.2', neutral)
        self.assertNotIn('达妮娅', neutral)
        self.assertIn('达妮娅', character)
        self.assertIn('最大烈度', neutral)

    def test_render_bulletin_success(self):
        async def run():
            alert = china.parse_jian_weather(JIAN_WEATHER)
            client = client_for(lambda request: httpx.Response(
                200, content=PNG, headers={'content-type': 'image/png'},
            ))
            try:
                png = await render.render_bulletin(
                    'weather', alert,
                    {'render': {'enabled': True, 'remote_url': 'http://127.0.0.1:3000'}},
                    'neutral', client=client,
                )
                self.assertEqual(png, PNG)
            finally:
                await client.aclose()

        asyncio.run(run())

    def test_render_bulletin_disabled(self):
        async def run():
            alert = china.parse_jian_weather(JIAN_WEATHER)
            self.assertIsNone(await render.render_bulletin(
                'weather', alert, {'render': {'enabled': False}},
            ))
            client = client_for(lambda request: httpx.Response(
                200, content=PNG, headers={'content-type': 'image/png'}))
            async with client:
                self.assertEqual(await render.render_bulletin(
                    'weather', alert, {'render': {'enabled': True, 'remote_url': ''}}, client=client,
                ), PNG)

        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
