# 移植自 Entropy-Increase-Team/astrbot_plugin_endfield v3.0.3（commit 41482b7）
# 的 core/render.py；上游作者：Entropy-Increase-Team。
# 本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 改为自包含 HTML + POST /render，不启动本地 Chromium；
# 详见 docs/third-party/endfield-NOTICE.md

"""Endfield 远程渲染客户端（AGPL-3.0-only）。

- 只发 HTML 到 denia 的 `POST /render`；不 import nonebot/muicebot。
- 限制与 renderer 一致：HTML ≤2 MB、宽 320–1600、高 200–2400、超时 1–60 s。
- 429/5xx/非 PNG/空 body 都抛 `RenderError`，由调用方回退文本。
"""

from __future__ import annotations

from typing import Any

import httpx

MAX_HTML_BYTES = 2 * 1024 * 1024
MIN_WIDTH, MAX_WIDTH = 320, 1600
MIN_HEIGHT, MAX_HEIGHT = 200, 2400
MIN_TIMEOUT_MS, MAX_TIMEOUT_MS = 1_000, 60_000


class RenderError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, path: str = "/render"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.path = path


class RenderClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 25.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = str(base_url or "").rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "RenderClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def render_html(
        self,
        html: str,
        *,
        width: int = 900,
        height: int = 1200,
        timeout_ms: int = 20_000,
    ) -> bytes:
        if not isinstance(html, str) or not html.strip():
            raise RenderError("empty html")
        encoded = html.encode("utf-8")
        if len(encoded) > MAX_HTML_BYTES:
            raise RenderError(
                f"html too large: {len(encoded)} > {MAX_HTML_BYTES} bytes"
            )
        if not (MIN_WIDTH <= int(width) <= MAX_WIDTH):
            raise RenderError(f"width out of range: {width}")
        if not (MIN_HEIGHT <= int(height) <= MAX_HEIGHT):
            raise RenderError(f"height out of range: {height}")
        if not (MIN_TIMEOUT_MS <= int(timeout_ms) <= MAX_TIMEOUT_MS):
            raise RenderError(f"timeout out of range: {timeout_ms}")

        response = await self._client.post(
            f"{self.base_url}/render",
            json={
                "html": html,
                "width": int(width),
                "height": int(height),
                "timeout_ms": int(timeout_ms),
            },
        )
        if response.status_code == 429:
            raise RenderError("renderer busy", status=429)
        if response.status_code >= 500:
            raise RenderError(
                f"renderer error {response.status_code}", status=response.status_code
            )
        if response.status_code != 200:
            raise RenderError(
                f"renderer http {response.status_code}", status=response.status_code
            )
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        if content_type != "image/png":
            raise RenderError(f"unexpected content-type: {content_type or 'none'}")
        data = response.content
        if not data or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RenderError("renderer did not return png")
        return data

    async def health(self) -> dict[str, Any]:
        try:
            response = await self._client.get(f"{self.base_url}/health")
        except httpx.RequestError as exc:
            raise RenderError(f"health failed: {exc}") from exc
        if response.status_code != 200:
            raise RenderError(
                f"health http {response.status_code}", status=response.status_code, path="/health"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise RenderError("health is not json", path="/health") from exc
        return data if isinstance(data, dict) else {}
