r"""署名查询超时的纯逻辑断言 —— 协议端不答时，她到底该拿什么当署名。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_name_guard_logic.py

为什么要有这一层（2026-09-27 那次「她整条消息不回」排查出来的）：

MuiceBot 每次拼提示词都要问一句「这个人叫什么」（`muice.py:174`、`muice.py:211`），
这一跳走 `nonebot_plugin_userinfo` → `bot.get_group_member_info()` 失败后回退
`bot.get_stranger_info()`，而 nonebot-adapter-onebot 的 API Future **没有超时**。
于是协议端一回不答（假 QQ 号、机器人不在那个群、QQ 侧抽风），整条对话就永远挂在
「正在调用模型...」之后 —— 不报错、不打日志、事件循环还活着，看起来像她已读不回。

这里只补一件事：问名字**最多等 N 秒**，等不到就用能确定下来的称呼顶上，
让她照常回话。别的事情一律不管（连超时以外的异常都不吞，见最后两条）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import name_guard_logic as ng  # noqa: E402

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


class Event:
    """只够用来取 userid 的事件替身。"""

    def __init__(self, user_id: str):
        self._u = user_id

    def get_user_id(self) -> str:
        return self._u


def call(coro):
    return asyncio.run(coro)


# ---------- 正常路径：名字问得到就照原样用 ----------

def t_fast_answer_passes_through():
    async def orig(user_id=None, event=None):
        await asyncio.sleep(0)
        return "漂泊者"

    assert call(ng.guard(orig, 0.5)(None, Event("123456"))) == "漂泊者"


# ---------- 卡住：不能让她跟着一起挂 ----------

def t_timeout_falls_back_to_the_id():
    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)
        return "绝不该走到这里"

    got = call(ng.guard(hang, 0.05)("555000111", None))
    assert got == "555000111", f"卡住时该用传进来的 QQ 号，结果是 {got!r}"


def t_timeout_uses_the_event_when_no_id_was_given():
    """`muice.py:174` 就是空手叫的（`await get_username()`）， userid 在事件里。"""

    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)

    got = call(ng.guard(hang, 0.05)(None, Event("1690711850")))
    assert got == "1690711850", f"该从事件里取 userid，结果是 {got!r}"


def t_timeout_without_anything_to_say_is_a_placeholder():
    """一句都认不出来时也不能顶个空字符串 —— 空署名会把后面的判断整个带跑。"""

    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)

    got = call(ng.guard(hang, 0.05)(None, None))
    assert got == ng.PLACEHOLDER, f"该退成占位称呼，结果是 {got!r}"
    assert got and got.strip(), "占位称呼不能是空白"


def t_the_hanging_lookup_is_cancelled():
    """超时必须把里面那一跳取消掉，否则挂住的 API 请求会越积越多。"""
    cancelled = []

    async def hang(user_id=None, event=None):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    call(ng.guard(hang, 0.05)("1", None))
    assert cancelled == [True], "超时后原来那个查询没被取消"


def t_timeout_is_reported_not_silent():
    """不答这件事必须留下记录，否则下次排查还得从头猜。"""
    seen: list[tuple] = []

    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)

    call(ng.guard(hang, 0.05, on_timeout=lambda n, t: seen.append((n, t)))("555000111", None))
    assert seen == [("555000111", 0.05)], f"该上报（顶上去的称呼, 等了多久），实际 {seen}"


# ---------- 边界：只管超时，别的一律不吞 ----------

def t_other_exceptions_are_not_swallowed():
    async def boom(user_id=None, event=None):
        raise ValueError("上游炸了")

    try:
        call(ng.guard(boom, 0.5)(None, None))
    except ValueError:
        return
    raise AssertionError("非超时的异常不该被这层吃掉")


def t_timeout_asks_the_running_context_for_the_event():
    """`muice.py:174` 是空手叫的（`await get_username()`）—— 只认参数就会退成「群友」，
    把 QQ 号弄丢；后面按署名取昵称、按人查记忆那条链就断了。"""

    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)

    got = call(ng.guard(hang, 0.05, current_event=lambda: Event("1690711850"))())
    assert got == "1690711850", f"该去上下文里拿事件，结果是 {got!r}"


def t_a_context_that_refuses_to_answer_still_falls_back():
    """上下文里没有正在处理的事件时，`get_event()` 是会抛的 —— 兜底自己不能再炸。"""

    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)

    def boom():
        raise LookupError("没有正在处理的事件")

    got = call(ng.guard(hang, 0.05, current_event=boom)())
    assert got == ng.PLACEHOLDER, f"该退成占位称呼，结果是 {got!r}"


def t_default_timeout_is_short_but_not_rude():
    """等太久等于不回；等太短会把正常的名片查询误判成不答。"""
    assert 0.5 <= ng.DEFAULT_TIMEOUT <= 8.0, f"默认超时 {ng.DEFAULT_TIMEOUT} 不合理"


# ---------- 接线要用到的「按模块换引用」 ----------

def t_patch_wraps_the_reference_that_module_actually_uses():
    """`muice.py` 是 `from ... import get_username`，只改源头管不到它。"""

    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)

    class FakeMod:
        get_username = staticmethod(hang)

    mod = FakeMod()
    assert ng.patch([mod], 0.05) == 1, "该报告包了一处"
    assert call(mod.get_username("555000111", None)) == "555000111"


def t_patch_is_idempotent():
    """装第二次不许把超时套两层。"""

    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)

    class FakeMod:
        get_username = staticmethod(hang)

    mod = FakeMod()
    ng.patch([mod], 0.05)
    first = mod.get_username
    assert ng.patch([mod], 0.05) == 0, "重复安装不该再包一层"
    assert mod.get_username is first, "重复安装把已包好的换掉了"


def t_patch_skips_a_module_without_that_name():
    class Nope:
        pass

    assert ng.patch([Nope()], 0.05) == 0


# ---------- 线上要能自证：到底包住了谁、到底走没走到 ----------

def t_patch_reports_what_it_wrapped():
    """装的这一下必须能报到日志里 —— 线上那次「保险没起作用」就是没法自证。"""

    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)

    class FakeMod:
        __name__ = "muicebot.muice"
        get_username = staticmethod(hang)

    seen: list[tuple] = []
    ng.patch([FakeMod()], 0.05, on_wrap=lambda name, fn: seen.append((name, fn)))
    assert [n for n, _ in seen] == ["muicebot.muice"], f"该报模块名，实际 {seen}"
    assert seen[0][1] is hang, "还要报被换掉的那一份，好知道它来自哪个模块"


def t_patch_reports_nothing_when_there_is_nothing_to_wrap():
    class FakeMod:
        __name__ = "muicebot.muice"
        get_username = staticmethod(lambda user_id=None, event=None: "")

    seen: list[tuple] = []
    mod = FakeMod()
    ng.patch([mod], 0.05, on_wrap=lambda n, f: seen.append(n))
    ng.patch([mod], 0.05, on_wrap=lambda n, f: seen.append(n))
    assert len(seen) == 1, f"重复安装不该再报一次，实际 {seen}"


def t_patch_passes_the_callbacks_through():
    """包进去的那一份也得会喊人 —— 接线只跟 `patch` 打交道。"""

    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)

    class FakeMod:
        __name__ = "m"
        get_username = staticmethod(hang)

    entered: list[float] = []
    timed: list[tuple] = []
    mod = FakeMod()
    ng.patch([mod], 0.05, on_timeout=lambda n, t: timed.append((n, t)),
             on_enter=lambda t: entered.append(t),
             current_event=lambda: Event("1690711850"))
    assert call(mod.get_username()) == "1690711850", "空手调用时该从上下文拿 userid"
    assert entered == [0.05] and timed == [("1690711850", 0.05)], f"{entered} / {timed}"


def t_guard_says_it_was_asked():
    """每次真的去问名字要能留痕：没有这行 = 保险根本没在调用链上。"""
    calls: list[float] = []

    async def ok(user_id=None, event=None):
        return "漂泊者"

    assert call(ng.guard(ok, 0.5, on_enter=lambda t: calls.append(t))()) == "漂泊者"
    assert calls == [0.5], f"该在问之前报一次超时上限，实际 {calls}"


def t_the_entry_log_survives_a_timeout():
    """卡住的时候「去过」这条更要留着 —— 那是判断走没走到保险的唯一凭据。"""
    calls: list[float] = []

    async def hang(user_id=None, event=None):
        await asyncio.sleep(5)

    got = call(ng.guard(hang, 0.05, on_enter=lambda t: calls.append(t))("777", None))
    assert calls == [0.05] and got == "777"


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("t_")]:
        run(fn.__name__, fn)
    print()
    print(f"{len([k for k in globals() if k.startswith('t_')]) - len(FAILED)} 通过 / "
          f"{len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
