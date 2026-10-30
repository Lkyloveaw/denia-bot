r"""检索词规划（`kb_logic`）的断言测试 —— 不 import nonebot，秒级。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_kb_logic.py

要保住的两侧行为，历史上互相打架：

- 「达妮娅，今天中午吃什么」**不能**因为「达妮娅」是知识库大词条就塞一堆她的设定（曾经的真实漏注）；
- 「达妮娅是谁」**必须**能塞 —— 主语就是她，剥掉唤醒词等于把问题扔了。

分界靠两条信号：唤醒词后面**带标点/空格** = 称呼，剥；
**紧贴**且剩下的只是一小截谓词（是谁/厉害吗/几岁…）= 那句在问她本人，保留原文。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kb_logic as kl  # noqa: E402

NICKS = ("达妮娅", "小娅", "娅")
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


def plan(text: str) -> list[str]:
    return kl.plan_queries(text, NICKS)


# ---------------------------------------------------------------- 唤醒词


def t_plain_question_is_untouched():
    assert plan("今汐是谁") == ["今汐是谁"]
    assert plan("深塔怎么打") == ["深塔怎么打"]


def t_vocative_before_punctuation_is_stripped():
    """带标点/空格的呼叫 = 称呼，剥掉后再检索 —— 这条就是当年那个真实漏注。"""
    assert plan("达妮娅，今天中午吃什么") == ["今天中午吃什么"]
    assert plan("达妮娅 深塔怎么打") == ["深塔怎么打"]
    assert plan("小娅，今天中午吃什么") == ["今天中午吃什么"]


def t_glued_full_question_is_still_a_vocative():
    """不带标点但剩下的是个完整问句：仍然当称呼，别把她的设定塞进「吃什么」。"""
    assert plan("达妮娅今天中午吃什么") == ["今天中午吃什么"]


def t_glued_bare_predicate_keeps_the_subject():
    """紧贴 + 剩下只是一截谓词 → 问的就是她本人，原文整句拿去检索。"""
    for text in ("达妮娅是谁", "达妮娅厉害吗", "达妮娅几岁", "达妮娅怎么样", "达妮娅强不强"):
        assert plan(text) == [text], f"「{text}」被剥成了残句"


def t_bare_calling_retrieves_nothing():
    assert plan("达妮娅") == [], "光叫人，没问题可查"
    assert plan("娅") == []
    assert plan("达妮娅，") == []


def t_predicate_test_is_narrow():
    """剩下的是长句就别当谓词，否则「有好东西推荐吗」这种会被误当成在问她。"""
    assert plan("达妮娅有好东西推荐吗") == ["有好东西推荐吗"]
    assert plan("达妮娅是谁呀") == ["达妮娅是谁呀"], "短谓词该保住主语"


# ---------------------------------------------------------------- @ 前缀


def t_at_prefix_is_always_a_vocative():
    assert plan("@达妮娅 深塔怎么打") == ["深塔怎么打"]
    assert plan("[at:qq=3892894069]今汐是谁") == ["今汐是谁"]
    assert plan("@达妮娅") == []


def t_strip_at_only_removes_the_leading_call():
    assert kl.strip_at("在吗 @达妮娅") == "在吗 @达妮娅", "句尾的 @ 不动它"


# ------------------------------------------------- 群里那句前面还有 `<昵称> `
#
# MuiceBot 在群里把发言人的昵称拼进 prompt（`muice.py:174`：`f"<{username}> {message}"`），
# 所以钩子拿到的是「<对乙酰氨基酚> 达妮娅，今天中午吃什么」这种形状。
# 真实日志（nonebot/logs/2026-09-27.log 14:46）里就是这么漏的：
#   [kb] 注入 2 块 / 1258 字，检索词「<对乙酰氨基酚> 达妮娅，在吗」命中词条「达妮娅」
# 更糟的是昵称自己能当词条（15:25 那条 `<芝士小特，喜欢用表情包> 你是水蜜桃派吗` 命中了「表情包」），
# 所以这层前缀既挡了剥离，自己还会开门。


def t_speaker_tag_is_stripped():
    assert plan("<对乙酰氨基酚> 达妮娅，今天中午吃什么") == ["今天中午吃什么"]
    assert plan("<对乙酰氨基酚> 深塔怎么打") == ["深塔怎么打"]


def t_speaker_tag_does_not_hide_the_subject():
    assert plan("<对乙酰氨基酚> 达妮娅是谁") == ["达妮娅是谁"]


def t_speaker_tag_never_becomes_the_query():
    """昵称是随机的，拿它去检索等于让陌生人的网名决定塞什么资料。"""
    assert plan("<芝士小特，喜欢用表情包> 你是水蜜桃派吗") == ["你是水蜜桃派吗"]


def t_at_and_speaker_tag_together():
    assert plan("[at:qq=3892894069] <甲> 达妮娅，深塔怎么打") == ["深塔怎么打"]


def t_speaker_tag_is_only_stripped_at_the_front():
    assert kl.strip_at("看看这个 <甲> 说的") == "看看这个 <甲> 说的"


def t_empty_speaker_tag_is_stripped_too():
    """没昵称的人在群里会被拼成光秃秃的 `<> ` —— 线上真实见过，一样得剥。

    2026-09-27 15:47 的日志：`检索词「<> 达妮娅，今天中午吃什么」命中词条「达妮娅」`
    """
    assert plan("<> 达妮娅，今天中午吃什么") == ["今天中午吃什么"]
    assert plan("<> 达妮娅是谁") == ["达妮娅是谁"]


def t_repeated_calling_is_stripped_all_the_way():
    """叠着叫人的那句也是线上真实消息：只剥一个「娅」会留下个残称呼当检索词。

    2026-09-27 15:52 的日志：`检索词「娅，你在虚质空间还好吗？」`，原话是「娅娅，你在虚质空间还好吗？」
    """
    assert plan("娅娅，你在虚质空间还好吗？") == ["你在虚质空间还好吗？"]
    assert plan("达妮娅达妮娅，深塔怎么打") == ["深塔怎么打"]
    assert plan("小娅娅，在吗") == ["在吗"]


def t_guard_label_is_stripped_too():
    """`prompt_guard`（priority=1，跑在 kb 前面）会把 `<昵称> ` 改写成 `〔发言人:某某〕\n`。

    两边的格式是个隐式约定：它改写出的新署名 kb 要还认得，否则署名又会挤到唤醒词前面，
    「先剥称呼」整套判断当场失效（就是 §11.4 记的那次漏注）。
    """
    assert plan("〔发言人:漂泊者〕\n达妮娅，今天中午吃什么") == ["今天中午吃什么"]
    assert plan("〔发言人:漂泊者〕\n达妮娅是谁") == ["达妮娅是谁"]
    assert plan("〔发言人:漂泊者〕\n深塔怎么打") == ["深塔怎么打"]


# ---------------------------------------------------------------- 最短长度


def t_too_short_remainder_is_dropped():
    assert plan("达妮娅，嗯") == [], "剥完只剩一个语气词，别去烦检索服务"


# ---------------------------------------------------------------- 诊断（A）


def t_diagnostic_query_is_the_raw_text():
    """/kb 查 是「这句话会不会命中」，不是「聊天时会拿什么去查」—— 所以要用原文。"""
    assert kl.diagnostic_query("达妮娅") == "达妮娅"
    assert kl.diagnostic_query("达妮娅，今天中午吃什么") == "达妮娅，今天中午吃什么"


def t_diagnostic_reports_too_short_instead_of_no_hit():
    assert kl.diagnostic_query("嗯") == ""
    assert kl.diagnostic_query("  ") == ""


# ---------------------------------------------------------------- /kb 查 的参数


def t_probe_keyword_extracts_the_raw_query():
    assert kl.parse_probe_command("查 达妮娅") == "达妮娅"
    assert kl.parse_probe_command("查达妮娅") == "达妮娅"
    assert kl.parse_probe_command("test 今汐是谁") == "今汐是谁"
    assert kl.parse_probe_command("TEST EMA怎么用") == "EMA怎么用", "别把大小写改了"


def t_probe_keyword_only_for_that_subcommand():
    assert kl.parse_probe_command("on") is None
    assert kl.parse_probe_command("查") == "", "缺参数也要认出来，好回用法"
    assert kl.parse_probe_command("") is None


# ---------------------------------------------------------------- 依次试查


def t_pick_hits_stops_at_the_first_query_that_hits():
    calls = []

    async def search(q):
        calls.append(q)
        return ([{"text": "深塔"}], {"entries": ["逆境深塔"]}) if q == "深塔怎么打" else ([], {})

    q, hits, stats = asyncio.run(kl.pick_hits(["达妮娅深塔怎么打", "深塔怎么打"], search))
    assert q == "深塔怎么打" and len(hits) == 1, "第一个命中的就该收手"
    assert calls == ["达妮娅深塔怎么打", "深塔怎么打"], "顺序不能反，也别多问"


def t_pick_hits_reports_last_attempt_when_nothing_hits():
    async def search(q):
        return ([], {"entries": []})

    q, hits, stats = asyncio.run(kl.pick_hits(["今天中午吃什么", "吃什么"], search))
    assert hits == [] and q == "吃什么", "都没命中也要说清查过哪一句"


def t_pick_hits_keeps_stats_of_the_last_attempt():
    """全都没命中时，服务自己那句话（词条闸门命中没有）要留给诊断用，别吞成 {}。"""
    async def search(q):
        return ([], {"entries": None if q.startswith("今天") else ["达妮娅"]})

    q, hits, stats = asyncio.run(kl.pick_hits(["今天中午吃什么", "吃什么"], search))
    assert stats == {"entries": ["达妮娅"]}, f"该回最后一次检索的服务统计，实际 {stats}"


def t_pick_hits_accepts_a_sync_search_too():
    """插件里是 async，测试里想拿普通函数当假检索 —— 两种都吃得下才算这条策略好测。"""
    q, hits, stats = asyncio.run(kl.pick_hits(["深塔"], lambda s: ([{"x": 1}], {})))
    assert q == "深塔" and hits == [{"x": 1}]


def t_pick_hits_with_nothing_to_ask():
    assert asyncio.run(kl.pick_hits([], lambda q: ([{"x": 1}], {}))) == ("", [], {})


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
