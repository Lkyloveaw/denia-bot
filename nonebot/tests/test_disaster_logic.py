# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""地震解析、过滤与格式化纯逻辑用例。"""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disaster_logic as logic

CENC_EEW = {
    'ID': 'b4zex3fm5wwyy',
    'EventID': '202610300153.0001',
    'ReportTime': '2026-10-30 01:53:54',
    'ReportNum': 1,
    'OriginTime': '2026-10-30 01:53:54',
    'HypoCenter': '新疆博尔塔拉州精河县',
    'Latitude': 44.221,
    'Longitude': 82.66,
    'Magnitude': 4.2,
    'Depth': 5,
    'MaxIntensity': 5.4,
}

CENC_LIST = {
    'No1': {
        'type': 'reviewed', 'EventID': 'CD.20261031030557.414',
        'time': '2026-10-31 03:05:57', 'ReportTime': '2026-10-31 03:13:07',
        'location': '西藏林芝市墨脱县', 'placeName': '西藏林芝市墨脱县',
        'magnitude': '3.1', 'depth': '10', 'latitude': '29.36',
        'longitude': '95.55', 'intensity': '4',
    },
    'No2': {
        'type': 'automatic', 'EventID': 'AU.20261031030557.000',
        'time': '2026-10-31 03:05:57', 'placeName': '自动测定',
        'magnitude': '3.0', 'depth': '10', 'latitude': '29.3',
        'longitude': '95.5', 'intensity': '3',
    },
}

JMA_EEW = {
    'EventID': '20261031093302',
    'Serial': 4,
    'AnnouncedTime': '2026/10/31 09:34:11',
    'OriginTime': '2026/10/31 09:32:51',
    'Hypocenter': '八丈島東方沖',
    'Latitude': 33.5,
    'Longitude': 141.5,
    'Magunitude': 4.8,
    'Magnitude': 4.8,
    'Depth': 60,
    'MaxIntensity': '1',
    'isSea': True,
    'isTraining': False,
    'isAssumption': False,
    'isWarn': False,
    'isFinal': True,
    'isCancel': False,
}

JMA_LIST = {
    'No1': {
        'Title': '震源・震度情報', 'EventID': '20261031061524',
        'time': '2026/10/31 06:15', 'time_full': '2026/10/31 06:15:00',
        'location': '種子島近海', 'magnitude': '4.0', 'shindo': '1',
        'depth': '30km', 'latitude': '30.3', 'longitude': '131.2',
    },
}


class ParseTest(unittest.TestCase):
    def test_parse_cenc_eew(self):
        event = logic.parse_cenc_eew(CENC_EEW)
        self.assertIsNotNone(event)
        self.assertEqual(event.source, 'cenc_eew')
        self.assertEqual(event.event_id, '202610300153.0001')
        self.assertEqual(event.report_num, 1)
        self.assertEqual(event.place, '新疆博尔塔拉州精河县')
        self.assertAlmostEqual(event.magnitude, 4.2)
        self.assertAlmostEqual(event.depth, 5)
        self.assertEqual(event.intensity, '5.4')
        self.assertEqual(event.key, 'cenc_eew:202610300153.0001:1')

    def test_parse_cenc_list_skips_automatic(self):
        events = logic.parse_cenc_list(CENC_LIST)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_id, 'CD.20261031030557.414')
        self.assertEqual(event.place, '西藏林芝市墨脱县')
        self.assertAlmostEqual(event.magnitude, 3.1)
        self.assertTrue(event.is_final)

    def test_parse_jma_eew(self):
        event = logic.parse_jma_eew(JMA_EEW)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_id, '20261031093302')
        self.assertEqual(event.report_num, 4)
        self.assertAlmostEqual(event.magnitude, 4.8)
        self.assertEqual(event.intensity, '1')
        self.assertTrue(event.is_final)

    def test_parse_jma_eew_skips_cancel_training_assumption(self):
        for flag in ('isCancel', 'isTraining', 'isAssumption'):
            with self.subTest(flag=flag):
                data = dict(JMA_EEW)
                data[flag] = True
                self.assertIsNone(logic.parse_jma_eew(data))

    def test_parse_jma_list(self):
        events = logic.parse_jma_list(JMA_LIST)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].place, '種子島近海')
        self.assertAlmostEqual(events[0].depth, 30)
        self.assertEqual(events[0].intensity, '1')

    def test_parse_float_handles_units_and_unknown(self):
        self.assertEqual(logic.parse_float('M4.8'), 4.8)
        self.assertEqual(logic.parse_float('30km'), 30)
        self.assertIsNone(logic.parse_float('调查中'))
        self.assertIsNone(logic.parse_float(None))


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.event = logic.EarthquakeEvent(
            source='cenc', event_id='x', report_num=1, title='t',
            occurred_at='', place='', latitude=30.0, longitude=120.0,
            magnitude=5.0,
        )

    def config(self, **local):
        base = {
            'local': {'enabled': True, 'latitude': 30.27, 'longitude': 120.15, **local},
            'filters': {'earthquake_min_magnitude': 4.5, 'earthquake_max_distance_km': 50},
        }
        return base

    def test_distance_and_filters(self):
        self.assertTrue(logic.within_filters(self.event, self.config()))
        far = self.config(latitude=31.5, longitude=121.5)
        self.assertFalse(logic.within_filters(self.event, far))
        too_strong = self.config()
        too_strong['filters']['earthquake_min_magnitude'] = 5.5
        self.assertFalse(logic.within_filters(self.event, too_strong))
        self.assertTrue(logic.within_filters(self.event, {
            'local': {'enabled': False},
            'filters': {'earthquake_min_magnitude': 4.5},
        }))

    def test_unknown_magnitude_passes(self):
        event = logic.EarthquakeEvent(
            source='cenc_eew', event_id='x', report_num=1, title='t',
            occurred_at='', place='', magnitude=None,
        )
        self.assertTrue(logic.within_filters(event, self.config()))

    def test_haversine_is_sane(self):
        self.assertLess(logic.distance_km(30.0, 120.0, 30.0, 120.0), 0.01)
        distance = logic.distance_km(30.0, 120.0, 31.0, 120.0)
        self.assertGreater(distance, 100)
        self.assertLess(distance, 120)


class FormatTest(unittest.TestCase):
    def test_format_cenc_eew(self):
        event = logic.parse_cenc_eew(CENC_EEW)
        text = logic.format_event(event)
        self.assertIn('中国地震预警', text)
        self.assertIn('第 1 报', text)
        self.assertIn('新疆博尔塔拉州精河县', text)
        self.assertIn('M4.2', text)
        self.assertIn('深度：5 km', text)
        self.assertIn('最大烈度：5.4', text)
        self.assertIn('Wolfx', text)
        self.assertIn('仅供参考', text)

    def test_format_jma_eew_marks_final(self):
        event = logic.parse_jma_eew(JMA_EEW)
        text = logic.format_event(event)
        self.assertIn('紧急地震速报', text)
        self.assertIn('第 4 报，最终报', text)
        self.assertIn('最大震度：1', text)

    def test_format_with_local_distance(self):
        event = logic.parse_cenc_eew(CENC_EEW)
        text = logic.format_event(event, {
            'local': {'enabled': True, 'latitude': 30.0, 'longitude': 120.0},
        })
        self.assertIn('距本地：约', text)

    def test_format_list(self):
        events = [logic.parse_cenc_eew(CENC_EEW), logic.parse_jma_eew(JMA_EEW)]
        text = logic.format_list(events, 5)
        self.assertIn('最近 2 条地震', text)
        self.assertIn('CENC EEW', text)
        self.assertIn('JMA EEW', text)

    def test_character_voice_for_later_report(self):
        event = replace(logic.sample_event(), report_num=3)
        text = logic.format_event(event, tone='character')
        self.assertIn('达妮娅', text)
        self.assertIn('M5.2', text)
        self.assertIn('最大烈度', text)

    def test_sample_event_formats(self):
        self.assertIn('中国地震预警', logic.format_event(logic.sample_event()))


if __name__ == '__main__':
    unittest.main()
