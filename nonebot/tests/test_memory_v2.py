import tempfile
import sqlite3
from contextlib import closing
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_logic as m


class MemoryV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.db = Path(self.tmp.name) / 'memory.sqlite3'

    def tearDown(self):
        self.tmp.cleanup()

    def test_vectors_rank_only_current_user_and_deletion_clears_vectors(self):
        v = m.revision(self.db, '1')
        m.store(self.db, '1', ['用户喜欢咖啡', '用户喜欢打游戏'], 'private', v)
        m.store(self.db, '2', ['其他人喜欢咖啡'], 'group', 0)
        for row in m.list_memories(self.db, '1'):
            m.set_vector(self.db, '1', row['id'], row['text'], [1, 0] if '咖啡' in row['text'] else [0, 1])
        m.set_vector(self.db, '2', m.list_memories(self.db, '2')[0]['id'], '其他人喜欢咖啡', [1, 0])
        ranked = m.recall(self.db, '1', [1, 0], '咖啡', limit=1)
        self.assertEqual([x['text'] for x in ranked], ['用户喜欢咖啡'])
        self.assertTrue(m.forget(self.db, '1', '全部'))
        self.assertEqual(m.recall(self.db, '1', [1, 0], '咖啡'), [])
        self.assertEqual(len(m.list_memories(self.db, '2')), 1)

    def test_near_duplicate_and_conflict_become_review_candidates(self):
        m.store(self.db, '1', ['用户喜欢喝咖啡'], 'private', 0)
        original = m.list_memories(self.db, '1')[0]
        m.set_vector(self.db, '1', original['id'], original['text'], [1, 0])
        result = m.store_vector_fact(self.db, '1', '用户爱喝咖啡', 'private', 0, [1, 0])
        self.assertEqual(result, 'candidate')
        self.assertEqual(len(m.list_memories(self.db, '1')), 1)
        self.assertEqual(len(m.list_candidates(self.db, '1')), 1)
        candidate = m.list_candidates(self.db, '1')[0]
        self.assertFalse(m.resolve_candidate(self.db, '2', candidate['id'], 'replace'))
        self.assertTrue(m.resolve_candidate(self.db, '1', candidate['id'], 'replace'))
        self.assertEqual(m.list_memories(self.db, '1')[0]['text'], '用户爱喝咖啡')
        self.assertEqual(m.list_candidates(self.db, '1'), [])

    def test_durable_jobs_retry_and_forget_prevents_resurrection(self):
        job = m.enqueue(self.db, '1', '一次对话', 'private', 'event-1', [])
        self.assertEqual(job, m.enqueue(self.db, '1', '一次对话', 'private', 'event-1', []))
        self.assertEqual(m.pending_jobs(self.db)[0]['id'], job)
        m.fail_job(self.db, job, 'timeout')
        self.assertEqual(m.pending_jobs(self.db)[0]['attempts'], 1)
        m.forget(self.db, '1', '全部')
        self.assertEqual(m.pending_jobs(self.db), [])
        self.assertEqual(m.store(self.db, '1', ['过期的偏好'], 'private', 0), 0)
        self.assertIsNone(m.enqueue(self.db, '1', '旧回复完成后才到的任务', 'private', 'late-event', [], expected_revision=0))

    def test_disable_prevents_new_jobs_and_cancels_pending(self):
        m.enqueue(self.db, '1', '我喜欢咖啡', 'private', 'event-1', [])
        m.set_enabled(self.db, '1', False)
        self.assertFalse(m.is_enabled(self.db, '1'))
        self.assertEqual(m.pending_jobs(self.db), [])
        self.assertIsNone(m.enqueue(self.db, '1', '我喜欢咖啡', 'private', 'event-2', []))
        m.set_enabled(self.db, '1', True)
        self.assertTrue(m.is_enabled(self.db, '1'))

    def test_irrelevant_low_similarity_not_recalled_but_shared_term_works(self):
        m.store(self.db, '1', ['用户喜欢喝咖啡'], 'private', 0)
        row = m.list_memories(self.db, '1')[0]
        m.set_vector(self.db, '1', row['id'], row['text'], [1, 0])
        self.assertEqual(m.recall(self.db, '1', [0.55, 0.835], '你好啊'), [])
        self.assertEqual(len(m.recall(self.db, '1', [0.58, 0.815], '你知道我喜欢什么吗')), 1)

    def test_existing_v1_database_is_preserved_during_migration(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.executescript('''CREATE TABLE memory_users(user_id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,text TEXT NOT NULL,
              source TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,text));
              INSERT INTO memories(user_id,text,source) VALUES('1','用户喜欢喝咖啡','private');''')
            con.commit()
        self.assertEqual(m.list_memories(self.db, '1')[0]['text'], '用户喜欢喝咖啡')
        self.assertEqual(len(m.missing_vectors(self.db, '1')), 1)


if __name__ == '__main__':
    unittest.main()
