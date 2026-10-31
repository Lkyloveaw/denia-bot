"""Endfield 绑定/订阅存储用例（MIT）。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from endfield.store import (  # noqa: E402
    BindingLimitExceeded,
    BindingNotFound,
    BindingStore,
    ConsentRequired,
    StoreError,
    SubscriptionStore,
)


def _binding(role_id="10001", token="token-a", **extra):
    value = {
        "role_id": role_id,
        "server_id": 1,
        "role_name": f"角色{role_id}",
        "framework_token": token,
    }
    value.update(extra)
    return value


class BindingStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "bindings.json"
        self.store = BindingStore(self.path)

    async def test_consent_required(self):
        with self.assertRaises(ConsentRequired):
            await self.store.add_binding("100", _binding())
        self.assertFalse(self.path.exists(), "未同意不应落盘")

    async def test_first_binding_is_primary(self):
        await self.store.set_consent("100")
        created = await self.store.add_binding("100", _binding())
        self.assertTrue(created["is_primary"])
        bindings = await self.store.list_bindings("100")
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["status"], "valid")

    async def test_dedup_updates_existing(self):
        await self.store.set_consent("100")
        await self.store.add_binding("100", _binding(token="old"))
        await self.store.add_binding("100", _binding(token="new"))
        bindings = await self.store.list_bindings("100")
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["framework_token"], "new")

    async def test_max_three(self):
        await self.store.set_consent("100")
        for index in range(3):
            await self.store.add_binding(
                "100", _binding(role_id=f"role-{index}", token=f"t{index}")
            )
        with self.assertRaises(BindingLimitExceeded):
            await self.store.add_binding("100", _binding(role_id="role-4"))

    async def test_set_primary_and_remove_promotes(self):
        await self.store.set_consent("100")
        await self.store.add_binding("100", _binding(role_id="a", token="ta"))
        await self.store.add_binding("100", _binding(role_id="b", token="tb"))
        await self.store.set_primary("100", 2)
        current = await self.store.get_primary("100")
        self.assertEqual(current["role_id"], "b")
        await self.store.remove_binding("100", 2)
        current = await self.store.get_primary("100")
        self.assertEqual(current["role_id"], "a")
        self.assertTrue(current["is_primary"])

    async def test_remove_missing_raises(self):
        await self.store.set_consent("100")
        await self.store.add_binding("100", _binding())
        with self.assertRaises(BindingNotFound):
            await self.store.remove_binding("100", 9)

    async def test_mark_invalid_keeps_metadata(self):
        await self.store.set_consent("100")
        await self.store.add_binding("100", _binding())
        await self.store.mark_invalid("100", 1, "40301")
        bindings = await self.store.list_bindings("100")
        self.assertEqual(bindings[0]["status"], "invalid")
        self.assertEqual(bindings[0]["invalid_reason"], "40301")
        self.assertEqual(bindings[0]["role_id"], "10001")

    async def test_public_view_hides_token(self):
        await self.store.set_consent("100")
        await self.store.add_binding(
            "100", _binding(role_id="12345678", token="super-secret")
        )
        view = await self.store.public_bindings("100")
        self.assertEqual(len(view), 1)
        self.assertNotIn("framework_token", view[0])
        self.assertTrue(view[0]["token_configured"])
        self.assertEqual(view[0]["role_id"], "12****78")
        self.assertNotIn("super-secret", json.dumps(view, ensure_ascii=False))

    async def test_file_mode_0600_on_posix(self):
        if os.name == "nt":
            self.skipTest("POSIX permission check")
        await self.store.set_consent("100")
        await self.store.add_binding("100", _binding())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    async def test_bad_version_raises(self):
        self.path.write_text('{"version": 2, "users": {}}', encoding="utf-8")
        with self.assertRaises(StoreError):
            await self.store.load()


class SubscriptionStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "subscriptions.json"
        self.store = SubscriptionStore(self.path)

    async def test_announcement_lifecycle(self):
        await self.store.add_announcement(123, 100, since_ts=50, last_item_id="i50")
        items = await self.store.list_announcements()
        self.assertEqual(items["123"]["since_ts"], 50)
        self.assertEqual(items["123"]["last_item_id"], "i50")
        await self.store.update_announcement(
            123, since_ts=60, last_item_id="i60", fail_count=2
        )
        items = await self.store.list_announcements()
        self.assertEqual(items["123"]["since_ts"], 60)
        self.assertEqual(items["123"]["fail_count"], 2)
        self.assertTrue(await self.store.remove_announcement(123))
        self.assertEqual(await self.store.list_announcements(), {})

    async def test_personal_lifecycle(self):
        await self.store.set_personal("sanity", 100)
        await self.store.set_personal("tickets", 100)
        await self.store.set_personal("signin", 100, enabled=True)
        personal = await self.store.list_personal()
        self.assertIn("100", personal["sanity"])
        self.assertIn("100", personal["tickets"])
        self.assertIn("100", personal["signin"])
        self.assertEqual(personal["sanity"]["100"]["target"], "private:100")

        await self.store.update_last_notified("sanity", 100, 1)
        personal = await self.store.list_personal("sanity")
        self.assertEqual(personal["100"]["last_notified"], 1)
        self.assertTrue(await self.store.remove_personal("sanity", 100))
        self.assertNotIn("100", await self.store.list_personal("sanity"))

    async def test_meta(self):
        await self.store.update_meta("last_sanity_poll", 123)
        meta = await self.store.get_meta()
        self.assertEqual(meta["last_sanity_poll"], 123)

    async def test_unknown_kind_raises(self):
        with self.assertRaises(StoreError):
            await self.store.set_personal("unknown", 100)


if __name__ == "__main__":
    unittest.main()
