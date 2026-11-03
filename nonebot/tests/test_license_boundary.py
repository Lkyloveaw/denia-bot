"""许可边界检查：AGPL port 文件头、NOTICE/LICENSE、README 例外。

本文件是本仓库原创（MIT），不属于 disaster port；见
docs/research/disaster-license-boundary.md。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
UPSTREAM = 'Pancakes-Labs/astrbot_plugin_disaster_warning'

AGPL_FILES = [
    'disaster_config.py', 'disaster_logic.py', 'disaster_sources.py', 'disaster_state.py',
    'disaster_render.py', 'disaster_china_logic.py', 'disaster_china_sources.py', 'disaster_tone.py',
    'disaster_store.py',
    'plugins/disaster_warning.py',
    'tests/test_disaster_config.py', 'tests/test_disaster_logic.py', 'tests/test_disaster_sources.py',
    'tests/test_disaster_state.py', 'tests/test_disaster_render.py', 'tests/test_disaster_panel.py',
    'tests/test_disaster_wiring.py', 'tests/test_disaster_china_logic.py',
    'tests/test_disaster_china_sources.py', 'tests/test_disaster_china_wiring.py',
    'tests/test_disaster_tone.py', 'tests/test_disaster_store.py',
]


class LicenseBoundaryTest(unittest.TestCase):
    def test_agpl_headers(self):
        for name in AGPL_FILES:
            path = ROOT / name
            self.assertTrue(path.is_file(), f'缺文件：{name}')
            head = path.read_text(encoding='utf-8')[:500]
            self.assertIn('AGPL-3.0', head, f'{name} 缺 AGPL 头')
            self.assertIn(UPSTREAM, head, f'{name} 缺上游项目名')
            self.assertIn('disaster-warning-NOTICE.md', head, f'{name} 缺 NOTICE 指针')

    def test_third_party_docs(self):
        third = REPO / 'docs' / 'third-party'
        notice = (third / 'disaster-warning-NOTICE.md').read_text(encoding='utf-8')
        attribution = (third / 'disaster-warning-ATTRIBUTION.md').read_text(encoding='utf-8')
        license_text = (third / 'disaster-warning-LICENSE').read_text(encoding='utf-8')
        self.assertIn(UPSTREAM, notice)
        self.assertIn('20ba182a36dcb58e6f711302813c092ae05a574b', notice)
        self.assertIn('AGPL-3.0', notice)
        self.assertIn('DBJD-CR & Aloys233', attribution)
        self.assertTrue(license_text.startswith('                    GNU AFFERO GENERAL PUBLIC LICENSE'))
        self.assertIn('Version 3, 19 November 2007', license_text)

    def test_readme_exception(self):
        readme = (REPO / 'README.md').read_text(encoding='utf-8')
        self.assertIn(UPSTREAM, readme)
        self.assertIn('disaster-warning-NOTICE.md', readme)
        self.assertIn('AGPL-3.0', readme)

    def test_original_files_do_not_claim_upstream_header(self):
        for name in ('docker/playwright-renderer/server.js', 'docs/research/prototypes/build_cards.py'):
            head = (REPO / name).read_text(encoding='utf-8')[:500]
            self.assertNotIn(UPSTREAM, head, f'{name} 不应冒充 AGPL port')
            self.assertNotIn('AGPL-3.0 分发', head)


if __name__ == '__main__':
    unittest.main()
