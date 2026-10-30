import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import group_topics_logic as topics


class GroupTopicsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.db = Path(self.tmp.name) / 'topics.sqlite3'

    def tearDown(self):
        self.tmp.cleanup()

    def test_groups_are_disabled_by_default_and_isolated(self):
        self.assertFalse(topics.enabled(self.db, '101'))
        topics.record(self.db, '101', 'm1', '今汐深塔挑战', now=2000000, tokenizer=lambda _: ['今汐', '深塔'])
        self.assertEqual(topics.top(self.db, '101', now=2000000), [])
        topics.set_enabled(self.db, '101', True)
        topics.record(self.db, '101', 'm1', '今汐深塔挑战', now=2000000, tokenizer=lambda _: ['今汐', '深塔'])
        topics.record(self.db, '101', 'm1', '今汐深塔挑战', now=2000000, tokenizer=lambda _: ['今汐', '深塔'])
        self.assertEqual(dict(topics.top(self.db, '101', now=2000000))['今汐'], 1)
        self.assertEqual(topics.top(self.db, '102', now=2000000), [])
        topics.set_enabled(self.db, '101', False)
        self.assertEqual(topics.top(self.db, '101', now=2000000), [])
        topics.record(self.db, '101', 'm2', '新的话题', now=2000000, tokenizer=lambda _: ['新的话题'])
        topics.set_enabled(self.db, '101', True)
        self.assertNotIn('新的话题', dict(topics.top(self.db, '101', now=2000000)))

    def test_expiry_clear_and_no_raw_messages(self):
        topics.set_enabled(self.db, '101', True)
        topics.record(self.db, '101', 'm1', '原始消息今汐', now=1000000, tokenizer=lambda _: ['今汐'])
        self.assertEqual(topics.top(self.db, '101', now=1000000 + 8 * 86400), [])
        topics.record(self.db, '101', 'm2', '完整原文不应保存', now=2000000, tokenizer=lambda _: ['鸣潮'])
        with closing(sqlite3.connect(self.db)) as con:
            self.assertNotIn('完整原文不应保存', '\n'.join(str(x) for x in con.iterdump()))
        topics.clear(self.db, '101')
        self.assertEqual(topics.top(self.db, '101', now=2000000), [])

    def test_filter_sensitive_and_low_quality_tokens(self):
        text = '去 https://example.com 登录 13800138000 今汐 深塔 123456'
        words = topics.keywords(text, lambda _: ['https', 'example', 'com', '13800138000', '今汐', '深塔', '123456', '的'])
        self.assertEqual(set(words), {'今汐', '深塔'})
        self.assertEqual(topics.keywords('/记忆 查看', lambda _: ['记忆', '查看']), [])

    def test_speaker_ranking_is_opt_in_deduplicated_expiring_and_clearable(self):
        now = 2000000
        topics.record(self.db, '101', '1', '嗯', now=now, tokenizer=lambda _: [], user_id='200', display_name='群友甲')
        self.assertEqual(topics.speaker_top(self.db, '101', now=now), [])
        topics.set_enabled(self.db, '101', True)
        version = topics.version(self.db, '101')
        for mid in ('1', '1', '2'):
            topics.record(self.db, '101', mid, '嗯', now=now, tokenizer=lambda _: [], user_id='200', display_name='群友甲', expected_version=version)
        topics.record(self.db, '101', '3', '测试', now=now, tokenizer=lambda _: [], user_id='201', display_name='群友乙', expected_version=version)
        self.assertEqual([(x['user_id'], x['count']) for x in topics.speaker_top(self.db, '101', now=now)], [('200', 2), ('201', 1)])
        self.assertEqual(topics.speaker_top(self.db, '102', now=now), [])
        topics.clear(self.db, '101')
        topics.record(self.db, '101', '4', '迟到队列', now=now, tokenizer=lambda _: [], user_id='200', expected_version=version)
        self.assertEqual(topics.speaker_top(self.db, '101', now=now), [])
        topics.record(self.db, '101', '5', '新消息', now=now, tokenizer=lambda _: [], user_id='200')
        self.assertEqual(topics.speaker_top(self.db, '101', now=now + 8 * 86400), [])

    def test_clear_and_disable_reject_queued_old_messages(self):
        topics.set_enabled(self.db, '101', True)
        old = topics.version(self.db, '101')
        topics.clear(self.db, '101')
        topics.record(self.db, '101', 'm1', '今汐深塔', now=2000000,
                      tokenizer=lambda _: ['今汐'], expected_version=old)
        self.assertEqual(topics.top(self.db, '101', now=2000000), [])
        old = topics.version(self.db, '101')
        topics.set_enabled(self.db, '101', False)
        topics.set_enabled(self.db, '101', True)
        topics.record(self.db, '101', 'm2', '今汐深塔', now=2000000,
                      tokenizer=lambda _: ['今汐'], expected_version=old)
        self.assertEqual(topics.top(self.db, '101', now=2000000), [])


if __name__ == '__main__':
    unittest.main()
