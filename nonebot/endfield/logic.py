"""Endfield 纯逻辑（MIT）。

不 import nonebot/muicebot/httpx；只做格式化、分页、脱敏、consent 文案。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence, TypeVar

T = TypeVar("T")

CONSENT_TEXT = (
    "绑定前请确认：绑定后，查询所需的账号标识与参数会发送到第三方「协议终端」"
    "end-api.shallow.ink；framework_token 仅保存在本机 data/endfield/bindings.json"
    "（0600），不会发到群里。回复「同意」继续，回复「取消」退出。"
)


def mask_token(token: Any) -> str:
    """只保留尾 4 位；空值返回空串。"""
    text = str(token or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "****"
    return "****" + text[-4:]


def mask_api_key(key: Any) -> str:
    return mask_token(key)


def mask_role_id(role_id: Any) -> str:
    text = str(role_id or "")
    if len(text) <= 4:
        return "****"
    return f"{text[:2]}****{text[-2:]}"


def parse_page(text: Any, *, default: int = 1, maximum: int = 999) -> int:
    """解析 `[页码]`；非法/越界回退 default。"""
    raw = str(text or "").strip()
    if not raw:
        return default
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return default
    if page < 1 or page > maximum:
        return default
    return page


def paginate(
    items: Sequence[T], page: int, page_size: int = 20
) -> tuple[list[T], int, int]:
    """返回 (当前页切片, 规范化页码, 总页数)；页码越界时夹到最后一页。"""
    size = max(1, int(page_size))
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    current = max(1, int(page))
    if current > pages:
        current = pages
    start = (current - 1) * size
    return list(items[start : start + size]), current, pages


def consent_text() -> str:
    return CONSENT_TEXT


def format_page_footer(page: int, pages: int, command: str) -> str:
    if pages <= 1:
        return ""
    return f"第 {page}/{pages} 页；发送「{command} {page + 1}」查看下一页"


def error_text(kind: str) -> str:
    """用户可见的短错误；不包含原始响应/token。"""
    return {
        "no_binding": "未绑定账号，请私聊发送「授权登陆」或「扫码绑定」。",
        "invalid_binding": "绑定已失效，请私聊重新授权；或发送「删除绑定 N」清理。",
        "rate_limit": "请求过于频繁，请稍后再试。",
        "timeout": "网关响应超时，请稍后再试。",
        "render": "图卡渲染失败，已回退文本。",
        "config": "终末地配置未就绪，请在管理面板填写 API Key。",
    }.get(kind, "操作失败，请稍后再试。")


BINDING_COMMANDS = frozenset(
    {"授权登陆", "扫码绑定", "绑定列表", "切换绑定", "删除绑定"}
)
QUERY_COMMANDS = frozenset({"便签", "理智", "签到"})
ALL_COMMANDS = BINDING_COMMANDS | QUERY_COMMANDS


@dataclass(frozen=True)
class Command:
    name: str
    arg: str = ""


def parse_command(text: Any) -> Command | None:
    """精确解析一期命令；不带前缀，不做子串匹配。"""
    raw = str(text or "").strip()
    if raw in {"授权登陆", "扫码绑定", "便签", "理智", "签到", "绑定列表"}:
        return Command(raw)
    for name in ("切换绑定", "删除绑定"):
        prefix = name + " "
        if raw.startswith(prefix):
            arg = raw[len(prefix) :].strip()
            if arg.isdigit() and 1 <= int(arg) <= 99:
                return Command(name, arg)
    return None


def action_of(command: Command) -> str:
    if command.name == "切换绑定":
        return "switch"
    if command.name == "删除绑定":
        return "delete"
    if command.name in BINDING_COMMANDS:
        return "bind"
    return "query"


def format_stamina(data: Any) -> str:
    payload = _mapping(data)
    stamina = _mapping(payload.get("stamina") or payload)
    current = _first(stamina, "current", "value", "cur")
    maximum = _first(stamina, "max", "limit", "maximum")
    if current is None and maximum is None:
        return "理智：已获取（字段待适配）"
    return f"理智：{current if current is not None else '?'}/{maximum if maximum is not None else '?'}"


def format_note(data: Any) -> str:
    payload = _mapping(data)
    note = _mapping(payload.get("note") or payload)
    parts: list[str] = []
    for label, keys in (
        ("当前", ("current", "value", "cur")),
        ("上限", ("max", "limit", "maximum")),
        ("今日", ("today", "daily", "todayCount")),
    ):
        value = _first(note, *keys)
        if value is not None:
            parts.append(f"{label} {value}")
    if not parts:
        return "便签：已获取（字段待适配）"
    return "便签：" + " / ".join(parts)


def format_attendance(data: Any) -> str:
    payload = _mapping(data)
    awards = payload.get("awards") or payload.get("items") or []
    if not isinstance(awards, list) or not awards:
        return "签到：今日已签到或已领取。"
    names: list[str] = []
    for item in awards[:5]:
        if isinstance(item, Mapping):
            names.append(str(item.get("name") or item.get("item_name") or "奖励"))
        else:
            names.append(str(item))
    return "签到成功：" + "、".join(names)


def format_binding_list(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return "暂无绑定。请私聊发送「授权登陆」进行绑定。"
    lines = ["绑定列表："]
    for index, item in enumerate(items, 1):
        binding = _mapping(item)
        name = binding.get("alias") or binding.get("role_name") or "未命名"
        server = binding.get("server_id", "?")
        primary = "主账号" if binding.get("is_primary") else "非主"
        status = "有效" if binding.get("status") == "valid" else "已失效"
        lines.append(f"{index}. {name} | 区服 {server} | {primary} | {status}")
    lines.append("发送「切换绑定 N」或「删除绑定 N」管理。")
    return "\n".join(lines)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


class UserGate:
    """每 QQ 防抖 + 每日配额；纯内存，重启后重置。"""

    def __init__(
        self,
        *,
        debounce_seconds: float = 5.0,
        query_limit: int = 100,
        bind_limit: int = 3,
        switch_limit: int = 10,
        delete_limit: int = 20,
    ):
        self.debounce_seconds = float(debounce_seconds)
        self.limits = {
            "query": int(query_limit),
            "bind": int(bind_limit),
            "switch": int(switch_limit),
            "delete": int(delete_limit),
        }
        self._last: dict[str, float] = {}
        self._counts: dict[tuple[str, str, str], int] = {}

    def check(
        self,
        qq: str,
        action: str,
        *,
        now: float | None = None,
        day: str | None = None,
        is_superuser: bool = False,
    ) -> tuple[bool, str]:
        if is_superuser:
            return True, ""
        current = time.monotonic() if now is None else float(now)
        current_day = date.today().isoformat() if day is None else str(day)
        last = self._last.get(str(qq))
        if last is not None and current - last < self.debounce_seconds:
            return False, "操作太快，请 5 秒后再试。"
        self._last[str(qq)] = current
        limit = self.limits.get(action)
        if limit is None:
            return True, ""
        key = (current_day, str(qq), action)
        count = self._counts.get(key, 0)
        if count >= limit:
            return False, "今日次数已用完，请明天再试。"
        self._counts[key] = count + 1
        return True, ""
