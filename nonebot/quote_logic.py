r"""被引用（QQ 的「回复」）消息的渲染 —— 纯逻辑，不 import nonebot / muicebot。

`tests/test_quote_logic.py` 秒级能跑完，`plugins/quote_fix.py` 只负责接到 alconna 上。

## 为什么要有这个文件

MuiceBot 在 handler 里把被引用的那条消息拼进正文（`muicebot/onebot.py:621-626`）：

    if isinstance(reply_message, BotMessage):
        bot_message += UniMessage("\n被引用的消息: ") + UniMessage(reply_message)
    else:
        bot_message += UniMessage(f"\n被引用的消息: {reply_message}")

它假设 `.msg` 要么是 uniseg 的 `UniMsg`、要么能直接格式化。但 alconna 的 OneBot v11
分支给的是**框架原生 Message**（`uniseg/adapters/onebot11/builder.py:64-69`，
`Reply(str(_reply.message_id), _reply.message, _reply)` 里那个 `_reply.message`），
于是 `UniMessage(原生 Message)` 在 `uniseg/message.py:508` 抛
`TypeError: Unsupported type <class 'nonebot.adapters.onebot.v11.message.MessageSegment'>`
—— handler 死在调模型之前，**整条消息一个字都不回**（不是「丢了引用」，是全丢）。

而 QQ 的「回复」又会在正文前面自动带一个 @（@ 的是被引用那条的作者），所以闸门是放行它的：
线上 15:13:38 与 15:52:54 两条都是这样死的，同一个人把同一句话**不带引用重发一遍**就回了。

## 这里为什么渲染成字符串而不是 UniMsg

字符串走的是 `else` 分支，不依赖任何 bot/adapter 上下文（`get_reply` 被调时手上没有 bot），
也不会因为 alconna 再改类型而第二次崩。渲染结果沿用 `prompt_logic` 的署名格式
（`〔发言人:某某〕` + 换行 + 正文），这样她至少知道**被引用的那句是谁说的** ——
跟上一轮修好的「谁在说话」是同一套读法，不另立规矩。

引用里套引用（`[回复:-1107119364] @706069795 我`）只渲染最外层那条：链条全灌进去
既长又抢话题，而最外层已经带上了「他在回谁」这个信息。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    import prompt_logic as pl
except ImportError:  # 不是从 nonebot\ 目录启动的，把工程根补进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import prompt_logic as pl

MAX_CHARS = 240
"""引用内容进提示词的长度上限。引用常常是整段聊天记录，不封顶会把本轮正文挤没。"""

TOKENS = {
    "image": "[图片]",
    "record": "[语音]",
    "video": "[视频]",
    "face": "[表情]",
    "file": "[文件]",
    "location": "[位置]",
    "json": "[分享]",
    "xml": "[分享]",
    "dice": "[骰子]",
    "poker": "[扑克]",
    "markdown": "[卡片]",
}
"""非文字段的读法：给模型一个「这里原来有个东西」的记号，别编造内容。"""

SKIP = {"reply", "quote"}
"""引用里套引用时，内层那个指针没有信息量（正文已经在下面了），直接不吃。"""

AT_ALL = ("all", "everyone")

SPACE_RUN = re.compile(r"[ \t]{2,}")


def segment_view(seg: Any) -> tuple[str, dict]:
    """把一段消息统一成 `(type, data)`，收 `MessageSegment` 也收二元组（好测）。"""
    if isinstance(seg, tuple):
        return str(seg[0] or ""), seg[1] or {}
    return str(getattr(seg, "type", "") or ""), getattr(seg, "data", None) or {}


def at_text(qq: str, data: dict, names: dict[str, str] | None) -> str:
    """@ 段怎么读出来。

    优先用称呼表（`templates.yml` 里配过的熟人、机器人自己），其次用消息里带的
    `name`，最后才是裸 QQ 号 —— 「@3892894069」模型看不出是在叫她，「@达妮娅」看得出。
    """
    if qq in AT_ALL:
        return "@全体成员"
    return "@" + (str((names or {}).get(qq) or "").strip() or str(data.get("name") or "").strip() or qq)


def render_segments(segments: Iterable[Any], names: dict[str, str] | None = None) -> str:
    """一条消息的段列表 → 一行可读文本；什么都不剩时返回空串（调用方据此放弃引用）。"""
    parts: list[str] = []
    for seg in segments or ():
        kind, data = segment_view(seg)
        if not kind or kind in SKIP:
            continue
        if kind == "text":
            parts.append(str(data.get("text", "")))
        elif kind == "at":
            parts.append(at_text(str(data.get("qq", "") or "").strip(), data, names) + " ")
        elif kind in TOKENS:
            parts.append(TOKENS[kind] + " ")
        else:
            parts.append(f"[{kind}] ")
    out = "".join(parts).strip()
    out = SPACE_RUN.sub(" ", out)  # `@某某 ` 紧跟着正文自己带的空格会顶出两个空格，模型不必照抄
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS].rstrip() + "…"
    return out


def quoted_block(
    msg: Any,
    names: dict[str, str] | None = None,
    sender_card: str = "",
    sender_nickname: str = "",
    sender_id: str = "",
) -> str:
    """被引用的那条消息 → 拼进提示词的那一段；没有可用内容返回**空串**。

    收三种 `msg`：框架原生 `Message`（本次的崩点）、已经是字符串的、None。
    署名沿用 `prompt_logic.resolve_speaker`：熟人称呼 > 群名片/QQ 昵称 > `群友<QQ号>`，
    所以「对乙酰氨基酚」那种怪昵称不会又从这里爬回提示词（拿不到熟人表时它才会出现，
    而 `plugins/prompt_guard.py` 的读法说明会告诉模型那只是署名）。
    """
    if msg is None:
        return ""
    if isinstance(msg, str):
        body = msg.strip()
    else:
        body = render_segments(msg, names)
    if not body:
        return ""
    raw_label = str(sender_card or "").strip() or str(sender_nickname or "").strip()
    who = pl.resolve_speaker(str(sender_id or "").strip(), raw_label, names)
    return pl.label_turn(who, body)
