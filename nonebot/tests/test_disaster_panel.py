# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""管理面板「灾害预警」接口用例。

直接调用路由函数，把鉴权换成空实现、路径指到临时目录——验证返回结构、
令牌不回显、留空保持、显式清除和非法值 400，不启动 FastAPI。
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import dashboard
import disaster_config
import disaster_store
from fastapi import HTTPException


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class DisasterPanelTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'disaster_warning.json'
        self.store_path = Path(self.folder.name) / 'disaster_warning.sqlite3'
        self.patches = [
            patch.object(dashboard, 'disaster_config_path', lambda: self.path),
            patch.object(dashboard, 'disaster_store_path', lambda: self.store_path),
            patch.object(dashboard, 'authorized', lambda req, allow_change=False: 'token'),
            patch.object(dashboard, 'mutate', lambda req, allow_change=False: 'token'),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.folder.cleanup()

    def test_get_defaults_masks_tokens(self):
        data = dashboard.get_disaster(None)
        self.assertIn('options', data)
        self.assertIn('weather_colors', data['options'])
        self.assertFalse(data['sources']['jian']['token_configured'])
        self.assertNotIn('token', data['sources']['jian'])
        self.assertNotIn('token', data['sources']['eqsc'])

    def test_post_saves_keeps_and_clears_token(self):
        body = {
            'enabled': True,
            'sources': {
                'jian': {'enabled': True, 'token': 'rt_secret', 'token_expires_at': '2027-05-01'},
                'eqsc': {'enabled': True, 'token': 'arh_secret', 'poll_seconds': 60},
                'earthquake_free': {'enabled': True},
            },
            'local': {'enabled': True, 'province': '浙江省', 'city': '杭州市', 'latitude': 30.27, 'longitude': 120.15},
            'push': {'enabled': True, 'group_id': '123456789', 'owner_id': '987654321'},
        }
        result = asyncio.run(dashboard.set_disaster(FakeRequest(body)))
        self.assertTrue(result['ok'])
        self.assertNotIn('token', result['config']['sources']['jian'])
        self.assertTrue(result['config']['sources']['jian']['token_configured'])

        saved = disaster_config.load_config(self.path)
        self.assertEqual(saved['sources']['jian']['token'], 'rt_secret')
        self.assertEqual(saved['sources']['eqsc']['token'], 'arh_secret')
        self.assertEqual(saved['local']['city'], '杭州市')

        # 留空保持
        asyncio.run(dashboard.set_disaster(FakeRequest({'sources': {'jian': {'token': ''}}})))
        self.assertEqual(disaster_config.load_config(self.path)['sources']['jian']['token'], 'rt_secret')

        # 显式清除
        asyncio.run(dashboard.set_disaster(FakeRequest({'clear_jian_token': True})))
        self.assertEqual(disaster_config.load_config(self.path)['sources']['jian']['token'], '')

    def test_invalid_value_returns_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(dashboard.set_disaster(FakeRequest({'local': {'latitude': 91}})))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn('纬度', str(ctx.exception.detail))

    def test_multiple_provinces_save_and_load_from_panel(self):
        result = asyncio.run(dashboard.set_disaster(FakeRequest({
            'local': {'province': '浙江省\n江苏省，海南省', 'city': ''},
        })))
        self.assertTrue(result['ok'])
        self.assertEqual(dashboard.get_disaster(None)['local']['province'], '浙江省、江苏省、海南省')
        self.assertEqual(disaster_config.load_config(self.path)['local']['province'], '浙江省、江苏省、海南省')

    def test_partial_patch_does_not_drop_other_fields(self):
        asyncio.run(dashboard.set_disaster(FakeRequest({'push': {'group_id': '123456789'}})))
        saved = disaster_config.load_config(self.path)
        self.assertEqual(saved['push']['group_id'], '123456789')
        self.assertEqual(saved['sources']['eqsc']['poll_seconds'], 120, '未提交的小节保持默认')
        self.assertFalse(saved['local']['enabled'])

    def test_history_empty(self):
        data = dashboard.get_disaster_history(None)
        self.assertTrue(data['stats']['ready'])
        self.assertEqual(data['events'], [])
        self.assertIn('24h', data['stats']['windows'])

    def test_history_reads_events_and_pushes(self):
        store = disaster_store.EventStore(self.store_path)
        store.record_event(
            event_key='eq:1', kind='earthquake', status='pushed',
            source='cenc_eew', title='样例地震', place='样例地点',
        )
        store.record_push('eq:1', target_type='group', target='123456789', ok=True)

        data = dashboard.get_disaster_history(None)
        self.assertEqual(data['stats']['windows']['24h']['pushed'], 1)
        self.assertEqual(data['stats']['windows']['24h']['push_ok'], 1)
        self.assertEqual(data['events'][0]['event_key'], 'eq:1')

    def test_render_status_uses_shared_default(self):
        async def fake_health(url, timeout=3):
            self.assertTrue(url.startswith('http://127.0.0.1:'))
            return {'ok': True, 'ready': True}
        with patch.object(dashboard.disaster_render_mod, 'health', fake_health):
            data = asyncio.run(dashboard.disaster_render_status(None))
        self.assertTrue(data['configured'])

    def test_render_status_ok(self):
        config = disaster_config.load_config(self.path)
        config['render']['remote_url'] = 'http://127.0.0.1:3000'
        disaster_config.save_config(config, self.path)

        async def fake_health(url, timeout=3):
            return {'ok': True, 'ready': True, 'renders': 2}

        with patch.object(dashboard.disaster_render_mod, 'health', fake_health):
            data = asyncio.run(dashboard.disaster_render_status(None))
        self.assertTrue(data['ok'])
        self.assertEqual(data['status']['renders'], 2)

    def test_render_status_offline(self):
        config = disaster_config.load_config(self.path)
        config['render']['remote_url'] = 'http://127.0.0.1:3000'
        disaster_config.save_config(config, self.path)

        async def fake_health(url, timeout=3):
            raise RuntimeError('down')

        with patch.object(dashboard.disaster_render_mod, 'health', fake_health):
            data = asyncio.run(dashboard.disaster_render_status(None))
        self.assertFalse(data['ok'])
        self.assertIn('down', data['message'])


if __name__ == '__main__':
    unittest.main()
