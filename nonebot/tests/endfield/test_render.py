"""Endfield 远程渲染客户端用例（MockTransport，不发网络）。"""

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from endfield.render import MAX_HTML_BYTES, RenderClient, RenderError  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


class RenderClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        if getattr(self, "client", None):
            await self.client.close()

    def _make(self, handler):
        self.client = RenderClient(
            "http://127.0.0.1:3000",
            transport=httpx.MockTransport(handler),
        )
        return self.client

    async def test_success_posts_expected_payload(self):
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["json"] = request.content.decode()
            return httpx.Response(
                200, content=PNG, headers={"content-type": "image/png"}
            )

        client = self._make(handler)
        data = await client.render_html("<html>ok</html>", width=900, height=1200)
        self.assertEqual(data, PNG)
        self.assertEqual(seen["path"], "/render")
        self.assertIn('"width"', seen["json"])
        self.assertIn("html", seen["json"])

    async def test_too_large_html_is_rejected_before_request(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

        client = self._make(handler)
        with self.assertRaises(RenderError):
            await client.render_html("x" * (MAX_HTML_BYTES + 1))
        self.assertEqual(calls, [])

    async def test_invalid_dimensions_are_rejected(self):
        client = self._make(lambda request: httpx.Response(200))
        with self.assertRaises(RenderError):
            await client.render_html("<html>x</html>", width=100)
        with self.assertRaises(RenderError):
            await client.render_html("<html>x</html>", height=100)
        with self.assertRaises(RenderError):
            await client.render_html("<html>x</html>", timeout_ms=0)

    async def test_429_is_render_error(self):
        client = self._make(lambda request: httpx.Response(429, text="busy"))
        with self.assertRaises(RenderError) as ctx:
            await client.render_html("<html>x</html>")
        self.assertEqual(ctx.exception.status, 429)

    async def test_non_png_is_render_error(self):
        client = self._make(
            lambda request: httpx.Response(
                200, text="<html>", headers={"content-type": "text/html"}
            )
        )
        with self.assertRaises(RenderError):
            await client.render_html("<html>x</html>")

    async def test_empty_body_is_render_error(self):
        client = self._make(
            lambda request: httpx.Response(
                200, content=b"", headers={"content-type": "image/png"}
            )
        )
        with self.assertRaises(RenderError):
            await client.render_html("<html>x</html>")

    async def test_5xx_is_render_error(self):
        client = self._make(lambda request: httpx.Response(503, text="down"))
        with self.assertRaises(RenderError):
            await client.render_html("<html>x</html>")

    async def test_health(self):
        def handler(request):
            self.assertEqual(request.url.path, "/health")
            return httpx.Response(200, json={"ok": True, "ready": True})

        client = self._make(handler)
        data = await client.health()
        self.assertTrue(data["ok"])

    async def test_connect_error_is_render_error(self):
        def handler(request):
            raise httpx.ConnectError("boom", request=request)

        client = self._make(handler)
        with self.assertRaises(RenderError):
            await client.render_html("<html>x</html>")


if __name__ == "__main__":
    unittest.main()
