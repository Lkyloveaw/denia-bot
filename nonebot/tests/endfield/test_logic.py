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

    def test_parse_command(self):
        self.assertEqual(logic.parse_command("便签"), logic.Command("便签"))
        self.assertEqual(logic.parse_command(" 理智 "), logic.Command("理智"))
        self.assertEqual(
            logic.parse_command("切换绑定 2"), logic.Command("切换绑定", "2")
        )
        self.assertEqual(
            logic.parse_command("删除绑定 3"), logic.Command("删除绑定", "3")
        )
        self.assertIsNone(logic.parse_command("切换绑定"))
        self.assertIsNone(logic.parse_command("删除绑定 0"))
        self.assertIsNone(logic.parse_command("便签呀"))
        self.assertIsNone(logic.parse_command(""))

    def test_action_of(self):
        self.assertEqual(logic.action_of(logic.Command("便签")), "query")
        self.assertEqual(logic.action_of(logic.Command("授权登陆")), "bind")
        self.assertEqual(logic.action_of(logic.Command("切换绑定", "1")), "switch")
        self.assertEqual(logic.action_of(logic.Command("删除绑定", "1")), "delete")

    def test_format_stamina_and_note(self):
        stamina = logic.format_stamina({"stamina": {"current": 50, "max": 120}})
        self.assertIn("50", stamina)
        self.assertIn("120", stamina)
        note = logic.format_note({"note": {"current": 3, "max": 5}})
        self.assertIn("3", note)
        self.assertIn("5", note)

    def test_format_attendance(self):
        text = logic.format_attendance({"awards": [{"name": "龙门币"}]})
        self.assertIn("签到成功", text)
        self.assertIn("龙门币", text)
        self.assertIn("已签到", logic.format_attendance({}))

    def test_format_binding_list(self):
        text = logic.format_binding_list(
            [
                {
                    "role_name": "角色A",
                    "server_id": 1,
                    "is_primary": True,
                    "status": "valid",
                },
                {
                    "role_name": "角色B",
                    "server_id": 2,
                    "is_primary": False,
                    "status": "invalid",
                },
            ]
        )
        self.assertIn("角色A", text)
        self.assertIn("主账号", text)
        self.assertIn("已失效", text)
        self.assertIn("暂无绑定", logic.format_binding_list([]))

    def test_user_gate_debounce_and_limit(self):
        gate = logic.UserGate(debounce_seconds=5, query_limit=2)
        ok, _ = gate.check("1", "query", now=100, day="2026-10-31")
        self.assertTrue(ok)
        ok, _ = gate.check("1", "query", now=101, day="2026-10-31")
        self.assertFalse(ok, "5 秒内应防抖")
        ok, _ = gate.check("1", "query", now=106, day="2026-10-31")
        self.assertTrue(ok, "第二次查询应允许")
        ok, _ = gate.check("1", "query", now=112, day="2026-10-31")
        self.assertFalse(ok, "第三次应触发日限额")
        ok, _ = gate.check("2", "query", now=112, day="2026-10-31")
        self.assertTrue(ok, "不同 QQ 不共享配额")
        ok, _ = gate.check("1", "query", now=200, day="2026-10-31", is_superuser=True)
        self.assertTrue(ok, "主人豁免")


if __name__ == "__main__":
    unittest.main()
