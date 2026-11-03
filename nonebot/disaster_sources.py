# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""Wolfx 免鉴权 HTTP 地震源。

第一版只轮询，不建 WebSocket：接口简单、失败可见、断线不需要状态机。
端点来自 Wolfx Open API：
- https://api.wolfx.jp/cenc_eew.json
- https://api.wolfx.jp/cenc_eqlist.json
- https://api.wolfx.jp/jma_eew.json
- https://api.wolfx.jp/jma_eqlist.json
"""
from __future__ import annotations

from typing import Any

import httpx

BASE_URL = 'https://api.wolfx.jp'
ENDPOINTS = {
    'cenc_eew': f'{BASE_URL}/cenc_eew.json',
    'cenc_list': f'{BASE_URL}/cenc_eqlist.json',
    'jma_eew': f'{BASE_URL}/jma_eew.json',
    'jma_list': f'{BASE_URL}/jma_eqlist.json',
}


class SourceError(RuntimeError):
    """某个源这一次没拿到可用数据；调用方记录错误后继续轮询。"""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(f'{kind}: {message}')


class WolfxSource:
    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 10.0):
        self._client = client
        self._owned = client is None
        self._timeout = timeout

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={'User-Agent': 'denia-bot-disaster/0.1'},
            )
        return self._client

    async def fetch(self, kind: str) -> dict[str, Any]:
        url = ENDPOINTS.get(kind)
        if not url:
            raise SourceError(kind, '未知端点')
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # 连接/SSL 异常常见 `str(exc)==''`；至少留下异常类型，状态命令才看得出问题。
            detail = str(exc).strip() or type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f'HTTP {exc.response.status_code}'
            raise SourceError(kind, detail) from exc
        if not isinstance(data, dict):
            raise SourceError(kind, '返回不是 JSON 对象')
        return data

    async def cenc_eew(self) -> dict[str, Any]:
        return await self.fetch('cenc_eew')

    async def cenc_list(self) -> dict[str, Any]:
        return await self.fetch('cenc_list')

    async def jma_eew(self) -> dict[str, Any]:
        return await self.fetch('jma_eew')

    async def jma_list(self) -> dict[str, Any]:
        return await self.fetch('jma_list')

    async def close(self) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()
            self._client = None
