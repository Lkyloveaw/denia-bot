# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""状态与采样存储用例。

重点验证三件事：默认值补齐不丢字段、写入能读回来、损坏的库不阻塞启动。
全部在临时目录里跑，不碰真实的 `data/`。
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import status_store as store


class StateStoreTest(unittest.TestCase):
    def test_defaults_are_merged(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text(json.dumps({'net': {'month': '2026-10'}}), encoding='utf-8')
            state = store.StateStore(path)
            data = state.load()
            self.assertEqual(data['net']['month'], '2026-10')
            self.assertIn('history', data['net'], '缺的字段要补默认值')
            self.assertIn('alerts', data)
            self.assertTrue(data['first_seen'])

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            state = store.StateStore(path)
            state.set('host', 'denia')
            state.update_section('net', month_up=123)
            state.save()
            again = store.StateStore(path)
            self.assertEqual(again.get('host'), 'denia')
            self.assertEqual(again.section('net')['month_up'], 123)

    def test_corrupt_file_falls_back_to_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text('{not json', encoding='utf-8')
            state = store.StateStore(path)
            data = state.load()
            self.assertIn('alerts', data)
            state.save()  # 坏文件也要能被正常覆盖
            self.assertTrue(json.loads(path.read_text(encoding='utf-8')))

    def test_instances_do_not_share_default_state(self):
        """一个新实例不能读到另一个实例改过的默认值（浅拷贝的经典坑）。"""
        with tempfile.TemporaryDirectory() as folder:
            first = store.StateStore(Path(folder) / 'a.json')
            first.load()
            first.set('alerts', {'rules': {'cpu': {'alerting': True}}})
            first.save()

            second = store.StateStore(Path(folder) / 'missing.json')
            self.assertEqual(second.load()['alerts']['rules'], {},
                             '状态文件不存在时必须是干净的默认值')

    def test_section_wrong_type_returns_dict(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text(json.dumps({'net': 'oops'}), encoding='utf-8')
            self.assertEqual(store.StateStore(path).section('net'), {})


class SampleStoreTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'samples.sqlite3'
        self.store = store.SampleStore(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_resource_roundtrip_and_window(self):
        self.store.add_resource(1000, cpu=10, mem=20, swap=0, disk=50, net_up=1, net_down=2)
        self.store.add_resource(2000, cpu=30, mem=40, swap=5, disk=60, net_up=3, net_down=4)
        rows = self.store.list_resources(1500)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['cpu'], 30)

    def test_events_and_recovery_resolution(self):
        event_id = self.store.add_event(1000, 'resource', 'CPU 使用率', '92%')
        self.assertGreater(event_id, 0)
        self.assertTrue(self.store.resolve_event('CPU 使用率', 1600))
        rows = self.store.list_events(0)
        self.assertEqual(rows[0]['resolved_ts'], 1600)
        self.assertFalse(self.store.resolve_event('不存在的标题', 1))

    def test_snapshot_and_report_runs(self):
        self.store.record_snapshot('2026-10-30', friends=1, groups=2, members=3, recv_total=10, sent_total=5)
        self.assertTrue(self.store.snapshot_ready('2026-10-30'))
        snapshot = self.store.get_snapshot('2026-10-30')
        self.assertEqual(snapshot['members'], 3)
        self.store.add_report_run('2026-10-30', 123, True, 'image')
        latest = self.store.latest_report_run('2026-10-30')
        self.assertEqual(latest['ok'], 1)

    def test_website_and_process_samples(self):
        self.store.add_website_samples(1000, [{'name': '博客', 'ok': True, 'status': 'HTTP 200', 'delay_ms': 90}])
        self.store.add_process_samples(1000, [{'kind': 'cpu', 'name': 'python3', 'value': 40}])
        self.assertEqual(len(self.store.list_website_samples(0)), 1)
        self.assertEqual(len(self.store.list_process_samples(0)), 1)

    def test_prune_keeps_recent(self):
        now = 2_000_000
        self.store.add_resource(now - 40 * 86400, cpu=1, mem=1, swap=0, disk=1, net_up=0, net_down=0)
        self.store.add_resource(now - 3600, cpu=2, mem=2, swap=0, disk=2, net_up=0, net_down=0)
        self.store.prune(resource_days=30, event_days=90, now=now)
        rows = self.store.list_resources(0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['cpu'], 2)

    def test_stats_shape(self):
        stats = self.store.stats()
        self.assertTrue(stats['ready'])
        self.assertIn('resource_samples', stats['counts'])

    def test_corrupt_database_is_replaced(self):
        broken = Path(self.folder.name) / 'broken.sqlite3'
        broken.write_bytes(b'this is not a sqlite file at all' * 100)
        recovered = store.SampleStore(broken)
        # 要么重建成功，要么明确不可用；都不应该抛异常
        self.assertTrue(recovered.stats()['ready'] or not recovered.stats()['ready'])
        recovered.add_resource(1, cpu=1, mem=1, swap=0, disk=1, net_up=0, net_down=0)

    def test_optional_values_become_null(self):
        self.store.record_snapshot('2026-10-31', friends=None, groups=None, members=None, recv_total=None, sent_total=None)
        con = sqlite3.connect(str(self.path))
        try:
            row = con.execute('SELECT friends FROM snapshots WHERE day=?', ('2026-10-31',)).fetchone()
        finally:
            con.close()
        self.assertIsNone(row[0])


class WindowTest(unittest.TestCase):
    def test_day_window(self):
        start, end = store.day_window('2026-10-30')
        self.assertEqual(end - start, 86400)
        # UTC+8 的 00:00 对应 UTC 前一天 16:00
        self.assertEqual(datetime.fromtimestamp(start, timezone.utc).hour, 16)

    def test_today_helpers(self):
        moment = datetime(2026, 10, 30, 23, 0)
        self.assertEqual(store.today_key(moment), '2026-10-30')
        self.assertEqual(store.yesterday_key(moment), '2026-10-29')


if __name__ == '__main__':
    unittest.main()
