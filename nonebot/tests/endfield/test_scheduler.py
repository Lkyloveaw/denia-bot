"""Endfield 调度器用例（MIT，不发网络）。"""

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from endfield.config import EndfieldConfig  # noqa: E402
from endfield.scheduler import (  # noqa: E402
    EndfieldScheduler,
    RecordingSink,
    _in_quiet,
    _signin_due,
)
from endfield.store import BindingStore, SubscriptionStore  # noqa: E402


class FakeClient:
    def __init__(self):
        self.latest = {
            "item_id": "a1",
            "published_at_ts": 100,
            "title": "公告一",
            "summary": "摘要",
        }
        self.stamina = {"stamina": {"current": 120, "max": 120}}
        self.domain = {
            "domain": [
                {
                    "name": "一区",
                    "settlements": [
                        {"name": "聚落A", "remainMoney": 50, "moneyMax": 50},
                        {"name": "聚落B", "remainMoney": 10, "moneyMax": 50},
                    ],
                }
            ]
        }
        self.attendance_calls = []
        self.closed = 0

    async def get_announcement_latest(self):
        return self.latest

    async def get_stamina(self, token, role_id, server_id):
        return self.stamina

    async def get_domain(self, token, role_id, server_id):
        return self.domain

    async def post_attendance(self, token, role_id, server_id):
        self.attendance_calls.append((role_id, server_id))
        return {"ok": True}

    async def close(self):
        self.closed += 1


def _binding(role_id="role-1", token="tok-1"):
    return {
        "role_id": role_id,
        "server_id": 1,
        "role_name": f"角色{role_id}",
        "framework_token": token,
    }


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.bindings = BindingStore(folder / "bindings.json")
        await self.bindings.set_consent("100")
        await self.bindings.add_binding("100", _binding())
        await self.bindings.add_binding("100", _binding("role-2", "tok-2"))
        self.subscriptions = SubscriptionStore(folder / "subscriptions.json")
        self.fake = FakeClient()
        self.sink = RecordingSink()
        self.config = EndfieldConfig(
            quiet_start="23:00",
            quiet_end="08:00",
            daily_push_limit=10,
            push_retry=0,
            push_disable_after_failures=2,
            signin_time="00:00",
            signin_jitter_minutes=0,
        )
        self.scheduler = EndfieldScheduler(
            bindings=self.bindings,
            subscriptions=self.subscriptions,
            client_factory=lambda qq: self.fake,
            sink=self.sink,
            config_provider=lambda: self.config,
            heartbeat_path=folder / "heartbeat.json",
        )

    async def test_announcement_dedupe(self):
        await self.subscriptions.add_announcement(123, 100, since_ts=0)
        now = datetime(2026, 10, 31, 12, 0)
        await self.scheduler.tick(now)
        self.assertEqual(len(self.sink.group_messages), 1)
        self.assertIn("公告一", self.sink.group_messages[0][1])
        subs = await self.subscriptions.list_announcements()
        self.assertEqual(subs["123"]["last_item_id"], "a1")
        self.assertEqual(subs["123"]["since_ts"], 100)

        await self.scheduler.tick(datetime(2026, 10, 31, 12, 1))
        self.assertEqual(len(self.sink.group_messages), 1, "同一 item 不重复推")

        self.fake.latest = {
            "item_id": "a2",
            "published_at_ts": 101,
            "title": "公告二",
        }
        await self.scheduler.tick(datetime(2026, 10, 31, 12, 2))
        self.assertEqual(len(self.sink.group_messages), 2)
        self.assertIn("公告二", self.sink.group_messages[1][1])

    async def test_sanity_full_once_and_reset(self):
        await self.subscriptions.set_personal("sanity", "100")
        now = datetime(2026, 10, 31, 12, 0)
        await self.scheduler.tick(now)
        self.assertEqual(len(self.sink.private_messages), 1)
        self.assertIn("理智已满", self.sink.private_messages[0][1])
        sub = (await self.subscriptions.list_personal("sanity"))["100"]
        self.assertEqual(sub["last_notified"], 1)

        await self.scheduler.tick(datetime(2026, 10, 31, 12, 1))
        self.assertEqual(len(self.sink.private_messages), 1, "满理智不重复推")

        self.fake.stamina = {"stamina": {"current": 10, "max": 120}}
        await self.scheduler.tick(datetime(2026, 10, 31, 12, 2))
        sub = (await self.subscriptions.list_personal("sanity"))["100"]
        self.assertEqual(sub["last_notified"], 0)

        self.fake.stamina = {"stamina": {"current": 120, "max": 120}}
        await self.scheduler.tick(datetime(2026, 10, 31, 12, 3))
        self.assertEqual(len(self.sink.private_messages), 2)

    async def test_tickets_full(self):
        await self.subscriptions.set_personal("tickets", "100")
        await self.scheduler.tick(datetime(2026, 10, 31, 12, 0))
        self.assertEqual(len(self.sink.private_messages), 1)
        self.assertIn("调度券已满", self.sink.private_messages[0][1])
        self.assertIn("聚落A", self.sink.private_messages[0][1])
        self.assertNotIn("聚落B", self.sink.private_messages[0][1])

    async def test_quiet_skips_personal(self):
        self.config = replace(self.config, quiet_start="00:00", quiet_end="23:59")
        await self.subscriptions.set_personal("sanity", "100")
        await self.scheduler.tick(datetime(2026, 10, 31, 12, 0))
        self.assertEqual(self.sink.private_messages, [])

    async def test_signin_once(self):
        self.config = replace(
            self.config, enable_subscriptions=False, enable_signin=True
        )
        await self.subscriptions.set_personal("signin", "100")
        now = datetime(2026, 10, 31, 1, 0)
        await self.scheduler.tick(now)
        self.assertEqual(len(self.fake.attendance_calls), 2)
        self.assertEqual(self.sink.private_messages, [], "成功静默")
        sub = (await self.subscriptions.list_personal("signin"))["100"]
        self.assertEqual(sub["last_run_date"], "2026-10-31")
        self.assertEqual(sub["last_result"], "ok")

        await self.scheduler.tick(datetime(2026, 10, 31, 2, 0))
        self.assertEqual(len(self.fake.attendance_calls), 2, "同日不重复签到")

    async def test_push_failure_disables(self):
        self.config = replace(
            self.config, push_retry=0, push_disable_after_failures=1
        )
        self.sink.fail_private = True
        await self.subscriptions.set_personal("sanity", "100")
        await self.scheduler.tick(datetime(2026, 10, 31, 12, 0))
        sub = (await self.subscriptions.list_personal("sanity"))["100"]
        self.assertFalse(sub["enabled"])
        self.assertEqual(sub["fail_count"], 1)

    async def test_heartbeat_written(self):
        await self.scheduler.tick(datetime(2026, 10, 31, 12, 0))
        path = Path(self.tmp.name) / "heartbeat.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["ts"], int(datetime(2026, 10, 31, 12, 0).timestamp()))

    def test_quiet_window_and_signin_due(self):
        self.assertTrue(
            _in_quiet(datetime(2026, 10, 31, 23, 30), EndfieldConfig())
        )
        self.assertTrue(
            _in_quiet(datetime(2026, 10, 31, 7, 30), EndfieldConfig())
        )
        self.assertFalse(
            _in_quiet(datetime(2026, 10, 31, 12, 0), EndfieldConfig())
        )
        config = EndfieldConfig(signin_time="08:00", signin_jitter_minutes=0)
        self.assertTrue(_signin_due(datetime(2026, 10, 31, 8, 1), config, "100"))
        self.assertFalse(_signin_due(datetime(2026, 10, 31, 7, 59), config, "100"))


if __name__ == "__main__":
    unittest.main()
