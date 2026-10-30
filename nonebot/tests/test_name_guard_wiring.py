r"""署名超时保险的接线断言 —— 真把 MuiceBot 那份引用换掉了没有。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_name_guard_wiring.py

`test_name_guard_logic.py` 管「等不到时该顶什么称呼」，这里管
「MuiceBot 用的那一份函数真的被换过了吗、换错了引用的话等于没换」。

2026-09-27 那次全站静默就是栽在这：MuiceBot 自己那份是
`from .utils.utils import get_username`（`muice.py:28`），
只改 `muicebot.utils.utils` 上的名字**一点用都没有**，调用点照旧挂在 API 上。

不连 QQ：把「问名字」换成一个睡 5 秒的假货，模拟协议端不答。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)  # MuiceBot 按 ./plugins 找插件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot  # noqa: E402

nonebot.init()
nonebot.load_plugin("muicebot")

import muicebot.builtin_plugins.moe_chat as moe  # noqa: E402
import muicebot.muice as mb  # noqa: E402
import name_guard_logic as ng  # noqa: E402
import plugins.name_guard as ngw  # noqa: E402

FAILED: list[str] = []


def run(name, fn):
    try:
        fn()
    except AssertionError as e:
        FAILED.append(f"{name}: {e}")
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        FAILED.append(f"{name}: {type(e).__name__}: {e}")
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
    else:
        print(f"  ok    {name}")


def hang(user_id=None, event=None):
    """协议端不答：永远不回。"""
    return asyncio.sleep(5)


def t_the_fallback_event_comes_from_muicebots_own_context():
    """超时顶出来的署名要跟模型看到的那份同源 —— 所以得用 MuiceBot 自己的上下文。"""
    import muicebot.plugin.context as ctx

    marker = object()
    old = ctx.get_event
    ctx.get_event = lambda: marker
    try:
        assert ngw._current_event() is marker, "没去 MuiceBot 的上下文里取正在处理的事件"
    finally:
        ctx.get_event = old


def t_stalled_lookup_cannot_stall_the_reply():
    """核心一条：装上保险之后，问名字卡住也必须在超时后回话，而不是拖着整条消息。"""

    class FakeMod:
        get_username = staticmethod(hang)

    mod = FakeMod()
    assert ngw.install([mod], timeout=0.05) == 1, "install 该报告包了一处"
    t = time.perf_counter()
    got = asyncio.run(mod.get_username("555000111", None))
    spent = time.perf_counter() - t
    assert got == "555000111", f"卡住时该退回 QQ 号当署名，结果是 {got!r}"
    assert spent < 1.0, f"不该等满 5 秒，实际等了 {spent:.2f}s"


def t_muice_reference_is_guarded_on_import():
    """插件一加载就该包住 `muice.py` 那份引用（它 import 时就把函数抄走了）。"""
    assert getattr(mb.get_username, ng.GUARDED, False), "muicebot.muice 里的引用没被包住"


def t_moe_chat_reference_is_guarded_too():
    assert getattr(moe.get_username, ng.GUARDED, False), "moe_chat 里那份引用漏包了"


def t_install_twice_does_not_stack_the_timeout():
    """重复安装不许把超时叠两层（NoneBot 重载插件、测试里再调一次都会走到这）。"""
    ngw.install()                              # 先把这个测试自己刚 import 出来的引用补上
    before = (mb.get_username, moe.get_username)
    assert ngw.install() == 0, "第二次装不该再包一层"
    assert (mb.get_username, moe.get_username) == before, "已经包过的又被换了一次"


def t_normal_lookup_still_returns_the_name():
    """没卡住的时候什么都不该改 —— 别把群名片弄丢。"""
    async def ok(user_id=None, event=None):
        await asyncio.sleep(0)
        return "漂泊者"

    old = mb.get_username
    mb.get_username = ok
    try:
        wrapped = ng.guard(mb.get_username, 0.5)
        assert asyncio.run(wrapped(None, None)) == "漂泊者"
    finally:
        mb.get_username = old


def t_off_switch_leaves_everything_untouched():
    """NAME_GUARD=0 是回滚用的：一个引用都不该动。

    注意要用**没包过**的模块来试 —— 拿已经包好的 MuiceBot 断言「install 返回 0」
    是条空断言（幂等也会返回 0），开关删掉照样过。
    """
    async def real_name(user_id=None, event=None):
        return "本名"

    class Fresh:
        get_username = staticmethod(real_name)

    mod = Fresh()
    cfg = nonebot.get_driver().config
    old = getattr(cfg, "name_guard", None)
    cfg.name_guard = "0"
    try:
        assert ngw.install([mod]) == 0, "关了开关还报告包了引用"
        assert mod.get_username is real_name, "关了开关还是把引用换掉了"
    finally:
        if old is None:
            try:
                del cfg.name_guard
            except AttributeError:
                pass
        else:
            cfg.name_guard = old


def t_the_timeout_can_come_from_the_process_env():
    """临时压一档超时不该只能去改 `.env` —— `NAME_GUARD_TIMEOUT=0.05 python bot.py` 也要认。

    线上验证「等不到时到底顶不顶得住」就得把它调小（真的让协议端不答做不到：
    `muice.py:174` 是空手调用，`nonebot.get_bot()` 拿的是**第一个**连上的 Bot，
    也就是真 SnowLuma，它总是秒回，自检脚本那个假连接根本收不到这一跳）。
    """
    cfg = nonebot.get_driver().config
    old_cfg = getattr(cfg, "name_guard_timeout", None)
    old_env = os.environ.get("NAME_GUARD_TIMEOUT")
    try:
        cfg.name_guard_timeout = 3          # .env 里写死的值：进程环境该盖过它
        os.environ["NAME_GUARD_TIMEOUT"] = "0.05"
        got = ngw._timeout()
        assert got == 0.05, f"没读进程环境变量（拿到 {got}，说明只认 .env）"
    finally:
        if old_env is None:
            os.environ.pop("NAME_GUARD_TIMEOUT", None)
        else:
            os.environ["NAME_GUARD_TIMEOUT"] = old_env
        if old_cfg is None:
            try:
                del cfg.name_guard_timeout
            except AttributeError:
                pass
        else:
            cfg.name_guard_timeout = old_cfg


def t_the_process_env_turns_the_switch_off():
    """`NAME_GUARD=0` 从进程环境来也得算数 —— 回滚不该被迫编辑文件。"""
    cfg = nonebot.get_driver().config
    old_cfg = getattr(cfg, "name_guard", None)
    old_env = os.environ.get("NAME_GUARD")
    try:
        cfg.name_guard = "1"
        os.environ["NAME_GUARD"] = "0"
        assert ngw._flag("name_guard") is False, "关了开关没生效（只看了 .env）"
    finally:
        if old_env is None:
            os.environ.pop("NAME_GUARD", None)
        else:
            os.environ["NAME_GUARD"] = old_env
        if old_cfg is None:
            try:
                del cfg.name_guard
            except AttributeError:
                pass
        else:
            cfg.name_guard = old_cfg


def t_without_any_override_it_uses_the_default():
    """两边都没写就用 `name_guard_logic.DEFAULT_TIMEOUT`，不能崩也不能是 0。"""
    cfg = nonebot.get_driver().config
    old_cfg = getattr(cfg, "name_guard_timeout", None)
    old_env = os.environ.get("NAME_GUARD_TIMEOUT")
    try:
        try:
            del cfg.name_guard_timeout
        except AttributeError:
            pass
        os.environ.pop("NAME_GUARD_TIMEOUT", None)
        assert ngw._timeout() == ng.DEFAULT_TIMEOUT, "两处都没写时该退成默认超时"
    finally:
        if old_env is not None:
            os.environ["NAME_GUARD_TIMEOUT"] = old_env
        if old_cfg is not None:
            cfg.name_guard_timeout = old_cfg


def t_our_import_did_not_break_muicebot_plugins():
    """本插件不许把 MuiceBot 的内置插件导入弄坏。

    曾经用 `importlib.import_module("muicebot.builtin_plugins.moe_chat")` 去找目标，
    而本插件正是在 MuiceBot 加载内置插件的半路上被拉起来的 —— 结果它的导入被做一半，
    日志里直接 `Failed to import "muicebot.builtin_plugins.moe_chat"`，功能整个没了一半。
    """
    names = {p.module_name for p in nonebot.get_loaded_plugins()}
    assert "muicebot.builtin_plugins.moe_chat" in names, \
        "moe_chat 没加载成功（八成是这里抢跑 import 了）"
    assert "plugins.name_guard" in names, "name_guard 自己没加载"


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("t_")]:
        run(fn.__name__, fn)
    print()
    total = len([k for k in globals() if k.startswith("t_")])
    print(f"{total - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
