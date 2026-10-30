"""给「问这个人叫什么」加超时保险 —— 协议端不答时，她照常回话。

    拼提示词 → MuiceBot 要一句署名（`muice.py:174`、`muice.py:211`）
      → nonebot_plugin_userinfo: get_group_member_info 失败
      → 回退 get_stranger_info
      → nonebot-adapter-onebot 的 API Future **没有超时** → 整条消息永远挂着

2026-09-27 16:00 之后她一条群消息都没回，日志停在「正在调用模型...」，
既不报错也不出「模型调用参数」，事件循环还活着（`/kb 查` 照样秒回）——
就是挂在这。触发条件很平常：发消息的是个假 QQ 号、机器人不在那个群，
或者 QQ 侧一时不答。

这个插件只做一件事：把 `muice.py` **自己那份** `get_username` 引用换成带超时的版本。
MuiceBot 是 `from .utils.utils import get_username`（`muice.py:28`），
改 `muicebot.utils.utils` 上的名字管不到调用点 —— 接线测试里专门锁了这条。
等不到名字就退回 QQ 号（`name_guard_logic.name_of`），署名难看但总比整条不回强。

策略在 `nonebot/name_guard_logic.py`（纯逻辑，`tests/test_name_guard_{logic,wiring}.py` 直接断言）。
开关在 `nonebot/.env`（改了要重启，插件不热加载），也可以启动时直接用进程环境覆盖：

    NAME_GUARD=0              # 关掉：一个引用都不动（回滚用）
    NAME_GUARD_TIMEOUT=3      # 问名字最多等几秒

    NAME_GUARD_TIMEOUT=0.05 python bot.py   # 临时压短，验「等不到时她还在不在」
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from nonebot import get_driver, logger
from muicebot.plugin import PluginMetadata

try:
    import name_guard_logic as ng
except ImportError:  # 不是从 nonebot\ 目录启动的，把工程根补进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import name_guard_logic as ng

__plugin_meta__ = PluginMetadata(
    name="name_guard",
    description="问名字加超时：协议端不答也别让整条对话静默挂死",
    usage="无需指令；开关在 nonebot/.env 的 NAME_GUARD / NAME_GUARD_TIMEOUT",
)

#: 要就地替换引用的模块 —— 都必须是**调用点所在**的模块，不是函数定义处。
TARGETS = (
    "muicebot.muice",
    "muicebot.builtin_plugins.moe_chat",
)


def _raw(key: str):
    """问一个配置项：先看进程环境，再看 `.env`（真环境变量盖过文件，dotenv 的老规矩）。

    为什么两条路都要：线上想临时把超时压到 0.05s 验一次保险，靠改 `.env` 就得
    动主人的配置文件；`NAME_GUARD_TIMEOUT=0.05 python bot.py` 更省事，回滚也快。
    """
    val = os.environ.get(key.upper())
    if val not in (None, ""):
        return val
    try:
        return getattr(get_driver().config, key, None)
    except Exception:  # noqa: BLE001 - 驱动还没起来（测试里直接调 install）
        return None


def _flag(name: str, default: bool = True) -> bool:
    """开关。关掉的写法照 `0 / false / off / no / 关` 都算。"""
    raw = _raw(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "关")


def _timeout() -> float:
    """问名字的秒数；填错了退回默认，不该因为一个错别字丢掉保险。"""
    try:
        raw = _raw("name_guard_timeout")
        return float(raw) if raw not in (None, "") else ng.DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        return ng.DEFAULT_TIMEOUT


def _reported(name: str, waited: float) -> None:
    """超时必须留痕：下次「她不回话了」先看有没有这行。"""
    logger.warning(f"[name] 问名字 {waited:g}s 没答上来，署名先用「{name}」顶上")


def _entering(limit: float) -> None:
    """真的去问了才留这行。线上那次「保险没起作用」，靠的就是有没有它来分两种情况。"""
    logger.debug(f"[name] 去问这张名片了（最多等 {limit:g}s）")


def _wrapped(mod_name: str, replaced) -> None:
    """装住的每一处都要报出来 —— 不然「到底包住了谁」只能靠猜。"""
    logger.info(f"[name] 包住 {mod_name}.get_username"
                f"（换掉的那一份来自 {getattr(replaced, '__module__', '?')}）")


def _current_event():
    """现在正在处理哪条消息 —— `muice.py:174` 空手调用，userid 只在事件里。

    用 MuiceBot 自己那份上下文（`muice.py` 的 `get_username` 取事件也是从这拿的），
    不是 nonebot 的 —— 两边不同步的话，超时顶出来的署名就会跟模型看到的不是一回事。
    """
    mod = sys.modules.get("muicebot.plugin.context")
    if mod is None:  # 极端情况：MuiceBot 没加载，交给 `name_of` 退成占位
        raise LookupError("MuiceBot 的对话上下文还没加载")
    return mod.get_event()


def _loaded_targets() -> list:
    """只挑**已经加载好**的那些模块。

    千万别在这里 `import` —— 本插件是 MuiceBot 加载内置插件的过程中被拉起来的，
    那时去 `importlib.import_module("muicebot.builtin_plugins.moe_chat")`
    会把它的导入做一半，紧接着 MuiceBot 自己那份就报
    `Failed to import "muicebot.builtin_plugins.moe_chat"`（实测过，别踩）。
    没在 `sys.modules` 里的，交给下面 `on_startup` 的第二遍。
    """
    return [sys.modules[name] for name in TARGETS if name in sys.modules]


def install(modules=None, timeout: float | None = None) -> int:
    """包住这些模块里的 `get_username`，返回实际包了几处。

    关着（`NAME_GUARD=0`）就一个都不动；已经包过的跳过，所以重复调用是 0。
    """
    if not _flag("name_guard"):
        logger.debug("[name] NAME_GUARD=0，不动 MuiceBot 的引用")
        return 0

    limit = _timeout() if timeout is None else timeout
    mods = list(modules) if modules is not None else _loaded_targets()

    n = ng.patch(mods, limit, on_timeout=_reported, on_wrap=_wrapped,
                 on_enter=_entering, current_event=_current_event)
    if n:
        logger.info(f"[name] 问名字加了超时保险：最多 {limit:g}s，"
                    f"等不到就用 QQ 号当署名（包住 {n} 处引用）")
    return n


COUNT = install()


def second_pass() -> None:
    """内置插件这时候才加载完，补上第一遍没看到的引用（包过的会跳过）。"""
    install()


get_driver().on_startup(second_pass)
