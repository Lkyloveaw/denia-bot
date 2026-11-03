# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""管理面板「服务器状态」接口用例。

直接调用路由函数，把鉴权换成空实现、路径指到临时目录——这些用例要验证的是
「返回结构对不对、空库会不会崩」，不是 FastAPI 的鉴权中间件。
"""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import dashboard
import status_config
import status_store


class PanelTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.base = Path(self.folder.name)
        dashboard._status_cache.clear()
        fake_live = {
            'cpu': {'percent': 12.5, 'cores': 8, 'threads': 16, 'brand': 'Test', 'freq_mhz': 3000},
            'memory': {'percent': 40.0, 'used': 1, 'total': 2},
            'swap': {'percent': 1.0, 'used': 0, 'total': 1},
            'disks': [{'path': '/', 'used': 1, 'total': 2, 'percent': 50.0}],
            'process': {'total': 10, 'running': 2, 'top': [{'name': 'python', 'cpu': 5.0, 'mem': 1.0}]},
            'load': '0.1 / 8 核',
            'uptime': 3600.0,
            'collected_at': time.time(),
        }
        self.patches = [
            patch.object(dashboard.status_live, 'panel_snapshot', lambda **kw: dict(fake_live)),
            patch.object(dashboard, 'status_paths',
                         lambda: (self.base / 'statuscard.json', self.base / 'statuscard-state.json', self.base / 'statuscard.sqlite3')),
            patch.object(dashboard, 'authorized', lambda req, allow_change=False: 'token'),
            patch.object(dashboard, 'mutate', lambda req, allow_change=False: 'token'),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        dashboard._status_cache.clear()
        self.folder.cleanup()

    def test_overview_on_empty_data_does_not_crash(self):
        data = dashboard.status_overview(None, days=7)
        self.assertEqual(data['range_days'], 7)
        self.assertIn('live', data)
        self.assertEqual(data['history']['points'], [])
        self.assertEqual(data['events'], [])
        self.assertIsNone(data['report'])
        self.assertIn('options', data['config'])

    def test_range_is_clamped(self):
        self.assertEqual(dashboard.status_overview(None, days=999)['range_days'], 30)
        self.assertEqual(dashboard.status_overview(None, days=0)['range_days'], 1)

    def test_overview_reads_samples_and_events(self):
        samples = status_store.SampleStore(self.base / 'statuscard.sqlite3')
        now = int(time.time()) - 60
        samples.add_resource(now, cpu=42.0, mem=51.0, swap=0.0, disk=20.0, net_up=1.0, net_down=2.0)
        samples.add_event(now, 'resource', 'CPU 使用率', '92%')
        samples.record_snapshot('2026-10-30', friends=1, groups=2, members=3, recv_total=10, sent_total=5)

        data = dashboard.status_overview(None, days=30)
        self.assertEqual(len(data['history']['points']), 1)
        self.assertEqual(data['history']['points'][0]['c'], 42.0)
        self.assertEqual(data['events'][0]['title'], 'CPU 使用率')
        self.assertTrue(data['history']['days'] >= 1)

    def test_downsamples_long_history(self):
        points = [{'t': index, 'c': index % 100, 'm': 0} for index in range(1000)]
        picked = dashboard.downsample(points, limit=240)
        self.assertEqual(len(picked), 240)
        self.assertEqual(picked[0], points[0])
        self.assertEqual(picked[-1], points[-1])

    def test_save_config_only_touches_whitelist(self):
        async def run():
            return await dashboard.set_status_config(FakeRequest({
                'display': {'redact': ['network'], 'width': 9999},
                'alerts': {'enabled': True, 'targets': ['123456'], 'sustain_samples': 99},
                'report': {'enabled': True, 'time': '20:30'},
                'nonsense': {'enabled': True},
            }))

        result = asyncio.run(run())
        self.assertTrue(result['ok'])
        saved = status_config.load_config(self.base / 'statuscard.json')
        self.assertEqual(saved['display']['redact'], ['network'])
        self.assertEqual(saved['display']['width'], 9999, '宽度在白名单里，应该生效')
        self.assertEqual(saved['alerts']['sustain_samples'], 3, '非白名单字段不该被面板改掉')
        self.assertTrue(saved['alerts']['enabled'])
        self.assertEqual(saved['report']['time'], '20:30')
        self.assertNotIn('nonsense', saved)


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


if __name__ == '__main__':
    unittest.main()
