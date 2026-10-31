"""Endfield 配置用例（MIT）。"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from endfield import config as cfg  # noqa: E402


class EndfieldConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "endfield.json"

    def test_missing_file_uses_defaults(self):
        config = cfg.load_config(self.path)
        self.assertTrue(config.enabled)
        self.assertEqual(config.announcement_poll_minutes, 10)
        self.assertEqual(config.render_url, "http://127.0.0.1:3000")
        self.assertEqual(config.log_level, "INFO")

    def test_roundtrip(self):
        config = cfg.EndfieldConfig(
            enabled=False,
            announcement_poll_minutes=30,
            signin_time="08:30",
            quiet_start="00:00",
            quiet_end="07:00",
            render_url="http://10.0.0.226:3000",
            render_timeout_seconds=40,
            log_level="WARNING",
        )
        cfg.save_config(config, self.path)
        saved = cfg.load_config(self.path)
        self.assertFalse(saved.enabled)
        self.assertEqual(saved.announcement_poll_minutes, 30)
        self.assertEqual(saved.signin_time, "08:30")
        self.assertEqual(saved.quiet_start, "00:00")
        self.assertEqual(saved.quiet_end, "07:00")
        self.assertEqual(saved.render_url, "http://10.0.0.226:3000")
        self.assertEqual(saved.render_timeout_seconds, 40)
        self.assertEqual(saved.log_level, "WARNING")

    def test_invalid_values_fallback(self):
        self.path.write_text(
            json.dumps(
                {
                    "enabled": "yes",
                    "announcement_poll_minutes": 1,
                    "sanity_poll_minutes": 999,
                    "ticket_poll_minutes": "abc",
                    "signin_time": "25:00",
                    "signin_jitter_minutes": -1,
                    "quiet_start": "23:99",
                    "daily_push_limit": 101,
                    "push_retry": 4,
                    "push_disable_after_failures": 0,
                    "render_timeout_seconds": 0,
                    "log_level": "TRACE",
                    "audit_max_entries": 1,
                    "unknown": "ignored",
                }
            ),
            encoding="utf-8",
        )
        config = cfg.load_config(self.path)
        self.assertTrue(config.enabled)
        self.assertEqual(config.announcement_poll_minutes, 10)
        self.assertEqual(config.sanity_poll_minutes, 20)
        self.assertEqual(config.ticket_poll_minutes, 20)
        self.assertEqual(config.signin_time, "00:05")
        self.assertEqual(config.signin_jitter_minutes, 60)
        self.assertEqual(config.quiet_start, "23:00")
        self.assertEqual(config.daily_push_limit, 10)
        self.assertEqual(config.push_retry, 1)
        self.assertEqual(config.push_disable_after_failures, 3)
        self.assertEqual(config.render_timeout_seconds, 20)
        self.assertEqual(config.log_level, "INFO")
        self.assertEqual(config.audit_max_entries, 200)

    def test_render_url_allows_private_and_rejects_public(self):
        allowed = cfg.EndfieldConfig.from_mapping(
            {"render_url": "http://10.0.0.226:3000"}
        )
        self.assertEqual(allowed.render_url, "http://10.0.0.226:3000")

        localhost = cfg.EndfieldConfig.from_mapping(
            {"render_url": "http://127.0.0.1:3000/render"}
        )
        self.assertEqual(localhost.render_url, "http://127.0.0.1:3000/render")

        public = cfg.EndfieldConfig.from_mapping(
            {"render_url": "http://example.com:3000"}
        )
        self.assertEqual(public.render_url, cfg.DEFAULT_RENDER_URL)

        scheme = cfg.EndfieldConfig.from_mapping(
            {"render_url": "ftp://127.0.0.1:3000"}
        )
        self.assertEqual(scheme.render_url, cfg.DEFAULT_RENDER_URL)

        credentials = cfg.EndfieldConfig.from_mapping(
            {"render_url": "http://user:pass@127.0.0.1:3000"}
        )
        self.assertEqual(credentials.render_url, cfg.DEFAULT_RENDER_URL)

    def test_bad_json_raises(self):
        self.path.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(cfg.ConfigError):
            cfg.load_config(self.path)

    def test_non_object_raises(self):
        self.path.write_text("[]", encoding="utf-8")
        with self.assertRaises(cfg.ConfigError):
            cfg.load_config(self.path)

    def test_mtime_reload(self):
        cfg.save_config(cfg.EndfieldConfig(enabled=True), self.path)
        cached, stamp = cfg.load_with_stamp(self.path)
        same, same_stamp, changed = cfg.reload_if_changed(
            self.path, cached, stamp
        )
        self.assertFalse(changed)
        self.assertIs(same, cached)
        self.assertEqual(same_stamp, stamp)

        time.sleep(0.02)
        cfg.save_config(cfg.EndfieldConfig(enabled=False), self.path)
        fresh, new_stamp, changed = cfg.reload_if_changed(
            self.path, cached, stamp
        )
        self.assertTrue(changed)
        self.assertFalse(fresh.enabled)
        self.assertNotEqual(new_stamp, stamp)

    def test_public_view_has_no_secret_fields(self):
        view = cfg.public_view(cfg.EndfieldConfig())
        self.assertIn("render_url", view)
        self.assertNotIn("api_key", view)
        self.assertNotIn("framework_token", view)


if __name__ == "__main__":
    unittest.main()
