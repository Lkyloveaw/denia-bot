r"""知识库接线的断言测试 —— 拿真实的 `ModelRequest` 喂注入钩子，看它到底问了什么。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_kb_wiring.py

跟 `test_kb_logic.py` 的分工：那边管「检索词该怎么切」，这边管
「插件真的照那么切了吗、切完真的发给检索服务了吗」。两条历史事故都在这里：

- 把「达妮娅是谁」剥成「是谁」，主语丢了，永远不命中；
- 反过来无脑用原文，则「达妮娅，今天中午吃什么」会把她自己的一堆设定塞进闲聊。

不连 QQ、不打网络：`_search_once` 换成假函数，只记录「实际问出去的那句话」。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)  # MuiceBot 按 ./plugins 找插件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot  # noqa: E402

try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot
ensure_nonebot()
import plugins.kb as kb  # noqa: E402
from muicebot.llm import ModelRequest  # noqa: E402
from nonebot.adapters.onebot.v11 import Message, MessageSegment, PrivateMessageEvent  # noqa: E402

SELF = "3892894069"
NICKS = ("达妮娅", "小娅", "娅")
FAILED: list[str] = []

HIT = {"path": "设定 > 达妮娅", "text": "达妮娅的设定正文。" * 4, "dense": 0.57}


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


def private_event(text: str, user: str = "123456") -> PrivateMessageEvent:
    m = Message()
    if text:
        m += MessageSegment.text(text)
    return PrivateMessageEvent(
        time=1700000000,
        self_id=int(SELF),
        post_type="message",
        sub_type="friend",
        user_id=int(user),
        message_type="private",
        message_id=2,
        message=m,
        raw_message=str(m),
        font=0,
        sender={"card": "", "nickname": "t", "role": "friend", "title": ""},
        to_me=True,
    )


def install_search(hits_for=lambda q: "达妮娅" in q):
    """换掉真正发 HTTP 的那一层，返回它记下的「问出去的句子」。"""
    calls: list[str] = []
    stats: dict[str, dict] = {}

    async def search(q: str):
        calls.append(q)
        if hits_for(q):
            stats[q] = {"entries": ["达妮娅"], "pool": 124, "exact": True}
            return [dict(HIT)], stats[q]
        stats[q] = {"entries": [], "pool": 0, "exact": None}
        return [], stats[q]

    old = kb._search_once
    kb._search_once = search
    kb.NICKNAMES = NICKS
    return calls, stats, lambda: setattr(kb, "_search_once", old)


def ask(prompt: str) -> tuple[str | None, ModelRequest]:
    """走一遍注入钩子，返回注入后的资料块（没注入就是 None）。"""
    calls, _stats, restore = install_search()
    try:
        req = ModelRequest(prompt=prompt, system="你的人格提示词")
        asyncio.run(kb.inject(req, private_event(prompt)))
        block = (req.system or "")[len("你的人格提示词"):] or None
        return block, req
    finally:
        restore()


def asked(prompt: str) -> list[str]:
    """这条消息实际被问出去的检索词。"""
    calls, _stats, restore = install_search()
    try:
        asyncio.run(kb.inject(ModelRequest(prompt=prompt), private_event(prompt)))
        return list(calls)
    finally:
        restore()


# ---------------------------------------------------------------- 注入路径（C）


def t_question_about_her_keeps_the_subject():
    """「达妮娅是谁」必须整句去查 —— 剥掉唤醒词就等于把问题扔了。"""
    assert asked("达妮娅是谁") == ["达妮娅是谁"]


def t_question_about_her_gets_injected():
    block, _ = ask("达妮娅是谁")
    assert block and HIT["text"][:50] in block, f"该把命中块拼进 system：{block!r}"


def t_vocative_is_stripped_and_not_injected():
    """「达妮娅，今天中午吃什么」里的逗号说明那是称呼 —— 不许注入她自己的设定。"""
    assert asked("达妮娅，今天中午吃什么") == ["今天中午吃什么"]
    block, _ = ask("达妮娅，今天中午吃什么")
    assert block is None, "无关闲聊一个字都不该塞"


def t_bare_calling_asks_nothing_at_all():
    assert asked("达妮娅") == [], "光叫人，连检索服务都不该打扰"


def t_group_prompt_with_speaker_tag_does_not_leak():
    """真实群里 MuiceBot 把发言人昵称拼进 prompt（`muice.py:174`），唤醒词就不在句首了。

    线上日志 14:46 那条就是证据：`检索词「<对乙酰氨基酚> 达妮娅，在吗」命中词条「达妮娅」`
    —— 有人问她在吗，却把她自己的设定塞了 1258 字进 prompt。
    """
    assert asked("<对乙酰氨基酚> 达妮娅，在吗") == ["在吗"]
    block, _ = ask("<对乙酰氨基酚> 达妮娅，在吗")
    assert block is None, "称呼后面只剩「在吗」，一个字都不该塞"
    assert asked("<芝士小特，喜欢用表情包> 你是水蜜桃派吗") == ["你是水蜜桃派吗"], \
        "昵称自己就能命中词条，更不能当检索词"


def t_group_prompt_about_her_still_hits():
    assert asked("<对乙酰氨基酚> 达妮娅是谁") == ["达妮娅是谁"]
    block, _ = ask("<对乙酰氨基酚> 达妮娅是谁")
    assert block and HIT["text"][:50] in block, "群里问她的事情照样该注入"


# ---------------------------------------------------------------- 诊断路径（A）


def probe(arg: str) -> tuple[str | None, list[str]]:
    calls, _stats, restore = install_search()
    try:
        out = asyncio.run(kb.run_probe(arg))
        return out, list(calls)
    finally:
        restore()


def t_probe_asks_the_raw_text():
    """/kb 查 问的是「这句话会不会命中」，不是「聊天时会拿什么去查」—— 所以用原文。"""
    text, calls = probe("查 达妮娅")
    assert calls == ["达妮娅"], f"该拿原文去查，实际 {calls}"
    assert "达妮娅" in text and "没命中" not in text, text


def t_probe_reports_not_asked_separately_from_no_hit():
    """剥空/太短 = 压根没发请求，报成「未命中」就是把 bug 写成特性。"""
    text, calls = probe("查 嗯")
    assert calls == [], "太短不该发 HTTP"
    assert "没去查" in text and "未命中" not in text and "没命中" not in text, text


def t_probe_reports_no_hit_after_asking():
    text, calls = probe("查 今天中午吃什么")
    assert calls == ["今天中午吃什么"], calls
    assert "没命中" in text, text


def t_probe_shows_what_the_chat_path_would_ask():
    """诊断命中了、聊天时却不一定会命中 —— 两句都得说出来，否则又是「查了没命中」那种坑。"""
    text, _ = probe("查 达妮娅")
    assert "聊天时" in text, f"该提示聊天路径实际会问什么：{text}"


def t_probe_keeps_letter_case():
    _text, calls = probe("查 EMA怎么用")
    assert calls == ["EMA怎么用"], f"别把大小写改了：{calls}"


def t_probe_says_the_service_did_not_answer():
    """第三种「没结果」：话问出去了、服务没答上来。报成「未命中」会让人去改词条而不是去拉服务。"""
    calls: list[str] = []

    async def dead(q: str):
        calls.append(q)
        return [], {"error": "ConnectError: all attempts failed"}

    old = kb._search_once
    kb._search_once = dead
    try:
        text = asyncio.run(kb.run_probe("查 达妮娅"))
    finally:
        kb._search_once = old
    assert calls == ["达妮娅"]
    assert "没答上来" in text and "未命中" not in text, text


def t_probe_only_answers_its_own_subcommand():
    assert kb.run_probe is not None
    _text, calls = probe("on")
    assert calls == [] and _text is None, "on/off 不是试检索"


def t_probe_without_a_question_shows_usage():
    text, calls = probe("查")
    assert calls == [] and "用法" in text, text


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
