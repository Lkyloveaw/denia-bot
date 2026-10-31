r"""引用修复的接线断言 —— 逐字复现 `muicebot/onebot.py:621-626` 那段拼接。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_quote_wiring.py

跟 `test_quote_logic.py` 的分工：那边管「该渲染成什么样」，这边管
「MuiceBot 真跑的那几行还崩不崩、引用内容到不到得了模型」。

复现的是 2026-09-27 15:52:54 那条真实消息（群 1108838060，1447458891）：
QQ 的「回复」自动带了 @，闸门放行，然后 MuiceBot 死在拼「被引用的消息」那一行，
整条消息一个字都没回。这里不连 QQ、不发请求，只把 alconna 交给它的那个对象如实造出来。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)  # MuiceBot 按 ./configs、./plugins 找文件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot  # noqa: E402

try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot
ensure_nonebot()
import plugins.quote_fix as qf  # noqa: E402
from nonebot.adapters import Message as BotMessage  # noqa: E402
from nonebot.adapters.onebot.v11 import Message as OBMessage  # noqa: E402
from nonebot.adapters.onebot.v11 import MessageSegment as OBSeg  # noqa: E402
from nonebot.adapters.onebot.v11.event import Reply as OBReply  # noqa: E402
from nonebot_plugin_alconna import Reply, UniMessage  # noqa: E402
from nonebot_plugin_alconna.builtins.extensions.reply import (  # noqa: E402
    ReplyRecordExtension,
)

SELF = 3892894069
FRIEND = 1447458891
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


def quoted_ob_message(text: str) -> OBMessage:
    """被引用的那条原话：OneBot v11 原生 Message —— 就是它把 MuiceBot 崩掉的形态。"""
    m = OBMessage()
    m += OBSeg.text(text)
    return m


def alconna_reply(msg, card: str = "", nickname: str = "达妮娅", user_id: int = SELF) -> Reply:
    """照 `uniseg/adapters/onebot11/builder.py:64-69` 的写法造缓存里那个对象。"""
    origin = OBReply(
        time=1790495574, message_type="group", message_id=1917583215, real_id=1917583215,
        sender={"card": card, "nickname": nickname, "user_id": user_id},
        message=msg if msg is not None else quoted_ob_message(""),
    )
    return Reply(str(origin.message_id), msg, origin)


def muice_merge(bot_message, reply_message):
    """逐字抄 `muicebot/onebot.py:621-626`：它当年怎么崩，这里就怎么崩。"""
    if isinstance(reply_message, BotMessage):
        return bot_message + UniMessage("\n被引用的消息: ") + UniMessage(reply_message)
    return bot_message + UniMessage(f"\n被引用的消息: {reply_message}")


def current_turn(text: str):
    return UniMessage(text)


def t_unpatched_quote_crashes_exactly_like_production():
    reply = alconna_reply(quoted_ob_message("嗯……还好，那里只是时间和空间有点乱。"))
    try:
        muice_merge(current_turn("你还能回来吗？"), reply.msg)
    except TypeError as e:
        assert "Unsupported type" in str(e), str(e)
        return
    raise AssertionError("复现失败：没崩，说明这条测试没在测当年那个现场")


def t_fix_reply_renders_the_quote_with_a_speaker():
    reply = alconna_reply(quoted_ob_message("嗯……还好，那里只是时间和空间有点乱。"))
    out = qf.fix_reply(reply)
    assert out is reply and isinstance(reply.msg, str), type(reply.msg)
    assert reply.msg == "〔发言人:达妮娅〕\n嗯……还好，那里只是时间和空间有点乱。", repr(reply.msg)
    assert not isinstance(reply.msg, BotMessage), "还是框架原生 Message，MuiceBot 那条分支照样崩"


def t_patched_quote_reaches_the_model():
    reply = alconna_reply(quoted_ob_message("嗯……还好，那里只是时间和空间有点乱。"))
    qf.fix_reply(reply)
    merged = muice_merge(current_turn("你还能回来吗？"), reply.msg)
    text = merged.extract_plain_text()
    assert "你还能回来吗？" in text and "被引用的消息" in text and "时间和空间有点乱" in text, text


def t_owner_quote_uses_the_configured_name():
    """被引用的是主人说的话：称呼走 templates.yml（对乙酰氨基酚 → 漂泊者），跟 prompt_guard 同源。"""
    reply = alconna_reply(
        quoted_ob_message("娅娅"), nickname="对乙酰氨基酚", user_id=1690711850
    )
    qf.fix_reply(reply)
    assert reply.msg.startswith("〔发言人:"), repr(reply.msg)
    assert "对乙酰氨基酚" not in reply.msg, reply.msg


def t_image_only_quote_still_goes_in_as_a_marker():
    m = OBMessage()
    m += OBSeg.image(file="ABC.jpg")
    reply = alconna_reply(m, nickname="霞月", user_id=FRIEND)
    qf.fix_reply(reply)
    assert reply.msg == "〔发言人:霞月〕\n[图片]", repr(reply.msg)


def t_quote_with_nothing_renderable_is_dropped():
    """渲染不出任何文字就别硬拼 —— 宁可少一句引用，也不要 `被引用的消息: ` 这种空壳。"""
    assert qf.fix_reply(alconna_reply(OBMessage())) is None
    assert qf.fix_reply(Reply("1", None, None)) is None
    assert qf.fix_reply(None) is None


def t_already_rendered_forms_are_left_alone():
    """幂等：补丁跑第二遍（两个 matcher 各有一份缓存、对象却是同一个）不能把文字吃干净。"""
    reply = alconna_reply(quoted_ob_message("在吗"))
    once = qf.fix_reply(reply).msg
    assert qf.fix_reply(reply).msg == once, "第二次取缓存把引用改没了"

    # UniMessage 不是框架 Message 的子类 —— MuiceBot 走 else 分支，本来就不崩，别多事去改它
    uni = UniMessage("已经渲染过的引用")
    assert not isinstance(uni, BotMessage)
    kept = qf.fix_reply(Reply("2", uni, None))
    assert kept.msg is uni
    assert "已经渲染过的引用" in muice_merge(current_turn("正文"), uni).extract_plain_text()

    kept3 = qf.fix_reply(Reply("3", "  纯字符串引用  ", None))
    assert kept3 is not None and kept3.msg == "  纯字符串引用  ", "字符串原样留着，格式化是 MuiceBot 的事"
    assert qf.fix_reply(Reply("4", "   ", None)) is None, "空白字符串该当没引用"


def t_install_wraps_get_reply_and_is_idempotent():
    ext = ReplyRecordExtension()
    key = "1917583215"
    ext.cache[key] = alconna_reply(quoted_ob_message("唔……霞月，你再转下去，泡泡都要被绕晕了。"))

    assert qf.install() is True
    assert qf.install() is False, "装了第二层，取引用会被套两遍"
    got = ext.get_reply(key)
    assert got is not None and isinstance(got.msg, str), type(got.msg)
    assert "泡泡都要被绕晕了" in got.msg, got.msg
    assert ext.get_reply("不存在的那个 id") is None


def t_kill_switch_leaves_the_extension_alone():
    """`QUOTE_FIX=0`：整个补丁不装（回滚开关，改了 .env 重启即可，不用动代码）。"""
    nonebot.get_driver().config.quote_fix = "0"
    calls: list[int] = []
    real = qf.install
    qf.install = lambda: (calls.append(1), True)[1]
    try:
        asyncio.run(qf.install_quote_fix())
    finally:
        qf.install = real
        del nonebot.get_driver().config.quote_fix
    assert calls == [], "开关关了还是去装补丁"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
