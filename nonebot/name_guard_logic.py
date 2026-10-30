r"""问名字这件事的超时保险 —— 纯逻辑，不 import nonebot / muicebot。

线上事故（2026-09-27 16:00 之后她一条群消息都没回）：

    MuiceBot 拼提示词 → await get_username()
        → nonebot_plugin_userinfo: get_group_member_info  (失败)
        → 回退 get_stranger_info                            (协议端不答)
        → nonebot-adapter-onebot 的 API Future **没有超时**  → 永远挂着

挂在「正在调用模型...」之后，不报错、不打日志，事件循环照常跑
（所以 `/kb 查` 还能秒回），看上去就像她已读不回。触发条件很平常：
发消息的人是个假 QQ 号、机器人不在那个群里、或者 QQ 侧一时抽风。

这一层只补一件事：**问名字最多等 N 秒**，等不到就用已经确定的称呼顶上，
让她照常回话。除此之外什么都不管 —— 非超时的异常一律原样抛出去，
免得把真毛病藏起来。

接线在 `plugins/name_guard.py`。
"""

from __future__ import annotations

import asyncio
from types import ModuleType
from typing import Callable, Iterable, Optional

#: 等名片的秒数。太短会把正常查询误判成不答，太长等于不回话。
DEFAULT_TIMEOUT = 3.0

#: 连 QQ 号都拿不到时的署名。不能是空串 —— 空的 `<...>` 会把后面
#: 按署名取昵称的判断整个带跑（`prompt_logic.split_speaker` 那条链）。
PLACEHOLDER = "群友"

#: 贴在包好的函数上，用来认出「这一处已经装过保险了」。
GUARDED = "_name_guarded"


def name_of(user_id: object, event: object) -> str:
    """问不到名字时，手头上能确定下来的称呼。

    顺序：调用方给的 user_id → 事件里的 userid → 占位。
    `muice.py:174` 是空手调的（`await get_username()`），userid 只在事件里，
    所以这两条都得走一遍，只认参数等于没兜住。
    """
    if user_id:
        return str(user_id)
    getter = getattr(event, "get_user_id", None)
    if callable(getter):
        try:
            uid = getter()
        except Exception:  # noqa: BLE001 - 兜底路径自己不该再炸
            return PLACEHOLDER
        if uid:
            return str(uid)
    return PLACEHOLDER


def _from_context(current_event):
    """去「正在处理这条消息」的上下文里问一嗓子；问不到就算了，兜底不该再炸。"""
    if current_event is None:
        return None
    try:
        return current_event()
    except Exception:  # noqa: BLE001 - 没有正在处理的事件（如 MuiceBot 的 contextvar 空）
        return None


def guard(
    get_name: Callable[..., object],
    timeout: float = DEFAULT_TIMEOUT,
    on_timeout: Optional[Callable[[str, float], None]] = None,
    on_enter: Optional[Callable[[float], None]] = None,
    current_event: Optional[Callable[[], object]] = None,
):
    """把「问名字」包一层超时：等不到就返回 `name_of(...)`，并把里面那一跳取消。

    取消很重要 —— `wait_for` 不取消的话，挂住的 API 请求会一条一条堆在适配器里。
    `on_timeout`（顶上去的称呼, 等了多久）是给接线那边打日志用的，别在这里静默。
    `on_enter`（等了多久上限）在**真的去问之前**报一声：线上那次怀疑保险压根没在
    调用链上，没有这行就只能靠猜。
    `current_event` 是「现在正在处理哪条消息」—— `muice.py:174` 空手调用，
    userid 只活在事件里，不给这条路的话超时只会顶出个「群友」。
    """

    async def wrapped(user_id=None, event=None):
        if on_enter is not None:
            on_enter(timeout)
        try:
            return await asyncio.wait_for(get_name(user_id, event), timeout)
        except (asyncio.TimeoutError, TimeoutError):
            name = name_of(user_id, event or _from_context(current_event))
            if on_timeout is not None:
                on_timeout(name, timeout)
            return name

    wrapped.__name__ = f"guarded_{getattr(get_name, '__name__', 'get_username')}"
    setattr(wrapped, GUARDED, True)
    return wrapped


def patch(modules: Iterable[ModuleType], timeout: float = DEFAULT_TIMEOUT,
          on_timeout: Optional[Callable[[str, float], None]] = None,
          on_wrap: Optional[Callable[[str, Callable], None]] = None,
          on_enter: Optional[Callable[[float], None]] = None,
          current_event: Optional[Callable[[], object]] = None) -> int:
    """就地把这些模块里用到的 `get_username` 换成带超时的版本，返回包了几处。

    为什么按模块换：`muice.py:28` 是 `from .utils.utils import get_username`，
    改 `muicebot.utils.utils` 上的名字**管不到它**，必须改它自己那份引用。
    已经包过的跳过 —— 重复安装会把超时套两层，日志也会说「等了 6s 才退」。
    `on_wrap`（模块名, 被换掉的那一份）给接线那边留证据用。
    """
    n = 0
    for mod in modules:
        cur = getattr(mod, "get_username", None)
        if cur is None or getattr(cur, GUARDED, False):
            continue
        if on_wrap is not None:
            on_wrap(getattr(mod, "__name__", repr(mod)), cur)
        setattr(mod, "get_username",
                guard(cur, timeout, on_timeout, on_enter, current_event))
        n += 1
    return n
