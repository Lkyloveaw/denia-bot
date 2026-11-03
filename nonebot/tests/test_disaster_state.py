# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""地震源状态文件用例：去重、首轮标记、错误记录、裁剪。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disaster_state as state_mod


class StateTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'state.json'

    def tearDown(self):
        self.folder.cleanup()

    def test_roundtrip(self):
        state = state_mod.new_state()
        state_mod.mark_seen(state, 'cenc_eew:a:1', 1000)
        state['initialized']['cenc_eew'] = True
        state['baseline_at']['cenc_list'] = '2026-10-31 00:00:00'
        state_mod.record_poll(state, 'cenc_eew', 1000)
        state_mod.increment_pushed(state)
        state_mod.save(state, self.path)
        loaded = state_mod.load(self.path)
        self.assertIn('cenc_eew:a:1', loaded['seen'])
        self.assertTrue(loaded['initialized']['cenc_eew'])
        self.assertEqual(loaded['baseline_at']['cenc_list'], '2026-10-31 00:00:00')
        self.assertEqual(loaded['last_poll']['cenc_eew'], 1000.0)
        self.assertEqual(loaded['counts']['pushed'], 1)

    def test_corrupt_file_falls_back(self):
        self.path.write_text('{not json', encoding='utf-8')
        state = state_mod.load(self.path)
        self.assertEqual(state['seen'], {})
        self.assertIn('counts', state)

    def test_bad_types_are_normalized(self):
        self.path.write_text('{"seen": [], "initialized": "x", "last_poll": 1, "counts": {"pushed": "2"}}',
                             encoding='utf-8')
        state = state_mod.load(self.path)
        self.assertEqual(state['seen'], {})
        self.assertEqual(state['initialized'], {})
        self.assertEqual(state['last_poll'], {})
        self.assertEqual(state['counts']['pushed'], 2)

    def test_record_poll_clears_error_on_success(self):
        state = state_mod.new_state()
        state_mod.record_poll(state, 'jma_eew', 1000, error='timeout')
        self.assertEqual(state['errors']['jma_eew'], 'timeout')
        state_mod.record_poll(state, 'jma_eew', 1010)
        self.assertNotIn('jma_eew', state['errors'])

    def test_prune_keeps_newest_and_drops_old(self):
        state = state_mod.new_state()
        now = 2_000_000
        state_mod.mark_seen(state, 'old', now - 30 * 86400)
        state_mod.mark_seen(state, 'a', now - 10)
        state_mod.mark_seen(state, 'b', now - 5)
        state_mod.mark_seen(state, 'c', now - 1)
        state_mod.prune(state, keep=2, now=now)
        self.assertEqual(set(state['seen']), {'b', 'c'})


if __name__ == '__main__':
    unittest.main()
