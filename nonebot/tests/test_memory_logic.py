import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_logic as ml


class MemoryTest(unittest.TestCase):
    def test_user_isolation_persistence_dedup_and_forget(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            db = Path(folder) / 'memories.sqlite3'
            version = ml.revision(db, '100')
            self.assertEqual(ml.store(db, '100', ['喜欢咖啡', '喜欢咖啡'], 'private', version), 1)
            self.assertEqual(ml.list_memories(db, '100')[0]['text'], '喜欢咖啡')
            self.assertEqual(ml.list_memories(db, '200'), [])
            self.assertTrue(ml.forget(db, '100', '全部'))
            self.assertEqual(ml.list_memories(db, '100'), [])
            self.assertEqual(ml.store(db, '100', ['旧的异步任务'], 'private', version), 0)

    def test_validated_extraction_and_private_group_annotation(self):
        self.assertEqual(ml.parse_extraction('{"memories":["喜欢咖啡", "喜欢咖啡", ""]}'), ['喜欢咖啡'])
        self.assertEqual(ml.parse_extraction('not json'), [])
        rendered = ml.render_memories([{'text': '喜欢咖啡', 'source': 'private'}], in_group=True)
        self.assertIn('私聊', rendered)
        self.assertIn('不要主动', rendered)


if __name__ == '__main__':
    unittest.main()
