r"""群聊署名规范化（`prompt_logic`）的断言测试 —— 不 import nonebot，秒级。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_prompt_logic.py

要保住的是 2026-09-27 那次「答非所问」的三条根因（详见 `prompt_logic.py` 开头）：

1. `<对乙酰氨基酚> 娅娅` —— 主人的 QQ 昵称正好是药名，模型把它当成了话题；
2. 同一条历史出现两遍（一份原文、一份带 `<署名>`），`list(set(...))` 一条都没并不掉；
3. 顺序被打乱 + 别的群/别的私聊的对话漏进本群。

分工：这边管「该怎么切、怎么排」，`test_prompt_wiring.py` 管「插件真的照那么做了吗」。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kb_logic as kl  # noqa: E402
import prompt_logic as pl  # noqa: E402

FAILED: list[str] = []
KNOWN = {"10000001": "漂泊者"}
GRP = "qq_OneBot V11_3892894069_922192732"
OLD = "qq_OneBot V11_3892894069_1108838060"


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


def turn(t, uid="1708420892", gid=GRP, msg="晚饭吃什么", resp="随便吃点热的吧。"):
    return pl.Turn(time=t, userid=uid, groupid=gid, body=msg, respond=resp)


# ---------------------------------------------------------------- 拆署名


def t_split_angle_bracket_label():
    assert pl.split_speaker("<对乙酰氨基酚> 娅娅") == ("对乙酰氨基酚", "娅娅")


def t_split_new_label_with_newline():
    assert pl.split_speaker("〔发言人:漂泊者〕\n娅娅") == ("漂泊者", "娅娅")


def t_split_empty_label():
    """线上真出现过 3 次拿不到名片的 `<> 正文`：不能因为它剥不掉就把 `<>` 当检索词。"""
    assert pl.split_speaker("<> 在吗") == ("", "在吗")


def t_split_leaves_mid_sentence_brackets_alone():
    """句中的尖括号是人家在说东西，不是署名。"""
    s = "她说 <达妮娅> 挺可爱的"
    assert pl.split_speaker(s) == ("", s)


def t_split_stacked_labels_take_the_first():
    lab, body = pl.split_speaker("<a> <b> 你好")
    assert lab == "a" and body == "你好", f"{lab!r} / {body!r}"


def t_label_turn_round_trips():
    assert pl.split_speaker(pl.label_turn("穿梭机", "你搁哪里发财呢")) == ("穿梭机", "你搁哪里发财呢")


def t_label_turn_without_label():
    assert pl.label_turn("", "晚安") == "晚安"


# ---------------------------------------------------------------- 定称呼


def t_known_name_beats_qq_nickname():
    assert pl.resolve_speaker("10000001", "对乙酰氨基酚", KNOWN) == "漂泊者"


def t_unknown_person_keeps_their_card():
    assert pl.resolve_speaker("982272752", "穿梭机", KNOWN) == "穿梭机"


def t_noise_labels_become_groupfriend_with_qq():
    """空名片、纯数字（userinfo 拿不到名字时回落到 QQ 号）都不该原样进提示词。"""
    assert pl.resolve_speaker("999", "", KNOWN) == "群友999"
    assert pl.resolve_speaker("999", "999", KNOWN) == "群友999"


def t_no_userid_no_label():
    assert pl.resolve_speaker("", "", KNOWN) == ""


def t_speaker_map_takes_models_and_dicts():
    class Row:
        def __init__(self, i, n):
            self.id, self.name = i, n

    m = pl.speaker_map([Row("1", "甲"), {"id": "2", "name": "乙"}, {"id": "", "name": "丙"}])
    assert m == {"1": "甲", "2": "乙"}, m


# ---------------------------------------------------------------- 整理历史


def t_twins_collapse_into_one():
    """同一轮的两份（一份原文、一份带 `<署名>`）必须并成一条 —— 事故里的 4 条变 3 条就是这么来的。"""
    a = turn("2026.09.27 15:20:46", msg="娅娅")
    b = turn("2026.09.27 15:20:46", msg="<对乙酰氨基酚> 娅娅")
    out = pl.normalize_turns([a, b])
    assert len(out) == 1 and out[0].body == "娅娅", out


def t_history_sorted_by_time_not_by_hash():
    rows = [
        turn("2026.09.27 15:21:42", msg="你搁哪里发财呢"),
        turn("2026.09.27 15:20:46", msg="娅娅"),
        turn("2026.09.27 15:21:39", msg="达妮娅是谁呀"),
    ]
    got = [t.time for t in pl.normalize_turns(rows)]
    assert got == sorted(got), got


def t_unparsable_time_sorts_first_not_crashing():
    rows = [turn("乱码", msg="早"), turn("2026.09.27 15:20:46", msg="晚")]
    out = pl.normalize_turns(rows)
    assert [t.body for t in out] == ["早", "晚"], out


def t_other_groups_and_privates_are_dropped_in_a_group():
    rows = [
        turn("2026.09.27 15:13:38", gid=OLD, msg="娅娅今天西格莉卡找你了吗"),
        turn("2026.09.27 15:20:46", gid="-1", msg="达妮娅 在干嘛呢"),
        turn("2026.09.27 15:22:34", msg="晚饭吃什么"),
    ]
    out = pl.normalize_turns(rows, current_group="922192732")
    assert [t.body for t in out] == ["晚饭吃什么"], out


def t_private_scope_keeps_only_privates():
    rows = [turn("2026.09.27 15:22:34", msg="群里"), turn("2026.09.27 15:23:34", gid="-1", msg="私聊")]
    out = pl.normalize_turns(rows, current_group="-1")
    assert [t.body for t in out] == ["私聊"], out


def t_unknown_scope_keeps_everything():
    """认不出当前场景就别筛 —— 宁可多带历史，也不能把人家的上下文清空。"""
    rows = [turn("2026.09.27 15:22:34", msg="群里"), turn("2026.09.27 15:23:34", gid="-1", msg="私聊")]
    assert len(pl.normalize_turns(rows, current_group=None)) == 2


def t_limit_keeps_the_newest():
    rows = [turn(f"2026.09.27 15:2{ i }:00", msg=f"第{i}句") for i in range(5)]
    out = pl.normalize_turns(rows, limit=2)
    assert [t.body for t in out] == ["第3句", "第4句"], out


def t_limit_zero_means_unlimited():
    rows = [turn(f"2026.09.27 15:2{i}:00", msg=f"第{i}句") for i in range(5)]
    assert len(pl.normalize_turns(rows, limit=0)) == 5


def t_labels_resolved_for_every_row():
    rows = [
        turn("2026.09.27 15:20:46", uid="10000001", msg="<对乙酰氨基酚> 娅娅"),
        turn("2026.09.27 15:21:42", uid="982272752", msg="<穿梭机> 你搁哪里发财呢"),
    ]
    out = pl.normalize_turns(rows, current_group="922192732", known=KNOWN)
    assert [t.label for t in out] == ["漂泊者", "穿梭机"], out


# ---------------------------------------------------------------- 会话 id / 场景


def t_group_token_extracts_the_group_number():
    assert pl.group_token(GRP) == "922192732"
    assert pl.group_token("-1") == "-1"
    assert pl.group_token("") == ""


def t_parse_time_shape():
    assert pl.parse_time("2026.09.27 15:20:46") == datetime(2026, 9, 27, 15, 20, 46)
    assert pl.parse_time("x") is None and pl.parse_time("") is None


# ------------------------------------------------- 与检索层（kb_logic）的对接契约


def t_kb_asks_the_clean_question_after_labeling():
    """新署名格式必须被 kb 的剥离器吃掉，否则「达妮娅」又会把知识闸门顶开（本次真实事故）。"""
    p = pl.label_turn("漂泊者", "达妮娅，在吗")
    assert kl.plan_queries(p, ("达妮娅", "小娅", "娅")) == ["在吗"], kl.plan_queries(p, ("达妮娅",))


def t_kb_still_keeps_the_subject_when_asking_about_her():
    p = pl.label_turn("漂泊者", "达妮娅是谁呀")
    assert kl.plan_queries(p, ("达妮娅", "小娅", "娅")) == ["达妮娅是谁呀"]


def t_kb_has_nothing_to_ask_for_a_bare_call():
    assert kl.plan_queries(pl.label_turn("漂泊者", "娅娅"), ("达妮娅", "小娅", "娅")) == []


def t_kb_still_strips_the_legacy_angle_label():
    """上游哪天不经过 guard（比如开关关了）时，老格式也别再把闸门顶开。"""
    assert kl.plan_queries("<对乙酰氨基酚> 达妮娅，在吗", ("达妮娅",)) == ["在吗"]


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
