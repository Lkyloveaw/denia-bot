# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""中国三类灾害纯逻辑用例。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disaster_china_logic as china


JIAN_WEATHER = {
    'id': '320312_20260729124100',
    'title': '江苏省徐州市铜山区发布强对流黄色预警',
    'headline': '铜山区气象台发布强对流黄色预警[Ⅲ级/较重]',
    'description': '铜山区气象台2026年07月29日12时41分发布强对流黄色预警信号：预计今天午后到上半夜我区部分镇（街道）将出现雷电。',
    'effective': '2026-07-29 12:41:00',
    'weather_type': 'p0000003',
    'province': '江苏省',
    'city': '徐州市',
    'district': '铜山区',
    'adcode': '320312',
    'latitude': 34.1929,
    'longitude': 117.1839,
}

JIAN_TSUNAMI = {
    'id': '20260729120000',
    'title': '海啸黄色警报',
    'level': '黄色',
    'originTime': '2026/07/29 12:00:00',
    'number': 2,
    'latitude': 35.0,
    'longitude': 140.0,
    'depth': 10,
    'magnitude': 7.8,
    'place': '日本本州东岸',
    'orgUnit': '自然资源部海啸预警中心',
    'description': '自然资源部海啸预警中心发布海啸黄色警报。',
    'htmlUrl': 'https://example.invalid/tsunami',
}

EQSC_TYPHOON = {
    'id': '2518',
    'nameEN': 'RAGASA',
    'nameCN': '桦加沙',
    'isActive': True,
    'historyTrack': [
        {
            'time': '2025/09/22 23:00:00', 'typeNameCN': '超强台风',
            'latitude': 19.5, 'longitude': 119.9, 'pressure': 920,
            'windSpeed': 58, 'directionCN': '西北', 'speed': '20',
        },
        {
            'time': '2025/09/23 05:00:00', 'typeNameCN': '强台风',
            'latitude': 20.2, 'longitude': 118.5, 'pressure': 935,
            'windSpeed': 52, 'directionCN': '西北', 'speed': '18',
        },
    ],
    'futureTrack': [
        {
            'time': '2025/09/25 23:00:00', 'typeNameCN': '热带低压',
            'latitude': 21.9, 'longitude': 106.1, 'pressure': 1003, 'windSpeed': 12,
        },
    ],
}


class WeatherTest(unittest.TestCase):
    def test_parse_jian_weather(self):
        alert = china.parse_jian_weather(JIAN_WEATHER)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.alert_id, '320312_20260729124100')
        self.assertEqual(alert.color, '黄色')
        self.assertEqual(alert.issue_at, '2026-07-29 12:41:00')
        self.assertIn('jian:weather:', alert.key)

    def test_parse_data_wrapper_and_list(self):
        alert = china.parse_jian_weather({'Data': JIAN_WEATHER})
        self.assertIsNotNone(alert)
        second = dict(JIAN_WEATHER, id='320312_20260729130000', title='铜山区发布暴雨蓝色预警')
        alerts = china.parse_jian_weather_list({'Data': [JIAN_WEATHER, second]})
        self.assertEqual(len(alerts), 2)

    def test_color_filter(self):
        alert = china.parse_jian_weather(JIAN_WEATHER)
        self.assertTrue(china.within_weather_filters(alert, {
            'filters': {'weather_min_color': '黄色'},
        }))
        self.assertTrue(china.within_weather_filters(alert, {
            'filters': {'weather_min_color': '蓝色'},
        }))
        self.assertFalse(china.within_weather_filters(alert, {
            'filters': {'weather_min_color': '橙色'},
        }))

    def test_region_filter(self):
        alert = china.parse_jian_weather(JIAN_WEATHER)
        self.assertTrue(china.within_weather_filters(alert, {
            'local': {'enabled': True, 'province': '江苏', 'city': '徐州'},
        }))
        self.assertFalse(china.within_weather_filters(alert, {
            'local': {'enabled': True, 'province': '浙江', 'city': '杭州'},
        }))
        self.assertFalse(china.within_weather_filters(alert, {
            'local': {'enabled': True, 'province': '江苏', 'city': '苏州'},
        }))

    def test_format_and_empty(self):
        alert = china.parse_jian_weather(JIAN_WEATHER)
        text = china.format_weather(alert)
        self.assertIn('强对流黄色预警', text)
        self.assertIn('铜山区', text)
        self.assertIn('中国气象局', text)
        self.assertEqual(china.weather_type_name(alert), '强对流')
        character = china.format_weather(alert, tone='character')
        self.assertIn('达妮娅', character)
        self.assertIsNone(china.parse_jian_weather({'id': 'x'}))

    def test_multiple_provinces_are_or_and_city_remains_and(self):
        config = {'local': {'enabled': True, 'province': '浙江省，江苏省\n海南省、河北省', 'city': ''}}
        for province, expected in [('浙江省', True), ('江苏省', True), ('海南省', True), ('河北省', True), ('山东省', False)]:
            with self.subTest(province=province):
                alert = china.parse_jian_weather(dict(JIAN_WEATHER, province=province))
                self.assertEqual(china.within_weather_filters(alert, config), expected)
        config['local']['city'] = '杭州'
        self.assertFalse(china.within_weather_filters(china.parse_jian_weather(JIAN_WEATHER), config))
        config['local']['city'] = '徐州'
        self.assertTrue(china.within_weather_filters(china.parse_jian_weather(JIAN_WEATHER), config))
        config['filters'] = {'weather_min_color': '橙色'}
        self.assertFalse(china.within_weather_filters(china.parse_jian_weather(JIAN_WEATHER), config))

    def test_empty_province_separators_mean_no_region_limit(self):
        self.assertTrue(china.within_weather_filters(china.parse_jian_weather(JIAN_WEATHER), {
            'local': {'enabled': True, 'province': '，、;\n'},
        }))

    def test_attached_weather_text_preserves_full_official_body_in_both_tones(self):
        description = '这是一段用于验证正文保留的文字。' * 80 + '最后一条官方防御提醒。'
        alert = china.parse_jian_weather(dict(JIAN_WEATHER, description=description))
        for tone in ('neutral', 'character'):
            with self.subTest(tone=tone):
                self.assertIn(description, china.format_weather(alert, tone=tone))


class TsunamiTest(unittest.TestCase):
    def test_parse_format_and_level_filter(self):
        alert = china.parse_jian_tsunami(JIAN_TSUNAMI)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.level, '黄色')
        self.assertEqual(alert.number, 2)
        self.assertIn('海啸黄色警报', china.format_tsunami(alert))
        self.assertTrue(china.within_tsunami_filters(alert, {
            'filters': {'tsunami_min_level': '蓝色'},
        }))
        self.assertFalse(china.within_tsunami_filters(alert, {
            'filters': {'tsunami_min_level': '橙色'},
        }))
        self.assertIn('达妮娅', china.format_tsunami(alert, tone='character'))

    def test_cancel_always_passes(self):
        alert = china.parse_jian_tsunami(dict(JIAN_TSUNAMI, level='解除', title='海啸解除通告'))
        self.assertTrue(china.within_tsunami_filters(alert, {
            'filters': {'tsunami_min_level': '红色'},
        }))

    def test_missing_title_returns_none(self):
        self.assertIsNone(china.parse_jian_tsunami({'id': 'x'}))


class TyphoonTest(unittest.TestCase):
    def test_latest_node_and_future(self):
        typhoon = china.parse_eqsc_typhoon_item(EQSC_TYPHOON)
        self.assertIsNotNone(typhoon)
        self.assertEqual(typhoon.typhoon_id, '2518')
        self.assertEqual(typhoon.name_cn, '桦加沙')
        self.assertEqual(typhoon.level, '强台风')
        self.assertEqual(typhoon.latitude, 20.2)
        self.assertEqual(typhoon.pressure, 935)
        self.assertEqual(typhoon.future_level, '热带低压')
        self.assertTrue(typhoon.is_active)

    def test_list_and_string_false(self):
        typhoons = china.parse_eqsc_typhoon_list({
            'typhoon': [dict(EQSC_TYPHOON, isActive='false')],
        })
        self.assertEqual(len(typhoons), 1)
        self.assertFalse(typhoons[0].is_active)

    def test_distance_filter(self):
        typhoon = china.parse_eqsc_typhoon_item(EQSC_TYPHOON)
        near = {
            'local': {'enabled': True, 'latitude': 20.2, 'longitude': 118.5},
            'filters': {'typhoon_max_distance_km': 100},
        }
        self.assertTrue(china.within_typhoon_filters(typhoon, near))
        far = {
            'local': {'enabled': True, 'latitude': 40.0, 'longitude': 100.0},
            'filters': {'typhoon_max_distance_km': 100},
        }
        self.assertFalse(china.within_typhoon_filters(typhoon, far))

    def test_format_and_stopped(self):
        typhoon = china.parse_eqsc_typhoon_item(EQSC_TYPHOON)
        text = china.format_typhoon(typhoon)
        self.assertIn('桦加沙', text)
        self.assertIn('强台风', text)
        self.assertIn('935 hPa', text)
        stopped = china.format_typhoon_stopped(typhoon)
        self.assertIn('停编', stopped)
        self.assertIn('桦加沙', stopped)
        self.assertIn('达妮娅', china.format_typhoon(typhoon, tone='character'))
        self.assertIn('达妮娅', china.format_typhoon_stopped(typhoon, tone='character'))

    def test_no_history_returns_none(self):
        self.assertIsNone(china.parse_eqsc_typhoon_item({'id': '9999', 'historyTrack': []}))


if __name__ == '__main__':
    unittest.main()
