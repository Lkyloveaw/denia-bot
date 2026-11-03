# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。
import json
import sys
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image

import epic_free_image as image
import epic_free_logic as logic

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
NOW = datetime(2026, 10, 31, 3, 14, tzinfo=timezone.utc)


def elements(name):
    data = json.loads((FIXTURES / name).read_text('utf-8'))
    return data['data']['Catalog']['searchStore']['elements']


class EpicFreeImageTest(unittest.TestCase):
    def test_renders_fixture_as_png(self):
        view = logic.build_view(elements('epic_free_cn.json'), NOW)
        png = image.render(view['free'], view['upcoming'])
        self.assertTrue(png.startswith(b'\x89PNG'))
        with Image.open(BytesIO(png)) as opened:
            opened.load()
            self.assertEqual(opened.width, 760)
            self.assertGreater(opened.height, 300)

    def test_renders_empty_state(self):
        png = image.render([], [])
        with Image.open(BytesIO(png)) as opened:
            opened.load()
            self.assertEqual(opened.width, 760)
            self.assertGreater(opened.height, 200)

    def test_long_description_does_not_crash(self):
        view = logic.build_view(elements('epic_free_cn.json'), NOW)
        view['free'][0]['description'] = '长' * 500
        png = image.render(view['free'], view['upcoming'])
        self.assertTrue(png.startswith(b'\x89PNG'))


if __name__ == '__main__':
    unittest.main()
