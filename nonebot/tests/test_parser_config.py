import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from media_parser.core.config import PluginConfig


class ParserConfigTests(unittest.TestCase):
    def test_concurrent_session_changes_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            configs = [PluginConfig(path) for _ in range(8)]
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda item: item[1].add_blacklist(f'group:{item[0]+1}'), enumerate(configs)))
            self.assertEqual(set(PluginConfig(path).blacklist), {f'group:{i}' for i in range(1, 9)})

    def test_session_switch_does_not_overwrite_dashboard_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            bot = PluginConfig(path)
            dashboard = PluginConfig(path)
            dashboard.debounce_interval = 41
            dashboard.save_config()
            bot.add_blacklist('group:123')
            result = PluginConfig(path)
            self.assertEqual(result.debounce_interval, 41)
            self.assertIn('group:123', result.blacklist)

    def test_default_platforms_and_persisted_blacklist(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            config = PluginConfig(path)
            self.assertIn('bilibili', config.parser.enabled_platforms())
            self.assertNotIn('jmcomic', config.parser.enabled_platforms())
            self.assertIn('youtube', config.parser.platforms())
            self.assertNotIn('youtube', config.parser.enabled_platforms())
            config.add_blacklist('group:123')
            self.assertIn('group:123', PluginConfig(path).blacklist)


if __name__ == '__main__':
    unittest.main()
