"""Endfield 纯逻辑（MIT）。

不 import nonebot/muicebot/httpx；只做格式化、分页、脱敏、consent 文案。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence, TypeVar

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
