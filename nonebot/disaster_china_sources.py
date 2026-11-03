# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""Jian Project / EQSC 网络客户端。

- Jian：`lk_` 登录密钥 → `/api/refresh` 换 `rt_` 长期 Token；`rt_` → `/api/access`
  换 `at_` 短期 Token；WebSocket `wss://api.sismotide.top/all?key=at_...`。
- EQSC：`ARh.` RefreshToken → `/createAccessToken` 换 `ATn.`（约 1 小时）；
  `/typhoonNMC.json` 拉台风列表。

网络客户端不解析业务字段；解析在 `disaster_china_logic`。测试通过注入
`httpx.AsyncClient(transport=MockTransport)` 与 `connect=` 完成，不需要真凭证。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import quote

import httpx

try:  # aiohttp 是 NoneBot 运行环境的既有依赖；纯逻辑/HTTP 测试不依赖它。
    import aiohttp
    from aiohttp import WSMsgType
except ImportError:  # pragma: no cover - 仅在极端精简环境触发
    aiohttp = None
    WSMsgType = None

CST = timezone(timedelta(hours=8))
JIAN_REFRESH_URL = 'https://auth.sismotide.top/api/refresh'
JIAN_ACCESS_URL = 'https://auth.sismotide.top/api/access'
JIAN_WS_URL = 'wss://api.sismotide.top/all'
EQSC_BASE_URL = 'https://equake.top'


class ChinaSourceError(RuntimeError):
    """Jian/EQSC 换票、连接或响应错误。"""


# ---------------------------------------------------------------- Jian


class JianAuth:
    """Jian Project 令牌链：lk_ → rt_ → at_。"""

    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None,
                 on_refresh: Callable[[str, str], None] | None = None):
        self.token = ''.join(str(token or '').split())
        self.refresh_token = self.token if self.token.startswith('rt_') else ''
        self.refresh_expires_at = 0.0
        self.access_token = self.token if self.token.startswith('at_') else ''
        self.access_expires_at = time.time() + 3600 if self.access_token else 0.0
        self.max_connections = 3
        self._client = client
        self._owns_client = client is None
        self._on_refresh = on_refresh

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20)
            self._owns_client = True
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    @property
    def refresh_expires_date(self) -> str:
        if not self.refresh_expires_at:
            return ''
        return datetime.fromtimestamp(self.refresh_expires_at, CST).strftime('%Y-%m-%d')

    async def _post(self, url: str, bearer: str) -> Mapping[str, Any]:
        client = self._ensure_client()
        try:
            response = await client.post(url, headers={
                'Authorization': f'Bearer {bearer}',
                'User-Agent': 'denia-bot-disaster/1.0',
            })
        except httpx.HTTPError as exc:
            detail = str(exc).strip() or type(exc).__name__
            raise ChinaSourceError(f'Jian 换票请求失败：{detail}') from exc
        if response.status_code != 200:
            detail = f'HTTP {response.status_code}'
            try:
                data = response.json()
            except ValueError:
                data = None
            if isinstance(data, Mapping):
                code = data.get('code')
                message = data.get('message') or data.get('error')
                if code is not None:
                    detail += f' [{code}]'
                if message:
                    detail += f' {message}'
            raise ChinaSourceError(f'Jian 换票失败：{detail}')
        try:
            data = response.json()
        except ValueError as exc:
            raise ChinaSourceError('Jian 换票返回非 JSON') from exc
        if not isinstance(data, Mapping):
            raise ChinaSourceError('Jian 换票返回格式无效')
        return data

    async def _exchange_login(self) -> None:
        data = await self._post(JIAN_REFRESH_URL, self.token)
        if not data.get('ok'):
            code = data.get('code')
            message = data.get('message') or data.get('error') or 'unknown'
            raise ChinaSourceError(f'Jian 登录密钥换票失败 [{code}]: {message}')
        token = str(data.get('token') or '').strip()
        if not token:
            raise ChinaSourceError('Jian 换票响应未包含长期 Token')
        self.refresh_token = token
        try:
            expires_min = float(data.get('expires_after_min') or 259200)
        except (TypeError, ValueError):
            expires_min = 259200
        self.refresh_expires_at = time.time() + expires_min * 60
        try:
            self.max_connections = int(data.get('max_connections') or 3)
        except (TypeError, ValueError):
            self.max_connections = 3
        if self._on_refresh is not None:
            try:
                self._on_refresh(self.refresh_token, self.refresh_expires_date)
            except Exception:
                # 持久化失败不能阻断本次连接；调用方会再尝试落盘。
                pass

    async def access(self) -> str:
        if self.access_token and time.time() < self.access_expires_at - 60:
            return self.access_token
        if not self.token:
            raise ChinaSourceError('Jian 令牌未配置')
        if self.token.startswith('lk_') and not self.refresh_token:
            await self._exchange_login()
        elif self.token.startswith('rt_'):
            self.refresh_token = self.token
        elif not self.token.startswith(('lk_', 'at_')):
            raise ChinaSourceError('Jian 令牌须以 lk_ / rt_ / at_ 开头')
        if self.access_token and time.time() < self.access_expires_at - 60:
            return self.access_token

        data = await self._post(JIAN_ACCESS_URL, self.refresh_token)
        if not data.get('ok'):
            code = data.get('code')
            message = data.get('message') or data.get('error') or 'unknown'
            raise ChinaSourceError(f'Jian 换取访问令牌失败 [{code}]: {message}')
        token = str(data.get('token') or '').strip()
        if not token:
            raise ChinaSourceError('Jian 换票响应未包含访问 Token')
        try:
            expires_sec = float(data.get('expires_after_sec') or 3600)
        except (TypeError, ValueError):
            expires_sec = 3600
        self.access_token = token
        self.access_expires_at = time.time() + max(60.0, expires_sec)
        return token


class JianConnection:
    """一层 WS 接收包装：只返回 dict，心跳/控制帧内部消化。"""

    def __init__(self, ws: Any):
        self.ws = ws

    async def recv(self) -> dict[str, Any] | None:
        if WSMsgType is None:  # pragma: no cover
            raise ChinaSourceError('aiohttp 不可用，无法接收 Jian WebSocket')
        while True:
            message = await self.ws.receive()
            if message.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                text = message.data if isinstance(message.data, str) else message.data.decode('utf-8', 'replace')
                try:
                    data = json.loads(text)
                except ValueError as exc:
                    raise ChinaSourceError('Jian 消息不是 JSON') from exc
                return data if isinstance(data, dict) else None
            if message.type in (WSMsgType.PING, WSMsgType.PONG):
                continue
            if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                return None
            if message.type == WSMsgType.ERROR:
                raise ChinaSourceError(f'Jian WebSocket 错误：{self.ws.exception}')
            return None

    async def close(self) -> None:
        try:
            await self.ws.close()
        except Exception:
            pass


class JianProjectSource:
    """Jian WebSocket 连接器；`connect=` 可注入 fake，测试不发网络。"""

    def __init__(self, *, ws_url: str = JIAN_WS_URL,
                 connect: Callable[[str], Awaitable[Any]] | None = None):
        self.ws_url = ws_url
        self._connect_override = connect
        self._session: Any = None

    async def connect(self, access_token: str) -> JianConnection:
        if self._connect_override is not None:
            return await self._connect_override(access_token)
        if aiohttp is None:  # pragma: no cover
            raise ChinaSourceError('未安装 aiohttp，无法连接 Jian WebSocket')
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=20, sock_connect=20),
            )
        url = f'{self.ws_url}?key={quote(access_token, safe="")}'
        ws = await self._session.ws_connect(
            url,
            headers={'X-API-Key': access_token},
            heartbeat=30,
        )
        return JianConnection(ws)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


class JianSource:
    """把 Jian 鉴权与 WS 连接合成一个可关闭对象。"""

    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None,
                 connect: Callable[[str], Awaitable[Any]] | None = None,
                 on_token: Callable[[str, str], None] | None = None):
        self.auth = JianAuth(token, client=client, on_refresh=on_token)
        self.ws = JianProjectSource(connect=connect)
        self._connection: Any = None

    async def connect(self) -> JianConnection:
        self._connection = await self.ws.connect(await self.auth.access())
        return self._connection

    async def close(self) -> None:
        if self._connection is not None:
            close = getattr(self._connection, 'close', None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass
            self._connection = None
        await self.ws.close()
        await self.auth.close()


# ---------------------------------------------------------------- EQSC


class EqscSource:
    """EQSC RefreshToken → AccessToken → 台风列表。"""

    def __init__(self, refresh_token: str, *, client: httpx.AsyncClient | None = None):
        # 从 EQuake 界面复制时常夹带换行/空格；Authorization 头不能含空白。
        self.refresh_token = ''.join(str(refresh_token or '').split())
        self.access_token = ''
        self.access_expires_at = 0.0
        self._client = client
        self._owns_client = client is None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=45)
            self._owns_client = True
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    async def access(self) -> str:
        if self.access_token and time.time() < self.access_expires_at - 60:
            return self.access_token
        if not self.refresh_token:
            raise ChinaSourceError('EQSC RefreshToken 未配置')
        if not self.refresh_token.startswith('ARh.'):
            raise ChinaSourceError(
                'EQSC 令牌须以 ARh. 开头；请在 EQuake → 设置 → 通用 → EQSC API 配置 复制刷新令牌'
            )
        client = self._ensure_client()
        try:
            response = await client.get(
                f'{EQSC_BASE_URL}/createAccessToken',
                headers={'Authorization': f'Bearer {self.refresh_token}'},
            )
        except httpx.HTTPError as exc:
            raise ChinaSourceError(f'EQSC 换票请求失败：{exc}') from exc
        if response.status_code != 200:
            raise ChinaSourceError(f'EQSC 换票返回 HTTP {response.status_code}')
        text = (response.text or '').strip()
        if ',' not in text:
            raise ChinaSourceError(f'EQSC 换票响应格式无效：{text[:80] or "<empty>"}')
        token_part, expires_part = text.rsplit(',', 1)
        token = token_part.strip().strip('"').strip("'")
        try:
            expires = int(expires_part.strip().strip('"').strip("'"))
        except ValueError as exc:
            raise ChinaSourceError('EQSC 换票有效期不是整数') from exc
        if not token or token.startswith('ARh.') or expires <= 0:
            raise ChinaSourceError('EQSC 换票返回的 AccessToken 无效')
        self.access_token = token
        self.access_expires_at = time.time() + expires
        return token

    async def fetch_typhoons(self) -> list[dict[str, Any]]:
        token = await self.access()
        client = self._ensure_client()
        try:
            response = await client.get(
                f'{EQSC_BASE_URL}/typhoonNMC.json',
                headers={'Authorization': f'Bearer {token}'},
            )
        except httpx.HTTPError as exc:
            raise ChinaSourceError(f'EQSC 台风请求失败：{exc}') from exc
        if response.status_code != 200:
            raise ChinaSourceError(f'EQSC 台风接口返回 HTTP {response.status_code}')
        try:
            data = response.json()
        except ValueError as exc:
            raise ChinaSourceError('EQSC 台风接口返回非 JSON') from exc
        if not isinstance(data, Mapping):
            raise ChinaSourceError('EQSC 台风接口返回格式无效')
        items = data.get('typhoon')
        if not isinstance(items, list):
            raise ChinaSourceError('EQSC 台风接口缺少 typhoon 列表')
        return [item for item in items if isinstance(item, dict)]
