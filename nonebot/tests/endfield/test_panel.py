"""Endfield 面板后端用例（TestClient，不发真实网络）。"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from endfield import config as ecfg  # noqa: E402
from endfield import store as estore  # noqa: E402
from endfield_panel import register  # noqa: E402


class PanelTest(unittest.TestCase):
    def test_partial_config_preserves_existing_fields(self):
        self._seed_config()
        self.client.post('/api/endfield/config', json={'quiet_start': '21:30'})
        result = self.client.post('/api/endfield/config', json={'enabled': False})
        self.assertEqual(result.json()['quiet_start'], '21:30')
        self.assertEqual(result.json()['render_url'], 'http://127.0.0.1:3000')

    def test_test_key_uses_unsaved_input_without_persisting(self):
        self._seed_key()
        fake = AsyncMock()
        with patch('endfield_panel.ec.EndfieldClient', return_value=fake) as factory:
            result = self.client.post('/api/endfield/key/test', json={'api_key': 'unsaved-key-12345'})
        self.assertTrue(result.json()['ok'])
        self.assertEqual(factory.call_args.args[0], 'unsaved-key-12345')
        self.assertEqual(self.client.get('/api/endfield/key').json()['masked'], '****3456')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "data" / "endfield").mkdir(parents=True)
        self.env_backup = os.environ.pop("ENDFIELD_API_KEY", None)
        self.addCleanup(self._restore_env)
        self.auth_calls = 0
        self.mutate_calls = 0

        def authorized(req):
            self.auth_calls += 1

        def mutate(req):
            self.mutate_calls += 1

        async def render_health(config):
            return {"ok": True, "ready": True}

        app = FastAPI()
        register(
            app,
            authorized=authorized,
            mutate=mutate,
            root=self.root,
            render_health=render_health,
        )
        self.client = TestClient(app)

    def _restore_env(self):
        if self.env_backup is not None:
            os.environ["ENDFIELD_API_KEY"] = self.env_backup

    def _seed_config(self):
        ecfg.save_config(
            ecfg.EndfieldConfig(enabled=True, render_url="http://127.0.0.1:3000"),
            self.root / "data" / "endfield.json",
        )

    def _seed_key(self, key="panel-key-123456"):
        (self.root / "data" / "endfield_secrets.json").write_text(
            json.dumps({"api_key": key}), encoding="utf-8"
        )

    def _seed_bindings(self):
        async def run():
            store = estore.BindingStore(self.root / "data" / "endfield" / "bindings.json")
            await store.set_consent("100")
            await store.add_binding(
                "100",
                {
                    "role_id": "role-12345678",
                    "server_id": 1,
                    "role_name": "角色A",
                    "framework_token": "secret-token",
                },
            )

        asyncio.run(run())

    def _seed_subscriptions(self):
        async def run():
            store = estore.SubscriptionStore(
                self.root / "data" / "endfield" / "subscriptions.json"
            )
            await store.add_announcement(123, 100)
            await store.set_personal("sanity", 100)

        asyncio.run(run())

    def test_key_masked_and_written(self):
        self._seed_config()
        self._seed_key()
        data = self.client.get("/api/endfield/key").json()
        self.assertTrue(data["configured"])
        self.assertEqual(data["masked"], "****3456")
        self.assertEqual(data["source"], "panel")
        self.assertNotIn("panel-key", json.dumps(data))

        result = self.client.post(
            "/api/endfield/key", json={"api_key": "new-panel-key-987654"}
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["masked"], "****7654")
        self.assertEqual(self.mutate_calls, 1)
        self.assertNotIn("new-panel-key", result.text)

        deleted = self.client.delete("/api/endfield/key")
        self.assertFalse(deleted.json()["configured"])

    def test_config_get_post(self):
        self._seed_config()
        data = self.client.get("/api/endfield/config").json()
        self.assertTrue(data["enabled"])
        result = self.client.post(
            "/api/endfield/config",
            json={"enabled": False, "announcement_poll_minutes": 30},
        )
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["enabled"])
        self.assertEqual(result.json()["announcement_poll_minutes"], 30)
        self.assertEqual(self.mutate_calls, 1)

    def test_status_and_readonly_data(self):
        self._seed_config()
        self._seed_key()
        self._seed_bindings()
        self._seed_subscriptions()
        heartbeat = {
            "ts": 1700000000,
            "last_announcement_poll": 1700000000,
        }
        (self.root / "data" / "endfield" / "heartbeat.json").write_text(
            json.dumps(heartbeat), encoding="utf-8"
        )
        (self.root / "data" / "endfield" / "audit.jsonl").write_text(
            json.dumps({"event": "test", "token": "[REDACTED]"}) + "\n",
            encoding="utf-8",
        )

        status = self.client.get("/api/endfield/status").json()
        self.assertTrue(status["renderer"]["ok"])
        self.assertEqual(status["bindings"]["items"], 1)
        self.assertEqual(status["subscriptions"]["announcement_groups"], 1)
        self.assertNotIn("secret-token", json.dumps(status))
        self.assertNotIn("framework_token", json.dumps(status))

        bindings = self.client.get("/api/endfield/bindings").json()
        self.assertEqual(bindings["count"], 1)
        self.assertNotIn("framework_token", json.dumps(bindings))
        self.assertNotIn("secret-token", json.dumps(bindings))
        self.assertNotIn("100", json.dumps(bindings), "不暴露 QQ")
        self.assertEqual(bindings["items"][0]["role_id"], "ro****78")

        subs = self.client.get("/api/endfield/subscriptions").json()
        self.assertEqual(subs["counts"]["sanity"], 1)
        audit = self.client.get("/api/endfield/audit").json()
        self.assertEqual(audit["items"][0]["event"], "test")
        self.assertEqual(self.auth_calls, 4)

    def test_page(self):
        page = self.client.get("/endfield", follow_redirects=False)
        self.assertEqual(page.status_code, 307)
        self.assertEqual(page.headers["location"], "/#endfield")


if __name__ == "__main__":
    unittest.main()
