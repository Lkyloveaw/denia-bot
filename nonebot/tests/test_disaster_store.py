# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 实现 V1 事件/推送/健康/统计 SQLite；
# 许可见 docs/third-party/disaster-warning-NOTICE.md，口径见 docs/research/disaster-event-storage.md

"""事件库用例：身份、推送记录、24h/7d 统计、30 天清理、损坏自愈。"""

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disaster_store as store


class EventStoreTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'disaster.sqlite3'
        self.db = store.EventStore(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def add(self, key='eq:1', **extra):
        data = {
            'event_key': key,
            'kind': 'earthquake',
            'source': 'cenc_eew',
            'event_id': '1',
            'report_num': 1,
            'title': '样例地震',
            'place': '样例地点',
            'magnitude': 5.2,
            'occurred_at': '2026-10-31 12:00:00',
        }
        data.update(extra)
        self.assertTrue(self.db.record_event(**data))

    def test_event_identity_and_recent(self):
        self.add()
        self.add('weather:1', kind='weather', source='jian', title='暴雨预警', magnitude=None)
        rows = self.db.recent(10)
        self.assertEqual(len(rows), 2)
        keys = {row['event_key'] for row in rows}
        self.assertEqual(keys, {'eq:1', 'weather:1'})
        self.assertEqual(rows[0]['kind'], 'weather', '最近写入的在前')

    def test_status_update_keeps_first_seen(self):
        self.add(status='filtered', when=1000.0)
        self.db.set_status('eq:1', 'pushed')
        row = self.db.recent(1)[0]
        self.assertEqual(row['status'], 'pushed')
        self.assertEqual(row['first_seen'], 1000.0)

    def test_push_records_and_stats(self):
        self.add()
        self.assertTrue(self.db.record_push('eq:1', target_type='group', target='123', ok=True))
        self.assertTrue(self.db.record_push('eq:1', target_type='private', target='456', ok=False, error='blocked'))
        row = self.db.recent(1)[0]
        self.assertEqual(row['pushes'], 2)
        self.assertEqual(row['push_ok'], 1)
        self.assertEqual(row['push_fail'], 1)
        stats = self.db.stats(7)
        self.assertEqual(stats['windows']['24h']['push_ok'], 1)
        self.assertEqual(stats['windows']['24h']['push_fail'], 1)
        self.assertEqual(stats['windows']['24h']['pushed'], 0, '状态未更新前不算 pushed')

    def test_windows_and_retention(self):
        now = time.time()
        self.add('old', when=now - 40 * 86400)
        self.db.set_status('old', 'pushed')
        self.add('new', when=now)
        stats = self.db.stats(7, now=now)
        self.assertEqual(stats['windows']['24h']['events'], 1)
        self.assertEqual(stats['windows']['7d']['events'], 1)
        deleted = self.db.prune(30, now=now)
        self.assertEqual(deleted['events'], 1)
        self.assertEqual({row['event_key'] for row in self.db.recent(10)}, {'new'})

    def test_source_health(self):
        self.assertTrue(self.db.sync_health({'cenc': 1234.0}, {'jian': 'token expired'}))
        con = sqlite3.connect(self.path)
        try:
            rows = dict(con.execute('SELECT source, last_error FROM source_health'))
        finally:
            con.close()
        self.assertEqual(rows['cenc'], '')
        self.assertEqual(rows['jian'], 'token expired')

    def test_corruption_rebuilds(self):
        self.db.close()
        self.path.write_bytes(b'not a sqlite database')
        recovered = store.EventStore(self.path)
        self.assertTrue(recovered.ready)
        self.assertEqual(recovered.recent(5), [])
        backups = list(self.path.parent.glob('disaster.sqlite3.bak-*'))
        self.assertTrue(backups)

    def test_errors_and_details_are_redacted(self):
        self.add(detail='token=secret-token Bearer abc.def')
        self.db.record_push(
            'eq:1', target_type='group', target='123', ok=False,
            error='Authorization: Bearer abc.def token=secret-token',
        )
        self.db.sync_health({}, {'jian': 'key=secret-token'})
        row = self.db.recent(1)[0]
        self.assertNotIn('secret-token', row['detail'])
        con = sqlite3.connect(self.path)
        try:
            error = con.execute('SELECT error FROM pushes').fetchone()[0]
            health = con.execute(
                "SELECT last_error FROM source_health WHERE source='jian'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertNotIn('secret-token', error)
        self.assertNotIn('secret-token', health)
        self.assertIn('***', error)

    def test_no_token_columns(self):
        self.add()
        con = sqlite3.connect(self.path)
        try:
            columns = {row[1] for row in con.execute('PRAGMA table_info(events)')}
        finally:
            con.close()
        self.assertNotIn('token', columns)
        self.assertNotIn('payload', columns)


if __name__ == '__main__':
    unittest.main()
