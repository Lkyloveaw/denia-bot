"""Endfield 客户端用例（MockTransport，不发网络）。"""

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from endfield.client import (  # noqa: E402
    AUTH_FRONTEND_URL,
    EndfieldAuthError,
    EndfieldClient,
    EndfieldConfigError,
    EndfieldProtocolError,
    EndfieldRateLimitError,
    EndfieldTransientError,
)


class EndfieldClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []

    async def asyncTearDown(self):
        if getattr(self, "client", None):
            await self.client.close()

    def _make(self, handler, **kwargs):
        self.client = EndfieldClient(
            "test-key",
            transport=httpx.MockTransport(handler),
            bot_qq="bot-qq",
            user_qq="user-qq",
            **kwargs,
        )
        return self.client

    async def test_headers_are_per_request(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(200, json={"code": 0, "data": {"ok": True}})

        client = self._make(handler)
        data = await client.get_note("token-abc", "role-1", 1)
        self.assertTrue(data["ok"])
        self.assertEqual(seen["x-api-key"], "test-key")
        self.assertEqual(seen["x-framework-token"], "token-abc")
        self.assertEqual(seen["client_id"], "bot-qq")
        self.assertEqual(seen["platform_id"], "user-qq")
        self.assertEqual(seen["platform"], "bot")

    async def test_success_returns_data(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/endfield/stamina")
            self.assertEqual(request.url.params.get("roleId"), "role-1")
            return httpx.Response(200, json={"code": 0, "data": {"stamina": {"current": 1}}})

        client = self._make(handler)
        data = await client.get_stamina("token", "role-1", 1)
        self.assertEqual(data["stamina"]["current"], 1)

    async def test_missing_key_is_config_error(self):
        client = EndfieldClient("", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        with self.assertRaises(EndfieldConfigError):
            await client.get_note("token", "role")
        await client.close()

    async def test_401_api_key(self):
        def handler(request):
            return httpx.Response(
                401, json={"code": 401, "message": "API Key 无效: 无效的 API Key 格式"}
            )

        client = self._make(handler)
        with self.assertRaises(EndfieldConfigError):
            await client.get_announcement_latest()

    async def test_401_invalid_credential_is_auth(self):
        def handler(request):
            return httpx.Response(401, json={"code": 401, "message": "无效的凭证"})

        client = self._make(handler)
        with self.assertRaises(EndfieldAuthError):
            await client.get_note("token", "role")

    async def test_403_is_auth(self):
        def handler(request):
            return httpx.Response(
                403,
                json={
                    "code": 40301,
                    "message": "无效的 Framework Token 或会话已过期，请重新登录获取",
                },
            )

        client = self._make(handler)
        with self.assertRaises(EndfieldAuthError):
            await client.get_note("token", "role")

    async def test_400_missing_framework_token_is_auth(self):
        def handler(request):
            return httpx.Response(400, json={"code": 400, "message": "缺少 framework_token"})

        client = self._make(handler)
        with self.assertRaises(EndfieldAuthError):
            await client.get_note("token", "role")

    async def test_429_html_is_rate_limit(self):
        def handler(request):
            return httpx.Response(429, text="<html>rate limited</html>")

        client = self._make(handler)
        with self.assertRaises(EndfieldRateLimitError):
            await client.get_announcement_latest()

    async def test_get_524_retries_once(self):
        self.calls.clear()

        def handler(request):
            self.calls.append(request.method)
            return httpx.Response(524, text="")

        client = self._make(handler)
        with self.assertRaises(EndfieldTransientError):
            await client.get_note("token", "role")
        self.assertEqual(len(self.calls), 2)

    async def test_post_524_does_not_retry(self):
        self.calls.clear()

        def handler(request):
            self.calls.append(request.method)
            return httpx.Response(524, text="")

        client = self._make(handler)
        with self.assertRaises(EndfieldTransientError):
            await client.post_attendance("token", "role", 1)
        self.assertEqual(len(self.calls), 1)

    async def test_get_connect_error_retries_once(self):
        self.calls.clear()

        def handler(request):
            self.calls.append(request.method)
            if len(self.calls) == 1:
                raise httpx.ConnectError("boom", request=request)
            return httpx.Response(200, json={"code": 0, "data": {"ok": True}})

        client = self._make(handler)
        data = await client.get_note("token", "role")
        self.assertTrue(data["ok"])
        self.assertEqual(len(self.calls), 2)

    async def test_404_text_is_protocol(self):
        def handler(request):
            return httpx.Response(404, text="404 page not found")

        client = self._make(handler)
        with self.assertRaises(EndfieldProtocolError):
            await client.get_note("token", "role")

    async def test_200_code_nonzero_is_protocol(self):
        def handler(request):
            return httpx.Response(200, json={"code": 500, "message": "bad"})

        client = self._make(handler)
        with self.assertRaises(EndfieldProtocolError):
            await client.get_note("token", "role")

    async def test_auth_url_prefix(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"auth_url": "/auth/req-1", "request_id": "req-1"},
                },
            )

        client = self._make(handler)
        data = await client.create_authorization_request()
        self.assertEqual(data["auth_url"], AUTH_FRONTEND_URL + "/auth/req-1")

    async def test_get_bindings_returns_list(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"bindings": [{"id": "b1"}, {"id": "b2"}]}},
            )

        client = self._make(handler)
        bindings = await client.get_bindings("user-qq")
        self.assertEqual([item["id"] for item in bindings], ["b1", "b2"])

    async def test_gacha_records_and_stats(self):
        def handler(request):
            if request.url.path.endswith("/records"):
                self.assertEqual(request.url.params.get("page"), "2")
                self.assertEqual(request.url.params.get("limit"), "20")
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"records": [{"rarity": 6}], "pages": 3}},
                )
            return httpx.Response(
                200, json={"code": 0, "data": {"stats": {"total_count": 10}}}
            )

        client = self._make(handler)
        records = await client.get_gacha_records("token", page=2, limit=20)
        self.assertEqual(records["pages"], 3)
        stats = await client.get_gacha_stats("token")
        self.assertEqual(stats["stats"]["total_count"], 10)

    async def test_gacha_fetch_posts_role_id(self):
        seen = {}

        def handler(request):
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"code": 0, "data": {"status": "started"}})

        client = self._make(handler)
        data = await client.post_gacha_fetch("token", "role-1")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/api/endfield/gacha/fetch")
        self.assertIn("role_id", seen["body"])
        self.assertIn("role-1", seen["body"])
        self.assertEqual(data["status"], "started")

    async def test_panel_chars_all_pagination(self):
        self.calls = []

        def handler(request):
            page = request.url.params.get("page")
            self.calls.append(page)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "synced_chars": [{"name": f"角色{page}"}],
                        "pages": 2,
                    },
                },
            )

        client = self._make(handler)
        rows = await client.get_panel_chars_all("token", page_size=1)
        self.assertEqual([row["name"] for row in rows], ["角色1", "角色2"])
        self.assertEqual(self.calls, ["1", "2"])

    async def test_card_detail_and_crisis_params(self):
        seen = []

        def handler(request):
            seen.append((request.url.path, dict(request.url.params)))
            if request.url.path.endswith("/card/detail"):
                return httpx.Response(200, json={"code": 0, "data": {"ok": True}})
            return httpx.Response(200, json={"code": 0, "data": {"ok": True}})

        client = self._make(handler)
        await client.get_card_detail("token", "role-1", 2)
        await client.get_crisis_contract("token", "c-1", role_id="role-1", server_id=2)
        self.assertEqual(seen[0][0], "/api/endfield/card/detail")
        self.assertEqual(seen[0][1], {"roleId": "role-1", "serverId": "2", "userId": "role-1"})
        self.assertEqual(seen[1][0], "/api/endfield/card/crisis-contract")
        self.assertEqual(seen[1][1]["contractId"], "c-1")

    async def test_wiki_and_panel_sync(self):
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path))
            return httpx.Response(200, json={"code": 0, "data": {"ok": True}})

        client = self._make(handler)
        await client.get_wiki_activities()
        await client.sync_panel("token")
        await client.get_panel_sync_status("token")
        self.assertEqual(
            seen,
            [
                ("GET", "/api/wiki/activities"),
                ("POST", "/api/panel/sync"),
                ("GET", "/api/panel/sync/status"),
            ],
        )

    async def test_panel_char_path(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/panel/char/tpl-1")
            return httpx.Response(200, json={"code": 0, "data": {"panel": {}}})

        client = self._make(handler)
        data = await client.get_panel_char("token", "tpl-1")
        self.assertIn("panel", data)

    async def test_2b_endpoints(self):
        seen = []

        def handler(request):
            seen.append((request.url.path, dict(request.url.params), request.headers.get("x-framework-token")))
            return httpx.Response(200, json={"code": 0, "data": {"ok": True}})

        client = self._make(handler)
        await client.get_achieve("token", "role-1", 2)
        await client.get_spaceship("token", "role-1", 2)
        await client.get_domain("token", "role-1", 2)
        await client.get_global_stats()
        self.assertEqual(
            [item[0] for item in seen],
            [
                "/api/endfield/achieve",
                "/api/endfield/spaceship",
                "/api/endfield/domain",
                "/api/endfield/gacha/global-stats",
            ],
        )
        self.assertEqual(seen[0][1], {"roleId": "role-1", "serverId": "2"})
        self.assertEqual(seen[0][2], "token")
        self.assertIsNone(seen[3][2], "全服统计是公开端点，不带用户 token")


if __name__ == "__main__":
    unittest.main()
