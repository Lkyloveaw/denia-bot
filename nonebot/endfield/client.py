# 移植自 Entropy-Increase-Team/astrbot_plugin_endfield v3.0.3（commit 41482b7）
# 的 core/client.py；上游作者：Entropy-Increase-Team。
# 本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 per-request header、错误分类、GET 重试与测试钩子；
# 详见 docs/third-party/endfield-NOTICE.md

"""Endfield 协议终端客户端（AGPL-3.0-only）。

- 不 import nonebot/muicebot；不读存储。
- 每请求显式传 `client_id`/`platform_id`，不用全局单例 caller。
- 429/401/403/400/404 不重试；GET 在 timeout/5xx/524 可重试 1 次；写不重试。
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

import httpx

BASE_URL = "https://end-api.shallow.ink"
AUTH_FRONTEND_URL = "https://end.shallow.ink"


class EndfieldError(RuntimeError):
    """所有终末地 API 错误的基类。"""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | None = None,
        path: str | None = None,
        payload: Any = None,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.path = path
        self.payload = payload


class EndfieldConfigError(EndfieldError):
    """API Key 缺失/格式错。"""


class EndfieldAuthError(EndfieldError):
    """用户未绑定 / framework token 缺失或失效。"""


class EndfieldRateLimitError(EndfieldError):
    """429（body 可能是 HTML）。"""


class EndfieldTransientError(EndfieldError):
    """timeout / 5xx / 524 / 连接错误。"""


class EndfieldProtocolError(EndfieldError):
    """404 / 非 JSON / 200+code!=0 / 字段异常。"""


class EndfieldClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 25.0,
        transport: httpx.AsyncBaseTransport | None = None,
        bot_qq: str = "",
        user_qq: str = "",
    ):
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or BASE_URL).rstrip("/")
        self.bot_qq = str(bot_qq or "")
        self.user_qq = str(user_qq or "")
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "EndfieldClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def _headers(self, framework_token: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "platform": "bot"}
        if self.bot_qq:
            headers["client_id"] = self.bot_qq
        if self.user_qq:
            headers["platform_id"] = self.user_qq
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if framework_token:
            headers["X-Framework-Token"] = framework_token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        framework_token: str | None = None,
    ) -> Any:
        if not self.api_key:
            raise EndfieldConfigError("missing api key", path=path)
        url = f"{self.base_url}{path}"
        headers = self._headers(framework_token)
        attempts = 2 if method.upper() == "GET" else 1
        last_error: EndfieldTransientError | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=dict(params) if params else None,
                    json=dict(body) if body else None,
                    headers=headers,
                )
            except httpx.RequestError as exc:
                last_error = EndfieldTransientError(
                    f"request failed: {exc}", path=path
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5)
                    continue
                raise last_error from exc

            status = response.status_code
            message = _response_message(response)
            if status == 429:
                raise EndfieldRateLimitError(
                    message or "rate limited",
                    status=status,
                    path=path,
                    payload=message,
                )
            if status in {502, 503, 504, 524}:
                error = EndfieldTransientError(
                    message or f"http {status}", status=status, path=path
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5)
                    continue
                raise error
            if status == 401:
                if "api key" in message.lower():
                    raise EndfieldConfigError(
                        message or "invalid api key", status=status, path=path
                    )
                raise EndfieldAuthError(
                    message or "invalid credential", status=status, path=path
                )
            if status == 403:
                raise EndfieldAuthError(
                    message or "forbidden", status=status, path=path
                )
            if status == 400:
                if "framework_token" in message.lower() or "framework token" in message.lower():
                    raise EndfieldAuthError(
                        message or "missing framework token", status=status, path=path
                    )
                raise EndfieldProtocolError(
                    message or "bad request", status=status, path=path
                )
            if status == 404:
                raise EndfieldProtocolError(
                    message or "not found", status=status, path=path
                )
            if status >= 500:
                error = EndfieldTransientError(
                    message or f"http {status}", status=status, path=path
                )
                if method.upper() == "GET" and attempt + 1 < attempts:
                    await asyncio.sleep(0.5)
                    continue
                raise error
            if status >= 400:
                raise EndfieldProtocolError(
                    message or f"http {status}", status=status, path=path
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise EndfieldProtocolError(
                    "response is not json", status=status, path=path
                ) from exc
            if not isinstance(data, dict):
                raise EndfieldProtocolError(
                    "response json is not an object", status=status, path=path
                )
            code = data.get("code")
            if code == 0:
                return data.get("data", data)
            msg = str(data.get("message") or data.get("msg") or "protocol error")
            if code in {401, 40301} or "framework token" in msg.lower():
                raise EndfieldAuthError(
                    msg, status=status, code=code if isinstance(code, int) else None, path=path
                )
            if code == 429:
                raise EndfieldRateLimitError(
                    msg, status=status, code=code if isinstance(code, int) else None, path=path
                )
            raise EndfieldProtocolError(
                msg, status=status, code=code if isinstance(code, int) else None, path=path
            )
        raise last_error or EndfieldTransientError("request failed", path=path)

    async def get_note(
        self, framework_token: str, role_id: str, server_id: int = 1
    ) -> Any:
        return await self._request(
            "GET",
            "/api/endfield/note",
            params={"roleId": role_id, "serverId": server_id},
            framework_token=framework_token,
        )

    async def get_stamina(
        self, framework_token: str, role_id: str, server_id: int = 1
    ) -> Any:
        return await self._request(
            "GET",
            "/api/endfield/stamina",
            params={"roleId": role_id, "serverId": server_id},
            framework_token=framework_token,
        )

    async def post_attendance(
        self, framework_token: str, role_id: str = "", server_id: int = 0
    ) -> Any:
        params: dict[str, Any] = {}
        if role_id:
            params["role_id"] = str(role_id)
        if server_id:
            params["server_id"] = int(server_id)
        return await self._request(
            "POST",
            "/api/endfield/attendance",
            params=params or None,
            framework_token=framework_token,
        )

    async def create_authorization_request(
        self,
        *,
        client_id: str = "",
        client_name: str = "denia-bot",
        client_type: str = "bot",
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "client_id": client_id or self.bot_qq or "denia-bot",
            "client_name": client_name,
            "client_type": client_type,
            "scopes": scopes
            or ["user_info", "binding_info", "game_data", "attendance"],
        }
        data = await self._request(
            "POST", "/api/v1/authorization/requests", body=payload
        )
        if isinstance(data, dict):
            auth_url = str(data.get("auth_url") or "")
            if auth_url.startswith("/"):
                data = {**data, "auth_url": AUTH_FRONTEND_URL + auth_url}
        return data if isinstance(data, dict) else {}

    async def get_authorization_status(self, request_id: str) -> dict[str, Any]:
        data = await self._request(
            "GET", f"/api/v1/authorization/requests/{request_id}/status"
        )
        return data if isinstance(data, dict) else {}

    async def create_binding(
        self,
        framework_token: str,
        user_id: str,
        *,
        is_primary: bool = True,
        provider: str = "skland",
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "framework_token": framework_token,
            "user_identifier": str(user_id),
            "client_type": "bot",
            "client_id": f"bot-{user_id}",
            "is_primary": bool(is_primary),
            "provider": provider,
        }
        payload.update(extra)
        data = await self._request("POST", "/api/v1/bindings", body=payload)
        return data if isinstance(data, dict) else {}

    async def get_bindings(
        self, user_id: str, *, provider: str = "skland"
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/api/v1/bindings",
            params={
                "user_identifier": str(user_id),
                "client_type": "bot",
                "provider": provider,
            },
        )
        if isinstance(data, dict) and isinstance(data.get("bindings"), list):
            return [item for item in data["bindings"] if isinstance(item, dict)]
        return []

    async def delete_binding(self, binding_id: str, user_id: str) -> bool:
        data = await self._request(
            "DELETE",
            f"/api/v1/bindings/{binding_id}",
            params={"user_identifier": str(user_id), "client_type": "bot"},
        )
        return data is not None

    async def get_announcement_latest(self) -> dict[str, Any]:
        data = await self._request("GET", "/api/announcements/latest")
        return data if isinstance(data, dict) else {}

    async def get_announcements(
        self, *, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/api/announcements",
            params={"page": max(1, int(page)), "page_size": min(50, max(1, int(page_size)))},
        )
        return data if isinstance(data, dict) else {}

    async def get_announcement_detail(self, item_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"/api/announcements/{item_id}")
        return data if isinstance(data, dict) else {}

    async def get_gacha_records(
        self,
        framework_token: str,
        *,
        page: int = 1,
        limit: int = 20,
        pools: str = "",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": max(1, int(page)), "limit": min(500, max(1, int(limit)))}
        if pools:
            params["pools"] = pools
        data = await self._request(
            "GET",
            "/api/endfield/gacha/records",
            params=params,
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_gacha_stats(self, framework_token: str) -> dict[str, Any]:
        data = await self._request(
            "GET", "/api/endfield/gacha/stats", framework_token=framework_token
        )
        return data if isinstance(data, dict) else {}

    async def post_gacha_fetch(
        self, framework_token: str, role_id: str = ""
    ) -> dict[str, Any]:
        body = {"role_id": role_id} if role_id else {}
        data = await self._request(
            "POST",
            "/api/endfield/gacha/fetch",
            body=body,
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_gacha_sync_status(self, framework_token: str) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/api/endfield/gacha/sync/status",
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_panel_chars(
        self,
        framework_token: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/api/panel/chars",
            params={
                "page": max(1, int(page)),
                "page_size": min(50, max(1, int(page_size))),
            },
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_panel_chars_all(
        self,
        framework_token: str,
        *,
        page_size: int = 50,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            data = await self.get_panel_chars(
                framework_token, page=page, page_size=page_size
            )
            chunk = data.get("synced_chars") or data.get("chars") or []
            if isinstance(chunk, list):
                rows.extend(item for item in chunk if isinstance(item, dict))
            try:
                pages = int(data.get("pages") or 1)
            except (TypeError, ValueError):
                pages = 1
            if page >= pages:
                break
            page += 1
        return rows

    async def get_panel_char(
        self, framework_token: str, template_id: str
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"/api/panel/char/{template_id}",
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_card_char(
        self,
        framework_token: str,
        *,
        inst_id: str = "",
        operator_id: str = "",
        char_id: str = "",
        role_id: str = "",
        server_id: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if inst_id:
            params["instId"] = inst_id
        else:
            if operator_id:
                params["operatorId"] = operator_id
            if char_id:
                params["charId"] = char_id
        if role_id:
            params["roleId"] = role_id
        if server_id:
            params["serverId"] = server_id
        data = await self._request(
            "GET",
            "/api/endfield/card/char",
            params=params or None,
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_card_detail(
        self, framework_token: str, role_id: str, server_id: int = 1
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/api/endfield/card/detail",
            params={"roleId": role_id, "serverId": server_id, "userId": role_id},
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_crisis_contract(
        self,
        framework_token: str,
        contract_id: str,
        *,
        role_id: str = "",
        server_id: int = 1,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"contractId": contract_id}
        if role_id:
            params["roleId"] = role_id
            params["userId"] = role_id
        if server_id:
            params["serverId"] = server_id
        data = await self._request(
            "GET",
            "/api/endfield/card/crisis-contract",
            params=params,
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_wiki_activities(self) -> dict[str, Any]:
        data = await self._request("GET", "/api/wiki/activities")
        return data if isinstance(data, dict) else {}

    async def sync_panel(self, framework_token: str) -> dict[str, Any]:
        data = await self._request(
            "POST", "/api/panel/sync", framework_token=framework_token
        )
        return data if isinstance(data, dict) else {}

    async def get_panel_sync_status(self, framework_token: str) -> dict[str, Any]:
        data = await self._request(
            "GET", "/api/panel/sync/status", framework_token=framework_token
        )
        return data if isinstance(data, dict) else {}

    async def get_spaceship(
        self, framework_token: str, role_id: str = "", server_id: int = 1
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/api/endfield/spaceship",
            params={"roleId": role_id, "serverId": server_id},
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_domain(
        self, framework_token: str, role_id: str = "", server_id: int = 1
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/api/endfield/domain",
            params={"roleId": role_id, "serverId": server_id},
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_achieve(
        self, framework_token: str, role_id: str = "", server_id: int = 1
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/api/endfield/achieve",
            params={"roleId": role_id, "serverId": server_id},
            framework_token=framework_token,
        )
        return data if isinstance(data, dict) else {}

    async def get_global_stats(
        self, *, pool_period: str = "", provider: str = ""
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if pool_period:
            params["pool_period"] = pool_period
        if provider:
            params["provider"] = provider
        data = await self._request(
            "GET",
            "/api/endfield/gacha/global-stats",
            params=params or None,
        )
        return data if isinstance(data, dict) else {}


def _response_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:200]
    if isinstance(data, dict):
        return str(data.get("message") or data.get("msg") or "")
    return ""
