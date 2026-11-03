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
import render_client as shared_render

MAX_HTML_BYTES = 2 * 1024 * 1024
MIN_WIDTH, MAX_WIDTH = 320, 1600
MIN_HEIGHT, MAX_HEIGHT = 200, 2400
MIN_TIMEOUT_MS, MAX_TIMEOUT_MS = 1_000, 60_000


RenderError = shared_render.RenderError


class RenderClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 25.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = shared_render.base_url(base_url)
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, trust_env=False)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "RenderClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def render_html(self, html: str, *, width: int = 900, height: int = 1200,
                          timeout_ms: int = 20_000) -> bytes:
        return await shared_render.render_html(html, width=width, height=height,
            timeout_ms=timeout_ms, url=self.base_url, source='endfield', client=self._client)

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
