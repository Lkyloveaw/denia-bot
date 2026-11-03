# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""插件 Runtime 接线：Jian 基线/去重/过滤，EQSC 基线/更新/停编。"""

import asyncio
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from tests._bootstrap import ensure_nonebot
except ImportError:
    from _bootstrap import ensure_nonebot

ensure_nonebot()

import disaster_config as config_mod
import plugins.disaster_warning as plugin
from nonebot.exception import FinishedException
from tests.test_disaster_china_logic import JIAN_TSUNAMI, JIAN_WEATHER, EQSC_TYPHOON
from tests.test_disaster_weather_regions import LOCAL, weather


class DummyWolfx:
    async def close(self):
        pass


class FakeEqsc:
    def __init__(self, items):
        self.items = items
        self.closed = False

    async def fetch_typhoons(self):
        return copy.deepcopy(self.items)

    async def close(self):
        self.closed = True


class ChinaWiringTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.pushed = []
        self.images = []

        async def push(text, config, image=None):
            self.pushed.append(text)
            self.images.append(image)
            return True

        self.runtime = plugin.Runtime(
            base=self.base,
            source=DummyWolfx(),
            push=push,
        )
        self.runtime.prepare()
        config = config_mod.load_config(self.runtime.config_path)
        config['push']['enabled'] = True
        config['push']['group_id'] = '123456789'
        config['sources']['jian']['enabled'] = True
        config['sources']['eqsc']['enabled'] = True
        config['render']['enabled'] = False  # 接线测试不访问地图/渲染服务。
        config['filters']['weather_min_color'] = '蓝色'
        config_mod.save_config(config, self.runtime.config_path)
        self.runtime.reload_config()

    def tearDown(self):
        self.temp.cleanup()

    def message(self, msg_type, data):
        return {'type': msg_type, 'Data': data}

    def test_all_baseline_then_new_weather_is_pushed_once(self):
        async def scenario():
            await self.runtime.handle_jian_message(
                self.message('all', {'weather': [JIAN_WEATHER]})
            )
            self.assertEqual(self.pushed, [])
            await self.runtime.handle_jian_message(self.message('weather', JIAN_WEATHER))
            self.assertEqual(self.pushed, [], '首帧 all 里的预警不应补推')

            new_alert = dict(
                JIAN_WEATHER,
                id='320312_20260729130000',
                title='铜山区发布暴雨蓝色预警',
                headline='铜山区气象台发布暴雨蓝色预警',
                description='预计未来 6 小时铜山区将出现暴雨。',
                effective='2026-07-29 13:00:00',
            )
            await self.runtime.handle_jian_message(self.message('weather', new_alert))
            self.assertEqual(len(self.pushed), 1)
            self.assertIn('暴雨蓝色预警', self.pushed[0])
            await self.runtime.handle_jian_message(self.message('weather', new_alert))
            self.assertEqual(len(self.pushed), 1, '同一预警不应重复推送')

        asyncio.run(scenario())

    def test_weather_color_and_region_filters(self):
        async def scenario():
            config = self.runtime.reload_config()
            config['filters']['weather_min_color'] = '橙色'
            config_mod.save_config(config, self.runtime.config_path)
            await self.runtime.handle_jian_message(self.message('weather', JIAN_WEATHER))
            self.assertEqual(self.pushed, [], '黄色低于橙色阈值应被过滤')

            config = self.runtime.reload_config()
            config['filters']['weather_min_color'] = '蓝色'
            config['local']['enabled'] = True
            config['local']['province'] = '浙江省'
            config['local']['city'] = '杭州市'
            config_mod.save_config(config, self.runtime.config_path)
            out_of_region = dict(JIAN_WEATHER, id='320312_20260729140000')
            await self.runtime.handle_jian_message(self.message('weather', out_of_region))
            self.assertEqual(self.pushed, [], '外省预警应被本地过滤')

        asyncio.run(scenario())

    def test_tsunami_pushed_and_deduped(self):
        async def scenario():
            await self.runtime.handle_jian_message(self.message('nmefc-tsunami', JIAN_TSUNAMI))
            self.assertEqual(len(self.pushed), 1)
            self.assertIn('海啸黄色警报', self.pushed[0])
            await self.runtime.handle_jian_message(self.message('nmefc-tsunami', JIAN_TSUNAMI))
            self.assertEqual(len(self.pushed), 1)

        asyncio.run(scenario())

    def test_incomplete_foreign_weather_is_recorded_but_never_rendered_or_pushed(self):
        async def scenario():
            config = self.runtime.reload_config()
            config['local'] = dict(LOCAL)
            config_mod.save_config(config, self.runtime.config_path)
            render = AsyncMock(return_value=None)
            with patch.object(self.runtime, '_render_china', render):
                for province in ('海南省', '陕西省'):
                    await self.runtime.handle_jian_message(self.message('weather', {
                        'id': province, 'title': f'{province}发布大风黄色预警',
                        'province': province, 'city': '', 'district': '',
                    }))
                self.assertEqual(self.pushed, [])
                render.assert_not_awaited()
                rows = self.runtime.store.recent(10)
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(row['status'] == 'filtered' and row['push_ok'] == 0
                                    for row in rows))
                await self.runtime.handle_jian_message(self.message('weather', {
                    'id': '330108_20261102120000', 'title': '滨江区发布大风黄色预警',
                    'province': '浙江省', 'city': '杭州市', 'district': '滨江区',
                }))
                self.assertEqual(len(self.pushed), 1)
                render.assert_awaited_once()
        asyncio.run(scenario())

    def test_weather_query_reloads_config_and_filters_before_limit(self):
        self.runtime.weather = [weather('海南省'), weather('浙江省', '杭州市', '滨江区')]
        config = self.runtime.reload_config()
        config['local'] = dict(LOCAL)
        config_mod.save_config(config, self.runtime.config_path)
        event = Mock()
        event.get_plaintext.return_value = '.气象预警 1'
        finish = AsyncMock(side_effect=FinishedException)
        with patch.object(plugin, '_runtime', self.runtime), patch.object(plugin.matcher, 'finish', finish):
            with self.assertRaises(FinishedException):
                asyncio.run(plugin._handle_command(event))
        text = finish.await_args.args[0]
        self.assertIn('滨江区', text)
        self.assertNotIn('海南', text)

    def test_weather_query_explains_no_matching_alerts(self):
        self.runtime.weather = [weather('海南省')]
        config = self.runtime.reload_config()
        config['local'] = dict(LOCAL)
        config_mod.save_config(config, self.runtime.config_path)
        event = Mock()
        event.get_plaintext.return_value = '.气象预警'
        finish = AsyncMock(side_effect=FinishedException)
        with patch.object(plugin, '_runtime', self.runtime), patch.object(plugin.matcher, 'finish', finish):
            with self.assertRaises(FinishedException):
                asyncio.run(plugin._handle_command(event))
        self.assertIn('没有符合当前地区和等级设置', finish.await_args.args[0])

    def test_typhoon_baseline_update_stop(self):
        async def scenario():
            source = FakeEqsc([EQSC_TYPHOON])
            await self.runtime.poll_typhoons(source)
            self.assertEqual(self.pushed, [], '首轮台风只做基线')
            self.assertEqual(len(self.runtime.typhoons), 1)

            await self.runtime.poll_typhoons(source)
            self.assertEqual(self.pushed, [], '没有新轨迹不应重复推送')

            changed = copy.deepcopy(EQSC_TYPHOON)
            changed['historyTrack'].append({
                'time': '2025/09/23 11:00:00', 'typeNameCN': '台风',
                'latitude': 21.0, 'longitude': 117.0, 'pressure': 950,
                'windSpeed': 45, 'directionCN': '西北', 'speed': '16',
            })
            source.items = [changed]
            await self.runtime.poll_typhoons(source)
            self.assertEqual(len(self.pushed), 1)
            self.assertIn('台风', self.pushed[0])
            self.assertIn('桦加沙', self.pushed[0])
            self.assertNotIn('达妮娅', self.pushed[0], '台风首次推送应为中性官方')

            stopped = copy.deepcopy(changed)
            stopped['isActive'] = False
            source.items = [stopped]
            await self.runtime.poll_typhoons(source)
            self.assertEqual(len(self.pushed), 2)
            self.assertIn('停编', self.pushed[-1])
            self.assertIn('达妮娅', self.pushed[-1], '后续停编通知应为达妮娅')

        asyncio.run(scenario())

    def test_weather_first_neutral_then_update_character(self):
        async def scenario():
            first = dict(
                JIAN_WEATHER,
                id='320312_20260729150000',
                title='铜山区发布暴雨蓝色预警',
                headline='铜山区气象台发布暴雨蓝色预警',
                description='预计未来 6 小时铜山区将出现暴雨。',
                effective='2026-07-29 15:00:00',
            )
            await self.runtime.handle_jian_message(self.message('weather', first))
            self.assertEqual(len(self.pushed), 1)
            self.assertNotIn('达妮娅', self.pushed[0], '首报应为中性官方')
            rows = self.runtime.store.recent(10)
            self.assertTrue(any(
                row['kind'] == 'weather' and row['status'] == 'pushed' for row in rows
            ), '气象事件应写入事件库')

            update = dict(
                first,
                description='更新：预计未来 3 小时铜山区降雨增强，请注意防范。',
                effective='2026-07-29 15:30:00',
            )
            await self.runtime.handle_jian_message(self.message('weather', update))
            self.assertEqual(len(self.pushed), 2)
            self.assertIn('达妮娅', self.pushed[1], '同一预警的后续更新应为达妮娅')

        asyncio.run(scenario())

    def test_status_counts(self):
        async def scenario():
            await self.runtime.handle_jian_message(self.message('weather', JIAN_WEATHER))
            await self.runtime.handle_jian_message(self.message('nmefc-tsunami', JIAN_TSUNAMI))
            source = FakeEqsc([EQSC_TYPHOON])
            await self.runtime.poll_typhoons(source)
            status = self.runtime.status()
            self.assertEqual(status['jian']['weather'], 1)
            self.assertEqual(status['jian']['tsunami'], 1)
            self.assertEqual(status['eqsc']['cached'], 1)
            self.assertTrue(status['jian']['configured'] is False)
            text = plugin._format_china_status(self.runtime)
            self.assertIn('Jian', text)
            self.assertIn('EQSC', text)

        asyncio.run(scenario())

    def test_parse_limit(self):
        self.assertEqual(plugin._parse_limit('9', 3), 9)
        self.assertEqual(plugin._parse_limit('abc', 3), 3)
        self.assertEqual(plugin._parse_limit('99', 3), 20)


if __name__ == '__main__':
    unittest.main()
