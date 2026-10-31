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

    def test_parse_2a_commands(self):
        self.assertEqual(logic.parse_command("抽卡记录"), logic.Command("抽卡记录"))
        self.assertEqual(
            logic.parse_command("抽卡记录 2"), logic.Command("抽卡记录", "2")
        )
        self.assertEqual(logic.parse_command("抽卡分析"), logic.Command("抽卡分析"))
        self.assertEqual(
            logic.parse_command("干员列表 3"), logic.Command("干员列表", "3")
        )
        self.assertEqual(
            logic.parse_command("干员面板 陈"), logic.Command("干员面板", "陈")
        )
        self.assertEqual(logic.parse_command("危机合约"), logic.Command("危机合约"))
        self.assertEqual(
            logic.parse_command("危机合约 净罪"), logic.Command("危机合约", "净罪")
        )
        self.assertEqual(logic.parse_command("日历"), logic.Command("日历"))
        self.assertEqual(logic.parse_command("同步抽卡"), logic.Command("同步抽卡"))
        self.assertEqual(logic.parse_command("同步面板"), logic.Command("同步面板"))
        self.assertIsNone(logic.parse_command("抽卡记录 abc"))
        self.assertIsNone(logic.parse_command("干员面板"))

    def test_action_of_2a(self):
        self.assertEqual(logic.action_of(logic.Command("抽卡记录")), "query")
        self.assertEqual(logic.action_of(logic.Command("日历")), "query")
        self.assertEqual(logic.action_of(logic.Command("同步抽卡")), "sync")

    def test_format_gacha_records_and_stats(self):
        records = logic.format_gacha_records(
            {
                "records": [
                    {"rarity": 6, "char_name": "陈", "pool_name": "限定寻访"}
                ],
                "pages": 2,
            },
            page=1,
        )
        self.assertIn("★6", records)
        self.assertIn("陈", records)
        self.assertIn("第 1/2 页", records)
        stats = logic.format_gacha_stats(
            {"stats": {"total_count": 100, "star6_count": 2}}
        )
        self.assertIn("总抽数 100", stats)
        self.assertIn("六星 2", stats)

    def test_format_operator_list_and_panel(self):
        listing = logic.format_operator_list(
            {
                "synced_chars": [
                    {"name_cn": "陈", "level": 90, "evolve_phase": 2}
                ],
                "pages": 1,
            }
        )
        self.assertIn("陈", listing)
        self.assertIn("Lv.90", listing)
        panel = logic.format_operator_panel(
            {"panel": {"name_cn": "陈", "summary": {"level": 90, "attack": 1000}}}
        )
        self.assertIn("陈", panel)
        self.assertIn("攻击 1000", panel)

    def test_format_crisis_calendar_sync(self):
        crisis = logic.format_crisis_contract(
            {
                "crisisContract": {
                    "name": "净罪",
                    "score": 100,
                    "indicators": [{"name": "目标：+1"}],
                }
            }
        )
        self.assertIn("净罪", crisis)
        self.assertIn("得分 100", crisis)
        calendar = logic.format_calendar(
            {"activities": [{"name": "活动A", "activity_start_at_ts": 1, "activity_end_at_ts": 2}]}
        )
        self.assertIn("活动A", calendar)
        sync = logic.format_sync_status({"status": "completed", "new_records": 5})
        self.assertIn("新增 5", sync)

    def test_sync_quota(self):
        gate = logic.UserGate(debounce_seconds=0, sync_limit=1)
        ok, _ = gate.check("1", "sync", now=100, day="2026-10-31")
        self.assertTrue(ok)
        ok, _ = gate.check("1", "sync", now=101, day="2026-10-31")
        self.assertFalse(ok, "同步 1 次/天")

    def test_parse_2b_commands(self):
        for name in ("成就列表", "帝江号建设", "地区建设", "全服统计"):
            self.assertEqual(logic.parse_command(name), logic.Command(name))
            self.assertEqual(logic.action_of(logic.Command(name)), "query")

    def test_format_2b(self):
        achievements = logic.format_achievements(
            {"achievements": [{"name": "A", "completed": True}, {"name": "B"}]}
        )
        self.assertIn("已完成 1/2", achievements)
        self.assertIn("A", achievements)

        spaceship = logic.format_spaceship(
            {"spaceship": {"level": 3, "power": 100, "modules": [{}, {}]}}
        )
        self.assertIn("等级 3", spaceship)
        self.assertIn("电力 100", spaceship)
        self.assertIn("模块 2", spaceship)

        domain = logic.format_domain(
            {"domain": {"level": 2, "money": 50, "settlements": [{}, {}]}}
        )
        self.assertIn("等级 2", domain)
        self.assertIn("调度券 50", domain)
        self.assertIn("聚落 2", domain)

        global_stats = logic.format_global_stats(
            {"stats": {"total_count": 10, "star6_count": 1}}
        )
        self.assertIn("全服统计", global_stats)
        self.assertIn("总抽数 10", global_stats)

    def test_parse_subscription_commands(self):
        for name in (
            "订阅公告",
            "取消订阅公告",
            "订阅理智",
            "取消订阅理智",
            "订阅调度券",
            "取消订阅调度券",
            "订阅签到",
            "取消订阅签到",
            "订阅列表",
        ):
            self.assertEqual(logic.parse_command(name), logic.Command(name))
            self.assertEqual(logic.action_of(logic.Command(name)), "subscribe")
        self.assertEqual(logic.parse_command("公告"), logic.Command("公告"))
        self.assertEqual(logic.parse_command("公告最新"), logic.Command("公告最新"))
        self.assertEqual(logic.action_of(logic.Command("公告")), "query")

    def test_format_announcements_and_subscriptions(self):
        latest = logic.format_announcement_latest(
            {
                "title": "公告A",
                "summary": "摘要",
                "published_at_ts": 1700000000,
            }
        )
        self.assertIn("公告A", latest)
        self.assertIn("摘要", latest)
        listing = logic.format_announcement_list(
            {"items": [{"title": "公告B", "published_at_ts": 1700000000}]}
        )
        self.assertIn("公告B", listing)
        subscriptions = logic.format_subscription_list(
            {"enabled": True},
            {"enabled": False},
            None,
            announcements={"123": {"enabled": True}},
        )
        self.assertIn("理智：开", subscriptions)
        self.assertIn("调度券：关", subscriptions)
        self.assertIn("自动签到：关", subscriptions)
        self.assertIn("公告群：123", subscriptions)


if __name__ == "__main__":
    unittest.main()
