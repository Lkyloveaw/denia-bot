# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""灾害预警配置用例。

重点：默认值补齐不共享、令牌不回显、留空保持、显式清除、非法值不写入。
全部在临时目录里跑，不碰真实的 `data/`。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disaster_config as cfg


class DisasterConfigTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'disaster_warning.json'

    def tearDown(self):
        self.folder.cleanup()

    def test_defaults_are_merged_and_not_shared(self):
        first = cfg.load_config(self.path)
        self.assertTrue(first['enabled'])
        self.assertFalse(first['local']['enabled'], '经纬度没填时本地过滤默认关闭')
        first['sources']['jian']['token'] = 'dirty'
        second = cfg.load_config(self.path)
        self.assertEqual(second['sources']['jian']['token'], '',
                         '默认值必须是深拷贝，不能被上一个实例改脏')

    def test_roundtrip(self):
        patch = {
            'enabled': False,
            'sources': {
                'jian': {'enabled': False, 'token': 'rt_secret', 'token_expires_at': '2027-05-01'},
                'eqsc': {'enabled': True, 'token': 'arh_secret', 'token_expires_at': '2027-05-01', 'poll_seconds': 60},
                'earthquake_free': {'enabled': False},
            },
            'render': {'enabled': True, 'remote_url': 'ws://127.0.0.1:3000', 'timeout_seconds': 15, 'container_name': 'pw-test'},
            'local': {'enabled': True, 'province': '浙江省', 'city': '杭州市', 'latitude': 30.27, 'longitude': 120.15},
            'push': {'enabled': True, 'group_id': '123456789', 'owner_id': '987654321'},
            'filters': {'weather_min_color': '橙色', 'typhoon_max_distance_km': 800, 'tsunami_min_level': '黄色', 'earthquake_min_magnitude': 4.0, 'earthquake_max_distance_km': 500},
        }
        cfg.save_config(cfg.apply_patch(cfg.load_config(self.path), patch), self.path)
        saved = cfg.load_config(self.path)
        self.assertFalse(saved['enabled'])
        self.assertEqual(saved['sources']['jian']['token'], 'rt_secret')
        self.assertEqual(saved['sources']['eqsc']['poll_seconds'], 60)
        self.assertEqual(saved['local']['city'], '杭州市')
        self.assertEqual(saved['push']['group_id'], '123456789')
        self.assertEqual(saved['filters']['tsunami_min_level'], '黄色')
        self.assertEqual(saved['filters']['earthquake_max_distance_km'], 500)

    def test_public_view_masks_tokens(self):
        config = cfg.apply_patch(cfg.load_config(self.path), {
            'sources': {
                'jian': {'token': 'rt_secret', 'token_expires_at': '2027-05-01'},
                'eqsc': {'token': 'arh_secret'},
            },
        })
        view = cfg.public_view(config)
        self.assertTrue(view['sources']['jian']['token_configured'])
        self.assertTrue(view['sources']['eqsc']['token_configured'])
        self.assertNotIn('token', view['sources']['jian'])
        self.assertNotIn('token', view['sources']['eqsc'])
        self.assertEqual(view['sources']['jian']['token_expires_at'], '2027-05-01')
        self.assertIn('weather_colors', view['options'])
        self.assertIn('tsunami_levels', view['options'])
        self.assertEqual(view['filters']['earthquake_max_distance_km'], 1000)

    def test_multiple_provinces_normalize_and_roundtrip(self):
        config = cfg.apply_patch(cfg.load_config(self.path), {
            'local': {'province': ' 浙江省，江苏省\n海南省、浙江省;河北省；  '},
        })
        cfg.save_config(config, self.path)
        view = cfg.public_view(cfg.load_config(self.path))
        self.assertEqual(view['local']['province'], '浙江省、江苏省、海南省、河北省')
        self.assertEqual(cfg.apply_patch(config, {'local': {'province': '，\n、'}})['local']['province'], '')

    def test_multiple_provinces_can_exceed_old_single_name_limit(self):
        names = '浙江省、江苏省、海南省、河北省、河南省、山东省、山西省、广东省、广西壮族自治区、内蒙古自治区'
        config = cfg.apply_patch(cfg.load_config(self.path), {'local': {'province': names}})
        self.assertEqual(cfg.public_view(config)['local']['province'], names)

    def test_province_limits_apply_to_each_keyword_and_entire_input(self):
        for value in (['浙江省'], '省' * 41, '浙江省、' * 151):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    cfg.apply_patch(cfg.load_config(self.path), {'local': {'province': value}})

    def test_city_district_list_normalizes_and_accepts_more_than_40_characters(self):
        names = '浙江省杭州市滨江区、浙江省杭州市上城区、浙江省杭州市西湖区、浙江省杭州市萧山区、浙江省杭州市余杭区'
        self.assertGreater(len(names), 40)
        config = cfg.apply_patch(cfg.load_config(self.path), {
            'local': {'city': names.replace('、', '，') + '\n浙江省杭州市滨江区'},
        })
        cfg.save_config(config, self.path)
        self.assertEqual(cfg.public_view(cfg.load_config(self.path))['local']['city'], names)
        self.assertEqual(cfg.apply_patch(config, {'local': {'city': '，\n、'}})['local']['city'], '')
        with self.assertRaises(ValueError):
            cfg.apply_patch(config, {'local': {'city': names * 20}})

    def test_blank_token_keeps_and_clear_removes(self):
        config = cfg.apply_patch(cfg.load_config(self.path), {
            'sources': {'jian': {'token': 'rt_secret'}},
        })
        config = cfg.apply_patch(config, {'sources': {'jian': {'token': ''}}})
        self.assertEqual(config['sources']['jian']['token'], 'rt_secret',
                         '面板留空表示保持原值')
        config = cfg.apply_patch(config, {'clear_jian_token': True})
        self.assertEqual(config['sources']['jian']['token'], '')
        self.assertFalse(cfg.public_view(config)['sources']['jian']['token_configured'])

    def test_unknown_fields_are_ignored(self):
        config = cfg.apply_patch(cfg.load_config(self.path), {
            'sources': {'jian': {'token': 'rt_secret', 'token_type': 'bearer'}},
            'nonsense': {'enabled': True},
        })
        self.assertNotIn('nonsense', config)
        self.assertNotIn('token_type', config['sources']['jian'])

    def test_invalid_values_raise(self):
        cases = [
            {'local': {'latitude': 91}},
            {'local': {'longitude': 181}},
            {'local': {'city': 'x' * 41}},
            {'sources': {'eqsc': {'poll_seconds': 5}}},
            {'sources': {'jian': {'token_expires_at': '2027/05/01'}}},
            {'render': {'remote_url': 'ftp://example.com'}},
            {'render': {'timeout_seconds': 0}},
            {'push': {'group_id': 'abc'}},
            {'filters': {'weather_min_color': '紫色'}},
            {'filters': {'tsunami_min_level': '巨大'}},
            {'filters': {'earthquake_min_magnitude': 11}},
            {'filters': {'earthquake_max_distance_km': 5001}},
        ]
        for patch in cases:
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):
                    cfg.apply_patch(cfg.load_config(self.path), patch)

    def test_corrupt_file_and_bad_sections_fall_back(self):
        self.path.write_text('{not json', encoding='utf-8')
        config = cfg.load_config(self.path)
        self.assertTrue(config['enabled'])
        self.assertIn('sources', config)
        self.path.write_text(json.dumps({'sources': 'oops', 'render': 1, 'filters': []}), encoding='utf-8')
        config = cfg.load_config(self.path)
        self.assertIn('jian', config['sources'])
        self.assertIn('remote_url', config['render'])
        self.assertIn('weather_min_color', config['filters'])
        self.assertIn('options', cfg.public_view(config))


if __name__ == '__main__':
    unittest.main()
