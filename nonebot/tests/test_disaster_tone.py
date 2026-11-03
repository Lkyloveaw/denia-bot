# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""首报/后续口吻判定用例。"""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disaster_tone as tone
from disaster_logic import sample_event
from tests.test_disaster_china_logic import EQSC_TYPHOON, JIAN_TSUNAMI, JIAN_WEATHER
import disaster_china_logic as china


class ToneTest(unittest.TestCase):
    def test_eew_report_one_neutral_later_character(self):
        first = sample_event()
        self.assertEqual(tone.earthquake_tone(first), tone.NEUTRAL)
        later = replace(sample_event(), report_num=3)
        self.assertEqual(tone.earthquake_tone(later), tone.CHARACTER)

    def test_cenc_list_is_neutral(self):
        event = replace(sample_event(), source='cenc', report_num=1)
        self.assertEqual(tone.earthquake_tone(event), tone.NEUTRAL)

    def test_weather_identity_first_then_update(self):
        alert = china.parse_jian_weather(JIAN_WEATHER)
        first, key = tone.tone_for('weather', alert, {})
        self.assertEqual(first, tone.NEUTRAL)
        self.assertIn('voice:weather:', key)
        later, _ = tone.tone_for('weather', alert, {key: True})
        self.assertEqual(later, tone.CHARACTER)

    def test_tsunami_and_typhoon_identity(self):
        tsunami = china.parse_jian_tsunami(JIAN_TSUNAMI)
        typhoon = china.parse_eqsc_typhoon_item(EQSC_TYPHOON)
        for kind, item in (('tsunami', tsunami), ('typhoon', typhoon)):
            first, key = tone.tone_for(kind, item, {})
            self.assertEqual(first, tone.NEUTRAL)
            later, _ = tone.tone_for(kind, item, {key: True})
            self.assertEqual(later, tone.CHARACTER)


if __name__ == '__main__':
    unittest.main()
