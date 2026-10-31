"""Endfield 纯逻辑用例（MIT）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from endfield import logic  # noqa: E402


class EndfieldLogicTest(unittest.TestCase):
    def test_mask_token(self):
        self.assertEqual(logic.mask_token(""), "")
        self.assertEqual(logic.mask_token("abcd"), "****")
        self.assertEqual(logic.mask_token("abcdefgh"), "****efgh")
        self.assertEqual(logic.mask_api_key("1234567890"), "****7890")

    def test_mask_role_id(self):
        self.assertEqual(logic.mask_role_id(""), "****")
        self.assertEqual(logic.mask_role_id("1234"), "****")
        self.assertEqual(logic.mask_role_id("12345678"), "12****78")

    def test_parse_page(self):
        self.assertEqual(logic.parse_page(""), 1)
        self.assertEqual(logic.parse_page("2"), 2)
        self.assertEqual(logic.parse_page("abc"), 1)
        self.assertEqual(logic.parse_page("0"), 1)
        self.assertEqual(logic.parse_page("1000"), 1)
        self.assertEqual(logic.parse_page("1000", maximum=2000), 1000)

    def test_paginate(self):
        items = list(range(45))
        page, current, pages = logic.paginate(items, 1)
        self.assertEqual(page, list(range(20)))
        self.assertEqual(current, 1)
        self.assertEqual(pages, 3)

        page, current, pages = logic.paginate(items, 3)
        self.assertEqual(page, list(range(40, 45)))
        self.assertEqual(current, 3)

        page, current, pages = logic.paginate(items, 99)
        self.assertEqual(current, 3)
        self.assertEqual(page, list(range(40, 45)))

        page, current, pages = logic.paginate([], 5)
        self.assertEqual(page, [])
        self.assertEqual(current, 1)
        self.assertEqual(pages, 1)

    def test_consent_text(self):
        text = logic.consent_text()
        self.assertIn("end-api.shallow.ink", text)
        self.assertIn("0600", text)
        self.assertIn("同意", text)

    def test_error_text_has_no_secret(self):
        for kind in ("no_binding", "invalid_binding", "rate_limit", "timeout", "render"):
            text = logic.error_text(kind)
            self.assertNotIn("token", text.lower())
            self.assertNotIn("key", text.lower())


if __name__ == "__main__":
    unittest.main()
