# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import epic_free_config as config


class EpicFreeConfigTest(unittest.TestCase):
    def test_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'epic_free.json'
            data = config.read(path)
            self.assertEqual(data['country'], 'CN')
            self.assertEqual(data['targets'], [])
            self.assertTrue(data['push_enabled'])
            self.assertEqual(data['poll_minutes'], 30)

    def test_add_and_remove_target(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'epic_free.json'
            config.add_target('group', '12345', path)
            config.add_target('private', '67890', path)
            config.add_target('group', '12345', path)
            data = config.read(path)
            self.assertEqual(len(data['targets']), 2)
            self.assertEqual(data['targets'][0], {'kind': 'group', 'id': '12345'})
            config.remove_target('group', '12345', path)
            self.assertEqual(len(config.read(path)['targets']), 1)

    def test_invalid_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'epic_free.json'
            with self.assertRaises(ValueError):
                config.add_target('bad', '12345', path)
            with self.assertRaises(ValueError):
                config.add_target('group', 'abc', path)

    def test_update_clamps_and_normalizes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'epic_free.json'
            config.update({'poll_minutes': 1, 'country': 'us', 'push_enabled': False}, path)
            data = config.read(path)
            self.assertEqual(data['poll_minutes'], 5)
            self.assertEqual(data['country'], 'US')
            self.assertFalse(data['push_enabled'])

    def test_corrupt_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'epic_free.json'
            path.write_text('{broken', encoding='utf-8')
            self.assertEqual(config.read(path)['targets'], [])
            self.assertEqual(config.read(path)['country'], 'CN')

    def test_invalid_targets_are_dropped_on_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'epic_free.json'
            path.write_text(json.dumps({'targets': [
                {'kind': 'group', 'id': '12345'},
                {'kind': 'bad', 'id': '12345'},
                {'kind': 'private', 'id': 'abc'},
            ]}), encoding='utf-8')
            self.assertEqual(config.read(path)['targets'], [{'kind': 'group', 'id': '12345'}])

    def test_test_trigger_is_one_shot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'trigger.json'
            self.assertFalse(config.has_test_request(path))
            payload = config.request_test(path)
            self.assertIn('nonce', payload)
            self.assertTrue(config.has_test_request(path))
            taken = config.take_test_request(path)
            self.assertEqual(taken['nonce'], payload['nonce'])
            self.assertFalse(config.has_test_request(path))
            self.assertIsNone(config.take_test_request(path))


if __name__ == '__main__':
    unittest.main()
