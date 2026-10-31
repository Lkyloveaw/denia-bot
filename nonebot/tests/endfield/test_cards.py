"""自包含卡片构建器用例（MIT）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from endfield.cards import MAX_LINES, build_card  # noqa: E402


class CardTest(unittest.TestCase):
    def test_card_contains_title_and_lines(self):
        html = build_card("抽卡记录", ["★6 陈", "★5 阿米娅"], subtitle="共 2 条")
        self.assertIn("抽卡记录", html)
        self.assertIn("★6 陈", html)
        self.assertIn("共 2 条", html)
        self.assertIn("<style>", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<link", html)

    def test_card_escapes_user_content(self):
        html = build_card("<script>alert(1)</script>", ["<b>x</b>"])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)

    def test_empty_card(self):
        html = build_card("日历", [])
        self.assertIn("暂无数据", html)

    def test_long_card_truncates(self):
        html = build_card("列表", [f"第{i}行" for i in range(MAX_LINES + 5)])
        self.assertIn(f"前 {MAX_LINES} 行", html)
        self.assertIn(f"第{MAX_LINES - 1}行", html)
        self.assertNotIn(f"第{MAX_LINES}行", html)

    def test_no_external_resources(self):
        html = build_card("测试", ["正常文本"], footer="页脚")
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertIn("页脚", html)


if __name__ == "__main__":
    unittest.main()
