r"""署名规范化的接线断言 —— 拿真实的 `ModelRequest` 喂钩子，看模型最终会看到什么。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_prompt_wiring.py

跟 `test_prompt_logic.py` 的分工：那边管「该怎么切、怎么排」，这边管
「插件真的照那么做了吗、改动真的落到发给模型的那个对象上了吗」。

复现的是 2026-09-27 15:20 那条真实消息（群 922192732，主人昵称「对乙酰氨基酚」）：
MuiceBot 会把 `娅娅` 拼成 `<对乙酰氨基酚> 娅娅` 再喂给模型，于是她回了一段用药科普。
不连 QQ、不打网络，也不去改 site-packages —— 钩子改的是同一个 `ModelRequest` 对象
（`muice.py:255-262`），所以这里断言的就是真正出门的内容。
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)  # MuiceBot 按 ./configs、./plugins 找文件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot  # noqa: E402

nonebot.init()
nonebot.load_plugin("muicebot")

import kb_logic as kl  # noqa: E402
import plugins.prompt_guard as pg  # noqa: E402
from muicebot.llm import ModelRequest  # noqa: E402
from muicebot.models import Message as Chat  # noqa: E402
from muicebot.plugin import context as ctx  # noqa: E402
from muicebot.plugin.hook._types import HookType  # noqa: E402
from muicebot.plugin.hook.manager import hook_manager  # noqa: E402
from nonebot.adapters.onebot.v11 import (  # noqa: E402
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)

SELF = 3892894069
OWNER = 1690711850
OTHER = 982272752
GRP = 922192732
OTHER_GRP = 1108838060
GKEY = f"qq_OneBot V11_{SELF}_{GRP}"
OKEY = f"qq_OneBot V11_{SELF}_{OTHER_GRP}"
NICKS = ("达妮娅", "小娅", "娅")
MARK = "【群聊消息的读法】"  # NOTE 自己就写着两遍 〔发言人:…〕，别拿它当计数锚点
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


def group_event(text: str, user: int = OWNER, group: int = GRP) -> GroupMessageEvent:
    m = Message()
    m += MessageSegment.at(SELF)
    m += MessageSegment.text(text)
    return GroupMessageEvent(
        time=1790493646, self_id=SELF, post_type="message", message_type="group",
        sub_type="normal",
        user_id=user, group_id=group, message_id=1621731289, message=m,
        raw_message=str(m), font=0,
        sender={"card": "", "nickname": "对乙酰氨基酚", "role": "member", "title": ""},
        to_me=True,
    )


def private_event(text: str, user: int = OWNER) -> PrivateMessageEvent:
    m = Message()
    if text:
        m += MessageSegment.text(text)
    return PrivateMessageEvent(
        time=1790493646, self_id=SELF, post_type="message", message_type="private",
        sub_type="friend", user_id=user, message_id=1, message=m, raw_message=str(m), font=0,
        sender={"card": "", "nickname": "对乙酰氨基酚", "role": "friend", "title": ""},
        to_me=True,
    )


def muice_turn(t, user, gid, msg, resp) -> Chat:
    """照 `muice.py` 给模型的样子造一条历史：群聊那份带 `<署名>`，私聊那份不带。"""
    return Chat(time=t, userid=str(user), groupid=gid, message=msg, respond=resp)


def accident_request() -> ModelRequest:
    """15:20 那一轮的真实形态：一份原文 + 一份带署名（同一条的两份）、旧群一条、私聊一条。"""
    raw = muice_turn("2026.09.27 15:20:46", OWNER, GKEY, "娅娅", "上一轮的回话")
    twin = muice_turn("2026.09.27 15:20:46", OWNER, GKEY, "<对乙酰氨基酚> 娅娅", "上一轮的回话")
    stale = muice_turn("2026.09.27 15:13:38", OWNER, OKEY, "娅娅今天西格莉卡找你了吗", "没有呢……")
    priv = muice_turn("2026.09.27 15:19:00", OWNER, "-1", "达妮娅 在干嘛呢", "在打盹。")
    mate = muice_turn("2026.09.27 15:21:42", OTHER, GKEY, "<穿梭机> 你搁哪里发财呢", "在星炬学院混日子。")
    req = ModelRequest(
        prompt="<对乙酰氨基酚> 娅娅",
        history=[stale, raw, twin, priv, mate],
        system="你现在是《鸣潮》中的「达妮娅」。",
    )
    return req, [raw, twin, stale, priv, mate]


def t_prompt_loses_the_drug_name():
    req, _ = accident_request()
    asyncio.run(pg.guard(req, group_event("娅娅")))
    assert req.prompt == "〔发言人:漂泊者〕\n娅娅", f"实际 {req.prompt!r}"
    assert "对乙酰氨基酚" not in req.prompt, "药名还留在模型看到的正文里"
    assert "<" not in req.prompt and "〔" in req.prompt


def t_known_person_uses_the_configured_name():
    """templates.yml 给主人配的是「漂泊者」—— 人格语气跟着称呼走，不该跟着 QQ 昵称漂。"""
    req, _ = accident_request()
    asyncio.run(pg.guard(req, group_event("娅娅")))
    assert "漂泊者" in req.prompt, req.prompt


def t_history_deduped_sorted_and_scoped():
    req, _ = accident_request()
    asyncio.run(pg.guard(req, group_event("娅娅")))
    times = [m.time for m in req.history]
    assert times == sorted(times), f"历史还是乱的：{times}"
    assert len(req.history) == 2, f"本群只该留 2 条，实际 {[(m.time, m.message) for m in req.history]}"
    joined = " ".join(m.message for m in req.history)
    assert "西格莉卡" not in joined and "在打盹" not in joined, "旧群/私聊的话漏进本群了"


def t_history_rows_keep_who_said_what():
    req, _ = accident_request()
    asyncio.run(pg.guard(req, group_event("娅娅")))
    assert req.history[-1].message == "〔发言人:穿梭机〕\n你搁哪里发财呢", req.history[-1].message


def t_upstream_objects_not_mutated():
    """钩子只能改副本：那份 list 和这些 Message 是 `muice.py:259` 的日志与落库要用的原值。"""
    req, originals = accident_request()
    asyncio.run(pg.guard(req, group_event("娅娅")))
    for m in originals:
        assert not m.message.startswith("〔发言人"), f"上游对象被改了：{m.message!r}"


def t_reading_rule_instructed_exactly_once():
    req, _ = accident_request()
    asyncio.run(pg.guard(req, group_event("娅娅")))
    assert req.system.startswith("你现在是《鸣潮》"), "别把人格提示词覆盖掉"
    assert req.system.count(MARK) == 1, "读法说明重复注入了"
    assert "不是话题" in req.system


def t_running_it_twice_stays_idempotent():
    req, _ = accident_request()
    ev = group_event("娅娅")
    asyncio.run(pg.guard(req, ev))
    once = req.prompt
    asyncio.run(pg.guard(req, ev))
    assert req.prompt == once, f"跑第二遍把署名套上了：{req.prompt!r}"
    assert req.system.count(MARK) == 1


def t_private_turn_gets_no_label_and_keeps_only_privates():
    req, _ = accident_request()
    req.prompt = "达妮娅 在干嘛呢"
    asyncio.run(pg.guard(req, private_event("达妮娅 在干嘛呢")))
    assert req.prompt == "达妮娅 在干嘛呢", req.prompt
    assert [m.message for m in req.history] == ["达妮娅 在干嘛呢"], req.history


def t_isolation_can_be_switched_off():
    """`PROMPT_GUARD_ISOLATE=0`：跨群/私聊记忆照带（回滚开关，不用改代码）。"""
    nonebot.get_driver().config.prompt_guard_isolate = "0"
    try:
        req, _ = accident_request()
        asyncio.run(pg.guard(req, group_event("娅娅")))
        assert len(req.history) == 4, f"关了隔离还只留 {len(req.history)} 条"
    finally:
        del nonebot.get_driver().config.prompt_guard_isolate


def t_kill_switch_leaves_everything_alone():
    nonebot.get_driver().config.prompt_guard = "0"
    try:
        req, _ = accident_request()
        asyncio.run(pg.guard(req, group_event("娅娅")))
        assert req.prompt == "<对乙酰氨基酚> 娅娅", req.prompt
        assert len(req.history) == 5
    finally:
        del nonebot.get_driver().config.prompt_guard


def t_guard_output_is_what_kb_will_search():
    """两个钩子的先后次序（priority 1 → 5）落在这条断言上：kb 拿到的是干净问句。"""
    req, _ = accident_request()
    req.prompt = "<对乙酰氨基酚> 达妮娅，在吗"
    asyncio.run(pg.guard(req, group_event("达妮娅，在吗")))
    assert kl.plan_queries(req.prompt, NICKS) == ["在吗"], kl.plan_queries(req.prompt, NICKS)


def t_unlabeled_prompt_is_left_alone():
    """`template_mode: user` 时正文前还压着一段指令，认不出署名就别动（当前配置走不到）。"""
    req, _ = accident_request()
    req.prompt = "一些用户指令\n\n<对乙酰氨基酚> 娅娅"
    asyncio.run(pg.guard(req, group_event("娅娅")))
    assert req.prompt == "一些用户指令\n\n<对乙酰氨基酚> 娅娅", req.prompt


def t_hooks_declare_every_param_they_take():
    """钩子的参数是**按类型注解**注入的（`plugin/hook/manager.py:53`：拿不到注解就 `continue`）。

    漏写注解不是「注入不到」而是「压根不传」：调用点变成 `guard(request=...)`，
    当场 `TypeError: missing 1 required positional argument: 'event'`。这个异常在
    `ask_stream` 里、被后台任务吃掉，日志只留一句「正在调用模型...」——
    2026-09-27 16:11 到 17:2x 全线不回话就是这个（闸门放行、模型一次都没被调过）。
    """
    mine = [h.function for h in hook_manager._hooks[HookType.BEFORE_MODEL_COMPLETION]
            if str(getattr(h.function, "__module__", "")).startswith("plugins.")]
    assert mine, "自家插件的钩子一个都没注册上，这条断言等于没跑"
    for fn in mine:
        for name, param in inspect.signature(fn).parameters.items():
            assert param.annotation is not inspect.Parameter.empty, (
                f"{fn.__module__}.{fn.__name__} 的参数 {name} 没写注解 —— "
                "MuiceBot 不会传它，钩子一调就 TypeError"
            )


def t_running_through_the_hook_manager_rewrites_the_prompt():
    """以前只 `asyncio.run(pg.guard(req, ev))` 直接调函数，注入这一层压根没被走到。

    这里换成真的 `hook_manager.run`：跑不通就是线上那种「一条都不回」。
    """
    req, _ = accident_request()
    ev = group_event("娅娅")

    async def go():
        ctx.set_ctx(None, ev, {}, None)  # 钩子里的 Event/Bot 由这几个 contextvar 供给
        await hook_manager.run(HookType.BEFORE_MODEL_COMPLETION, req)

    asyncio.run(go())
    assert req.prompt == "〔发言人:漂泊者〕\n娅娅", req.prompt
    assert MARK in (req.system or ""), "钩子没跑完（读法说明是最后一步加的）"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
