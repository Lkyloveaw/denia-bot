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
SYNC_COMMANDS = frozenset({"同步抽卡", "同步面板"})
PRIVATE_COMMANDS = BINDING_COMMANDS | SYNC_COMMANDS
QUERY_COMMANDS = frozenset(
    {
        "便签",
        "理智",
        "签到",
        "抽卡记录",
        "抽卡分析",
        "干员列表",
        "干员面板",
        "危机合约",
        "日历",
    }
)
ALL_COMMANDS = PRIVATE_COMMANDS | QUERY_COMMANDS


@dataclass(frozen=True)
class Command:
    name: str
    arg: str = ""


def parse_command(text: Any) -> Command | None:
    """精确解析命令；不带前缀，不做子串匹配。"""
    raw = str(text or "").strip()
    if raw in {
        "授权登陆",
        "扫码绑定",
        "便签",
        "理智",
        "签到",
        "绑定列表",
        "抽卡分析",
        "日历",
        "同步抽卡",
        "同步面板",
    }:
        return Command(raw)
    if raw == "危机合约":
        return Command("危机合约")
    if raw.startswith("危机合约 "):
        arg = raw[len("危机合约 ") :].strip()
        if 1 <= len(arg) <= 32:
            return Command("危机合约", arg)
    for name in ("切换绑定", "删除绑定"):
        prefix = name + " "
        if raw.startswith(prefix):
            arg = raw[len(prefix) :].strip()
            if arg.isdigit() and 1 <= int(arg) <= 99:
                return Command(name, arg)
    for name in ("抽卡记录", "干员列表"):
        if raw == name:
            return Command(name)
        prefix = name + " "
        if raw.startswith(prefix):
            arg = raw[len(prefix) :].strip()
            if arg.isdigit() and 1 <= int(arg) <= 99:
                return Command(name, arg)
    if raw.startswith("干员面板 "):
        arg = raw[len("干员面板 ") :].strip()
        if arg:
            return Command("干员面板", arg)
    return None


def action_of(command: Command) -> str:
    if command.name == "切换绑定":
        return "switch"
    if command.name == "删除绑定":
        return "delete"
    if command.name in SYNC_COMMANDS:
        return "sync"
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


def format_gacha_records(data: Any, *, page: int = 1, pages: int = 1) -> str:
    payload = _mapping(data)
    records = payload.get("records") or []
    try:
        total_pages = int(payload.get("pages") or pages or 1)
    except (TypeError, ValueError):
        total_pages = 1
    if not isinstance(records, list) or not records:
        return "暂无抽卡记录；可私聊发送「同步抽卡」后再试。"
    lines = [f"抽卡记录（第 {page}/{total_pages} 页）："]
    for index, item in enumerate(records[:20], 1):
        record = _mapping(item)
        rarity = record.get("rarity") or record.get("star") or "?"
        name = (
            record.get("char_name")
            or record.get("item_name")
            or record.get("name")
            or "未知"
        )
        pool = record.get("pool_name") or record.get("pool") or ""
        suffix = f" | {pool}" if pool else ""
        lines.append(f"{index}. ★{rarity} {name}{suffix}")
    footer = format_page_footer(page, total_pages, "抽卡记录")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def format_gacha_stats(data: Any) -> str:
    payload = _mapping(data)
    stats = _mapping(payload.get("stats") or payload)
    total = _first(stats, "total_count", "total", "count")
    star6 = _first(stats, "star6_count", "six_star_count", "star_6")
    rate = _first(stats, "star6_rate", "six_star_rate", "rate")
    if total is None and star6 is None:
        return "暂无抽卡分析；可私聊发送「同步抽卡」后再试。"
    parts = [f"总抽数 {total if total is not None else '?'}"]
    if star6 is not None:
        parts.append(f"六星 {star6}")
    if rate is not None:
        parts.append(f"六星率 {rate}")
    return "抽卡分析：" + " / ".join(parts)


def format_operator_list(data: Any, *, page: int = 1, pages: int = 1) -> str:
    payload = _mapping(data)
    rows = payload.get("synced_chars") or payload.get("chars") or []
    try:
        total_pages = int(payload.get("pages") or pages or 1)
    except (TypeError, ValueError):
        total_pages = 1
    if not isinstance(rows, list) or not rows:
        return "暂无干员数据；可私聊发送「同步面板」后再试。"
    lines = [f"干员列表（第 {page}/{total_pages} 页）："]
    for index, item in enumerate(rows[:20], 1):
        row = _mapping(item)
        name = row.get("name_cn") or row.get("name") or "未知"
        level = row.get("level") or row.get("lv") or "?"
        elite = row.get("evolve_phase") or row.get("elite") or row.get("phase")
        suffix = f" | 精英 {elite}" if elite is not None else ""
        lines.append(f"{index}. {name} | Lv.{level}{suffix}")
    footer = format_page_footer(page, total_pages, "干员列表")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def format_operator_panel(data: Any, *, name: str = "") -> str:
    payload = _mapping(data)
    panel = _mapping(payload.get("panel") or payload)
    title = name or panel.get("name_cn") or panel.get("name") or "干员"
    summary = _mapping(panel.get("summary") or panel.get("stats"))
    parts: list[str] = []
    for label, keys in (
        ("等级", ("level", "lv")),
        ("攻击", ("attack", "atk")),
        ("防御", ("defense", "def")),
        ("生命", ("hp", "health")),
    ):
        value = _first(summary, *keys) or _first(panel, *keys)
        if value is not None:
            parts.append(f"{label} {value}")
    if not parts:
        return f"{title}：已获取面板数据（字段待适配）。"
    return f"{title}：" + " / ".join(parts)


def format_crisis_contract(data: Any, *, contract: Any = None) -> str:
    payload = _mapping(data)
    crisis = _mapping(
        payload.get("crisisContract")
        or payload.get("crisis_contract")
        or payload
    )
    contract_info = _mapping(contract)
    name = (
        crisis.get("name")
        or crisis.get("title")
        or contract_info.get("name")
        or "危机合约"
    )
    score = _first(crisis, "score", "total_score", "point")
    parts = [str(name)]
    if score is not None:
        parts.append(f"得分 {score}")
    indicators = crisis.get("indicators") or crisis.get("indicator")
    if isinstance(indicators, list) and indicators:
        names = []
        for item in indicators[:5]:
            if isinstance(item, Mapping):
                names.append(str(item.get("name") or item.get("title") or "指标"))
            else:
                names.append(str(item))
        parts.append("指标：" + "、".join(names))
    return "危机合约：" + " / ".join(parts)


def format_calendar(data: Any) -> str:
    payload = _mapping(data)
    activities = payload.get("activities") or payload.get("list") or []
    if not isinstance(activities, list) or not activities:
        return "暂无活动日历数据。"
    lines = ["活动日历："]
    for item in activities[:10]:
        row = _mapping(item)
        name = row.get("name") or row.get("title") or "未命名活动"
        start = row.get("activity_start_at_ts") or row.get("start_at_ts") or ""
        end = row.get("activity_end_at_ts") or row.get("end_at_ts") or ""
        window = f"（{start} - {end}）" if start or end else ""
        lines.append(f"- {name}{window}")
    return "\n".join(lines)


def format_sync_status(data: Any) -> str:
    payload = _mapping(data)
    status = str(payload.get("status") or "")
    if status in {"completed", "success", "done"}:
        return f"同步完成，新增 {payload.get('new_records', 0)} 条。"
    if status in {"failed", "error"}:
        return f"同步失败：{payload.get('error') or '未知错误'}"
    if status in {"conflict", "running"}:
        return "服务器正在同步，请稍后再查。"
    return "同步已提交，请稍后再查。"


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
        sync_limit: int = 1,
    ):
        self.debounce_seconds = float(debounce_seconds)
        self.limits = {
            "query": int(query_limit),
            "bind": int(bind_limit),
            "switch": int(switch_limit),
            "delete": int(delete_limit),
            "sync": int(sync_limit),
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
