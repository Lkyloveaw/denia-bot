r"""检索词规划 —— 「拿什么话去问知识库」这件事的纯逻辑。

不 import nonebot / muicebot，所以 `tests/test_kb_logic.py` 秒级能跑完。
`plugins/kb.py` 只负责把它接到检索服务上。

为什么要有这个文件：唤醒词「达妮娅」本身是知识库里的大词条（124 个候选块），
于是「叫她一声」和「问她本人」这两种消息，会被同一个检索词搞混。历史上为了挡前者，
写过一版「无脑剥掉句首昵称」，结果把「达妮娅是谁」剥成「是谁」—— 主语丢了，永远不命中。

分界用两条信号：

1. 唤醒词后面**带标点或空格**（`达妮娅，…`）→ 是称呼，剥掉后再检索；
2. **紧贴**（`达妮娅是谁`）→ 看剩下那一截：只够得上一小截谓词（是谁 / 厉害吗 / 几岁…）
   就认定这句在问她本人，**整句原样拿去检索**；否则（`达妮娅今天中午吃什么`）仍当称呼剥掉。

第 2 条里的谓词表是刻意的窄判据：宁可漏一句「关于她」的注入，也不要把她的设定
塞进一句无关的闲聊 —— 那是这套闸门的设计前提。
"""

from __future__ import annotations

import inspect
import re
from typing import Iterable

MIN_QUERY = 2
"""短于这个长度的检索词不值得问：BM25 里没有词，向量里全是噪声。"""

# 呼叫前缀/唤醒词后面允许出现的分隔符
CALL_PUNCT = " \t，,、。.!！?？~～…：:"

# 剩下这一截像是「问她本人」的谓语开头
PREDICATE_HEADS = set("是谁啥怎咋几多强厉好哪有")
PREDICATE_MAX = 6
"""谓词判定的长度上限：超过它就不是残句而是一个完整问句（「有好东西推荐吗」）。"""

_AT_PREFIX = re.compile(
    r"^(?:\[at[:：][^\]]*\]|@[\w.\-一-鿿]+|<[^<>\n]{0,40}>"
    r"|〔发言人:[^\n〕]{0,40}〕)"  # 〔发言人:…〕= prompt_guard 改写后的署名（认这一种就够，别剥句中的）
    + r"[ \t\r\n，,、。.!！?？~～]*",
    re.I,
)


def strip_at(text: str) -> str:
    """吃掉句首的呼叫/署名前缀，一次剥不干净就接着剥。

    四种：`@达妮娅`、`[at:qq=…]`（群里 @ 她）、MuiceBot 在群里拼进 prompt 的
    `<发言人昵称> `（`muice.py:174`），以及 `plugins/prompt_guard.py` 改写后的
    `〔发言人:某某〕`（它排在 kb 之前，所以这里吃到的是新格式）。
    署名最容易忽略，也最要命 ——
    昵称是随便起的，「<芝士小特，喜欢用表情包> …」光顶着它去查就能命中「表情包」词条，
    还把后面的唤醒词挤到了句中，让「先剥称呼」的判断整个失效。
    只剥句首：句中出现的 `<...>` 是人家在说东西，不是署名。
    """
    s = (text or "").strip()
    while True:
        nxt = _AT_PREFIX.sub("", s, count=1).strip()
        if nxt == s:
            return s
        s = nxt


def looks_like_bare_predicate(rest: str) -> bool:
    """剥掉唤醒词后剩下的那一截，是否只够构成一个谓语（「是谁」「厉害吗」）。"""
    s = rest.strip()
    return bool(s) and len(s) <= PREDICATE_MAX and s[0] in PREDICATE_HEADS


def plan_queries(text: str, nicknames: Iterable[str] = ()) -> list[str]:
    """聊天注入路径要按顺序试的检索词。空列表 = 没问题可查，别去烦检索服务。

    循环剥：线上真有人叠着叫人（「娅娅，你在虚质空间还好吗？」），
    只剥一层会留下个残称呼当检索词。每轮都在变小，不会转圈。
    """
    s = strip_at(text)
    if not s:
        return []
    names = sorted(nicknames, key=len, reverse=True)  # 长名字优先：达妮娅 > 娅

    while True:
        for nick in names:
            if not nick or not s.startswith(nick):
                continue
            rest = s[len(nick):]
            if not rest:
                return []  # 光叫人，没问题可查
            separated = rest[0] in CALL_PUNCT
            remainder = rest.lstrip(CALL_PUNCT).strip()
            if not separated and looks_like_bare_predicate(remainder):
                return [s]  # 「达妮娅是谁」：主语就是唤醒词，整句拿去查
            s = remainder  # 可能还在叫人，接着剥
            break
        else:
            return [s] if len(s) >= MIN_QUERY else []


def diagnostic_query(text: str) -> str:
    """`/kb 查 <问题>` 用的检索词：**原文**，只去掉句首的 @ 呼叫。

    这条指令的语义是「这句话会不会命中」，不是「聊天时会拿什么去查」，
    所以不能套用剥离 —— 否则「查 达妮娅」会被剥成空串，报出个假「未命中」。
    返回空串表示**根本没发起检索**（太短），调用方要照实说，别混进「没命中」。
    """
    s = strip_at(text)
    return s if len(s) >= MIN_QUERY else ""


_PROBE_KEYWORDS = ("查", "test")


def parse_probe_command(arg: str) -> str | None:
    """从 `/kb` 的参数里取出试检索的问题：`查 达妮娅` / `test 达妮娅` → `"达妮娅"`。

    不是试检索子命令返回 None（`on` / `off` / 空）；认出来但没带问题返回 `""`，
    让调用方回用法。问题**原样返回**：不改大小写（`EMA` 不是 `ema`）、不剥唤醒词。
    """
    s = (arg or "").strip()
    if not s:
        return None
    low = s.lower()
    for kw in _PROBE_KEYWORDS:
        if low.startswith(kw):
            return s[len(kw):].strip()
    return None


async def pick_hits(plans: list[str], search):
    """按顺序拿候选检索词去问服务，**第一个命中的就用它**。

    `search(query) -> (hits, stats)` 由调用方注入，这样这条策略能脱离检索服务测。
    异步普通两版都收：插件里传协程函数，测试里传普通 lambda。
    返回 `(实际用出去的那句, hits, stats)` —— 全都没命中时也告诉调用方最后问的是哪句、
    服务当时怎么答的，否则日志里分不清「没查」和「查了没命中」（就是这次踩的那个坑）。
    """
    last, last_stats = "", {}
    for q in plans:
        res = search(q)
        if inspect.isawaitable(res):
            res = await res
        hits, stats = res
        if hits:
            return q, hits, stats
        last, last_stats = q, stats
    return (last, [], last_stats)

