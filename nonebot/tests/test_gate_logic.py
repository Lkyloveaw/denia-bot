r"""`gate_logic` 的断言测试 —— 不 import nonebot、不碰 QQ，直接跑：

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_gate_logic.py

为什么只测这一层：闸门真正的触发/抑制要整条链跑起来才能验（用 tools\自检全链路.py），
但「上限怎么取值、什么时候清零、指令怎么解析」这些是会算错的地方，
错一次就是机器人在群里装死或者被刷屏，所以必须先有失败可看。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gate_logic import (  # noqa: E402
    BARE_HELP_HEADS,
    EXTRA_HELP_ROWS,
    GATE_HEADS,
    HELP_GROUPS,
    HELP_HEADS,
    HELP_ROWS,
    UNLIMITED,
    all_help_rows,
    apply_command,
    auto_at_index,
    bump,
    check,
    decide,
    describe,
    grouped_help_rows,
    help_footer,
    load_state,
    mentions_self,
    new_state,
    parse_gate_command,
    register_help_rows,
    render_help,
    resolve_cap,
    save_state,
)

TODAY = "2026-09-27"
G, U = "741096253", "123456"
OTHER_GROUP, OTHER_USER = "998877", "123456"

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


def state(**over):
    s = new_state(TODAY, default_cap=20)
    s.update(over)
    return s


# ---------------------------------------------------------------- 上限取值


def t_cap_prefers_member():
    s = state()
    s["caps"]["group"][G] = 5
    s["caps"]["member"][f"{G}|{U}"] = 2
    assert resolve_cap(s, G, U) == 2, "成员上限没盖过群上限"


def t_cap_falls_back_group_then_default():
    s = state()
    s["caps"]["group"][G] = 5
    assert resolve_cap(s, G, U) == 5, "没有成员上限时应退回群上限"
    assert resolve_cap(s, OTHER_GROUP, U) == 20, "群也没设时应退回默认"


def t_unlimited_means_zero_and_beats_narrower_scope():
    s = state()
    s["caps"]["group"][G] = 5
    s["caps"]["member"][f"{G}|{U}"] = UNLIMITED
    assert resolve_cap(s, G, U) == UNLIMITED, "成员设成不限应盖过群上限"


# ---------------------------------------------------------------- 放行判定


def t_check_blocks_at_cap():
    s = state()
    s["caps"]["member"][f"{G}|{U}"] = 2
    assert check(s, G, U)[0] is True, "0 次时应放行"
    bump(s, G, U)
    assert check(s, G, U)[0] is True, "1/2 次时应放行"
    bump(s, G, U)
    assert check(s, G, U)[0] is False, "已到 2/2 就该拦"
    assert check(s, G, U)[1:] == (2, 2), "应回报用量与上限"


def t_check_never_blocks_unlimited():
    s = state()
    s["caps"]["member"][f"{G}|{U}"] = UNLIMITED
    for _ in range(50):
        bump(s, G, U)
    assert check(s, G, U)[0] is True, "不限的人不该被拦"
    assert check(s, G, U)[1] == 50, "不限时仍要记用量"


def t_counts_are_per_group_and_per_user():
    s = state()
    bump(s, G, U)
    bump(s, G, "777")
    bump(s, OTHER_GROUP, U)
    assert check(s, G, U)[1] == 1
    assert check(s, OTHER_GROUP, "777")[1] == 0, "计数串了群或串了人"


# ---------------------------------------------------------------- 存储与日切


def t_save_then_load_roundtrip():
    s = state()
    bump(s, G, U)
    s["caps"]["group"][G] = 3
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "gate.json"
        save_state(p, s)
        back = load_state(p, TODAY, default_cap=20)
        assert back["counts"] == {f"{G}|{U}": 1}, back["counts"]
        assert back["caps"]["group"] == {G: 3}
        assert not list(p.parent.glob("*.tmp")), "原子写留下了临时文件"


def t_load_rolls_over_the_day():
    p = None
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "gate.json"
        s = new_state("2026-01-01", default_cap=20)
        bump(s, G, U)
        s["caps"]["member"][f"{G}|{U}"] = 7
        save_state(p, s)
        back = load_state(p, TODAY, default_cap=20)
        assert back["day"] == TODAY, "日期应推进到今天"
        assert back["counts"] == {}, "跨天必须清零计数"
        assert back["caps"]["member"][f"{G}|{U}"] == 7, "上限设置不能跟着日切丢"


def t_load_survives_missing_and_corrupt_file():
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "nope.json"
        assert load_state(missing, TODAY, default_cap=20)["counts"] == {}, "缺文件应给干净初始态"
        broken = Path(d) / "gate.json"
        broken.write_text("{ 这不是 json", encoding="utf-8")
        s = load_state(broken, TODAY, default_cap=20)
        assert s["caps"]["default"] == 20, "坏文件应回落默认而不是抛异常"
        weird = Path(d) / "weird.json"
        weird.write_text(json.dumps({"day": TODAY, "counts": {"x|y": "好多"}}), encoding="utf-8")
        s2 = load_state(weird, TODAY, default_cap=20)
        assert s2["counts"] == {}, "非数字计数要丢掉，不能带进判定"


# ---------------------------------------------------------------- 指令解析


def cmd(*args, s=None):
    """跑一条指令，返回 (状态, 改动了吗, 回复)。"""
    s = s if s is not None else state()
    changed, reply = apply_command(s, list(args))
    return s, changed, reply


def t_cmd_sets_member_cap():
    s, changed, reply = cmd("上限", G, U, "5")
    assert changed and s["caps"]["member"][f"{G}|{U}"] == 5
    assert "5" in reply, reply


def t_cmd_accepts_unlimited_words():
    for word in ("不限", "无限", "0"):
        s, changed, _ = cmd("默认", word)
        assert changed and s["caps"]["default"] == UNLIMITED, f"「{word}」没被当成不限"


def t_cmd_group_and_default():
    s, _, _ = cmd("群上限", G, "3")
    assert s["caps"]["group"][G] == 3
    s, _, _ = cmd("默认", "40", s=s)
    assert s["caps"]["default"] == 40
    assert resolve_cap(s, G, U) == 3, "群上限仍应优先于默认"


def t_cmd_rejects_bad_numbers_with_usage():
    for bad in ("abc", "-3", "0次", ""):
        s, changed, reply = cmd("默认", bad)
        assert not changed, f"「{bad}」不该被接受"
        assert "用法" in reply, f"非法输入要给用法：{reply}"


def t_cmd_missing_argument_gives_usage():
    s, changed, reply = cmd("上限", G)
    assert not changed and "用法" in reply, "缺参数要给用法"


def t_cmd_unknown_subcommand():
    s, changed, reply = cmd("瞎说")
    assert not changed and "瞎说" in reply, "不认识的子指令要回执并说明"


def t_cmd_toggles_at_only():
    s, changed, _ = cmd("群内只@", "关")
    assert changed and s["group_at_only"] is False
    s, changed, _ = cmd("群内只@", "开", s=s)
    assert changed and s["group_at_only"] is True
    s, changed, _ = cmd("群内只@", "随便", s=s)
    assert not changed, "开关只认开/关"


def t_cmd_reset_scopes():
    s = state()
    bump(s, G, U)
    bump(s, G, "777")
    bump(s, OTHER_GROUP, U)
    cmd("清零", G, U, s=s)
    assert check(s, G, U)[1] == 0 and check(s, G, "777")[1] == 1, "只该清掉指定的人"
    cmd("清零", G, s=s)
    assert check(s, G, "777")[1] == 0 and check(s, OTHER_GROUP, U)[1] == 1, "只该清掉指定群"
    cmd("清零", s=s)
    assert s["counts"] == {}, "不带参数是全清"


def t_describe_reports_usage_and_cap():
    s = state()
    bump(s, G, U)
    s["caps"]["member"][f"{G}|{U}"] = 3
    out = describe(s, group=G)
    assert U in out and "1/3" in out, out
    assert "未超限" in out, out
    assert "20" in describe(state(), group=None), "总览要给出默认上限"



# ---------------------------------------------------------------- @ 判定


def t_mentions_self_only_counts_at_of_self():
    assert mentions_self([("at", G), ("text", "hi")], G) is True, "开头 @我 要算"
    assert mentions_self([("text", "喂 "), ("at", G), ("text", "你看")], G) is True, "@ 在中间也要算"
    assert mentions_self([("at", "1597516006"), ("text", "hi")], G) is False, "@ 别人不算"
    assert mentions_self([("text", "达妮娅你看")], G) is False, "光打字说名字不算"
    assert mentions_self([("at", "all")], G) is False, "@全员不算"


# ---------------------------------------------------------------- 引用自动补的 @
#
# QQ 的「回复」会在正文最前面自动塞一个 @被引用的人，肉眼看着像 @ 了她，
# 其实主人只是顺手回了她上一句。真机段结构（logs/nonebot.out.log:59/:134/:210）：
#   回复她 / 回复别人 → [reply:id][at:qq=被引用者]正文
#   回复自己发的那条 → [reply:id]正文                （QQ 不给自己补 @）


def t_auto_at_is_the_one_sitting_right_after_the_reply():
    assert auto_at_index([("reply", "55"), ("at", G), ("text", "在吗")], ) == 1
    assert auto_at_index([("reply", "55"), ("text", "好叭"), ("at", G)]) is None, "隔开了就不是自动补的"
    assert auto_at_index([("at", G), ("text", "在吗")]) is None, "没有 reply 段就谈不上自动"
    assert auto_at_index([]) is None and auto_at_index(None) is None


def t_auto_at_must_belong_to_the_quoted_author_when_known():
    """知道被引用的是谁时再卡一道：位置对、人也得对得上，免得误删人家手打的 @。"""
    segs = [("reply", "55"), ("at", "111"), ("text", "x")]
    assert auto_at_index(segs, reply_author="111") == 1
    assert auto_at_index(segs, reply_author="222") is None, "这个 @ 不是被引用的人，是手打的"
    assert auto_at_index(segs) == 1, "认不出被引用的人时，只按位置判"


def t_replying_to_your_own_message_carries_no_auto_at():
    """2026-09-27 17:46 那条真实事件：主人回复自己发的消息，整条一个 at 段都没有。"""
    segs = [("reply", "-1886103778"), ("text", "好叭 你好好休息")]
    assert auto_at_index(segs, reply_author="10000001") is None


def t_mentions_self_can_ignore_the_auto_at():
    segs = [("reply", "55"), ("at", G), ("text", "那你看今天")]
    assert mentions_self(segs, G) is True, "默认还是老口子：自动补的 @ 也算"
    assert mentions_self(segs, G, ignore_auto_at=True) is False, "认出手是自动补的之后就不该算"


def t_a_real_at_next_to_the_auto_one_still_counts():
    """回复她 + 又自己手动 @ 了一次 → 只吃掉第一个，第二个照算。"""
    segs = [("reply", "55"), ("at", G), ("text", "还有 "), ("at", G)]
    assert mentions_self(segs, G, ignore_auto_at=True) is True
    others = [("reply", "55"), ("at", "1597516006"), ("text", "他说的 "), ("at", G)]
    assert mentions_self(others, G, ignore_auto_at=True) is True, "回复别人再手动 @ 她，该回"


# ---------------------------------------------------------------- 放行决策


def t_decide_private_ignores_group_rules():
    s = state()
    s["caps"]["member"][f"{G}|{U}"] = 1
    for _ in range(5):
        bump(s, G, U)
    ok, reason = decide(s, private=True, at_self=False, group=G, user=U)
    assert ok is True, "私聊不受群规则影响"
    assert "私聊" in reason, reason


def t_decide_group_requires_at_when_switch_on():
    s = state()
    ok, reason = decide(s, private=False, at_self=False, group=G, user=U)
    assert ok is False and "@" in reason, f"群里没@ 应该拦，还得说明原因：{reason}"


def t_decide_reason_blames_the_auto_at_when_it_was_one():
    """日志要分得清「压根没@」和「只有引用自动补的@」—— 排查时这两句话差很远。"""
    s = state()
    ok, reason = decide(s, private=False, at_self=False, group=G, user=U, auto_at=True)
    assert ok is False, "只有自动补的 @ 一样拦"
    assert "引用" in reason, f"这条得说是引用带的 @：{reason}"
    assert "没 @" not in reason, f"别和真没@ 混成一句话：{reason}"


def t_decide_at_only_off_allows_plain_group_chat():
    s = state(group_at_only=False)
    ok, _ = decide(s, private=False, at_self=False, group=G, user=U)
    assert ok is True, "开关关了就该走原来的昵称前缀逻辑"


def t_decide_group_blocks_when_over_cap():
    s = state()
    s["caps"]["member"][f"{G}|{U}"] = 3
    for _ in range(3):
        bump(s, G, U)
    ok, reason = decide(s, private=False, at_self=True, group=G, user=U)
    assert ok is False, "3/3 已到顶"
    assert "3/3" in reason, f"要说清是几次到几次：{reason}"


def t_decide_at_matters_before_cap():
    s = state()
    s["caps"]["member"][f"{G}|{U}"] = 1
    bump(s, G, U)
    ok, reason = decide(s, private=False, at_self=False, group=G, user=U)
    assert ok is False and "@" in reason and "3/" not in reason, "既没@又超限时报没@"


def t_decide_exempt_skips_at_and_cap():
    s = state()
    s["caps"]["member"][f"{G}|{U}"] = 1
    bump(s, G, U)
    ok, reason = decide(s, private=False, at_self=True, group=G, user=U, exempt=True)
    assert ok is True, "主人豁免"
    assert "豁免" in reason, reason


def t_decide_allow_reason_reports_usage():
    s = state()
    bump(s, G, U)
    ok, reason = decide(s, private=False, at_self=True, group=G, user=U)
    assert ok is True and "1/20" in reason, f"放行也要能写进日志：{reason}"


# ---------------------------------------------------------------- 指令识别


def t_parse_command_bare_and_prefixed():
    for text in ("限流 查看", ".限流 查看", "/限流 查看", "限流  查看  "):
        assert parse_gate_command(text, {"限流"}) == ["限流", "查看"], f"{text} 没认出来"
    assert parse_gate_command("限流", {"限流"}) == ["限流"], "光一个词也要认"
    assert parse_gate_command(".限流 默认 5", {"限流"}) == ["限流", "默认", "5"]
    assert parse_gate_command("限流 上限 741096253 123456 3", {"限流"}) == [
        "限流", "上限", "741096253", "123456", "3"]


def t_parse_command_rejects_plain_chat():
    for text in ("今天天气不错", "达妮娅", "帮我限流一下", "群里谁在限流", "", "   "):
        assert parse_gate_command(text, {"限流"}) is None, f"「{text}」不该被当成指令"
    assert parse_gate_command("限流量很大", {"限流"}) is None, "关键词后面还得是边界"
    assert parse_gate_command("查一下天气", {"限流"}) is None


def t_parse_command_strips_wakeword_and_at():
    n = ("达妮娅", "小娅", "娅")
    assert parse_gate_command("达妮娅 限流 查看", {"限流"}, n) == ["限流", "查看"]
    assert parse_gate_command("小娅，限流 默认 不限", {"限流"}, n) == ["限流", "默认", "不限"]
    assert parse_gate_command("@达妮娅 限流 查看", {"限流"}, n) is None, "群里 @ 的呼叫前缀不归指令管"
    assert parse_gate_command("达妮娅你好", {"限流"}, n) is None


def t_parse_command_wakeword_then_prefix():
    """先叫人再打指令：「达妮娅，.help」里的点不该被当成标点吃掉，它是指令前缀。"""
    n = ("达妮娅", "小娅", "娅")
    assert parse_gate_command("达妮娅，.限流 查看", {"限流"}, n) == ["限流", "查看"]
    assert parse_gate_command("达妮娅，/限流默认1", {"限流"}, n) == ["限流", "默认", "1"]
    assert parse_gate_command("达妮娅，.help", {"help", "帮助"}, n, allow_bare={"帮助"}) == ["help"]
    assert parse_gate_command("达妮娅，help", {"help", "帮助"}, n, allow_bare={"帮助"}) is None, \
        "裸写的拉丁词还是不认，前缀必须真的写出来"
    assert parse_gate_command("达妮娅,限流 查看", {"限流"}, n) == ["限流", "查看"], "半角逗号也得吃掉"


def t_parse_command_head_must_be_known():
    assert parse_gate_command("帮助", {"限流"}) is None, "只认给定的关键词"
    assert parse_gate_command("帮助 一下", {"限流", "帮助"}) == ["帮助", "一下"]


def t_parse_command_without_spaces():
    """QQ 上打字不带空格是常态，「限流查看」也得认。"""
    h = {"限流"}
    assert parse_gate_command("限流查看", h) == ["限流", "查看"]
    assert parse_gate_command(".限流默认5", h) == ["限流", "默认", "5"]
    assert parse_gate_command("限流清零", h) == ["限流", "清零"]
    assert parse_gate_command("限流群上限 741096253 3", h) == ["限流", "群上限", "741096253", "3"]
    assert parse_gate_command("限流群内只@关", h) == ["限流", "群内只@", "关"]
    assert parse_gate_command("限流瞎写", h) is None, "子指令不认识就别当指令"
    assert parse_gate_command("限流量很大", h) is None


def t_parse_command_bare_only_for_listed_heads():
    """拉丁词要带前缀才认，免得群里有人喊「help me」就蹦出指令表。"""
    h = {"help", "帮助"}
    assert parse_gate_command("help me", h, allow_bare=set()) is None
    assert parse_gate_command(".help", h, allow_bare=set()) == ["help"]
    assert parse_gate_command("帮助", h, allow_bare={"帮助"}) == ["帮助"]
    assert parse_gate_command("help", h) == ["help"], "不传 allow_bare 时默认都认"


def t_help_command_uses_the_real_head_sets():
    """帮助指令线上用的就是这两个词表：拉丁词必须带前缀，中文词可以裸写。"""
    for word in ("help me", "can you help", "who helps"):
        assert parse_gate_command(word, HELP_HEADS, allow_bare=BARE_HELP_HEADS) is None, word
    assert parse_gate_command(".help", HELP_HEADS, allow_bare=BARE_HELP_HEADS) == ["help"]
    assert parse_gate_command("/help", HELP_HEADS, allow_bare=BARE_HELP_HEADS) == ["help"]
    assert parse_gate_command("帮助", HELP_HEADS, allow_bare=BARE_HELP_HEADS) == ["帮助"]
    assert parse_gate_command("指令", HELP_HEADS, allow_bare=BARE_HELP_HEADS) == ["指令"]


def t_parse_command_picked_up_by_apply_command():
    """识别出来的参数要能直接喂给 apply_command，别在这儿再一套词表。"""
    s = state()
    changed, reply = apply_command(s, parse_gate_command("限流默认5", GATE_HEADS)[1:])
    assert changed and s["caps"]["default"] == 5, reply
    changed, reply = apply_command(s, parse_gate_command("限流群内只@关", GATE_HEADS)[1:])
    assert changed and s["group_at_only"] is False, reply


# ---------------------------------------------------------------- 帮助列表


def t_render_help_includes_every_row():
    txt = render_help(state(), [("help", "看这份列表"), ("限流 查看", "看今天的用量")])
    for need in ("help", "限流 查看", "看这份列表", "看今天的用量"):
        assert need in txt, f"帮助里少了「{need}」：\n{txt}"


def t_render_help_shows_prefixes():
    txt = render_help(state(), [("kb", "x")])
    assert "." in txt.splitlines()[0] and "/" in txt.splitlines()[0], "得说清前怎么写"


def t_render_help_reports_current_gate_settings():
    txt = render_help(state(), [])
    assert "只回" in txt and "20" in txt, f"要带上当前的 @ 规则和上限：\n{txt}"
    s = state(group_at_only=False)
    assert "叫名字" in render_help(s, []), "关着的时候要说真话"
    s2 = state()
    s2["caps"]["default"] = UNLIMITED
    assert "不限" in render_help(s2, []), "上限是不限时别写 0 次"


def t_help_rows_cover_every_command():
    joined = " ".join(cmd for cmd, _ in HELP_ROWS)
    for need in ("help", "status", "reset", "kb", "限流", "whoami"):
        assert need in joined, f"指令表漏了「{need}」"
    owners = [c for c, d in HELP_ROWS if "主人" in d]
    assert any(c.startswith("限流") for c in owners), "限流是主人指令，得标出来"
    assert not any("主人" in d for c, d in HELP_ROWS if c.startswith("kb")), "kb 谁都能用"


def t_registered_rows_join_the_help_without_touching_the_builtin_table():
    """别的插件把自己的指令挂进来，不能靠改 `HELP_ROWS` 这份字面表（会被重复追加）。"""
    keep = list(EXTRA_HELP_ROWS)
    rows = [("模型 查看", "看首选/次选（仅主人）"), ("模型 首选 <条目名>", "换回话用的模型（仅主人）")]
    try:
        EXTRA_HELP_ROWS.clear()
        register_help_rows(rows)
        register_help_rows(rows)  # 重启失败/重复加载都会再注册一遍
        assert all_help_rows()[: len(HELP_ROWS)] == HELP_ROWS, "自带那张表一个字都不该变"
        assert all_help_rows()[len(HELP_ROWS):] == rows, f"注册两遍只出现一遍：{all_help_rows()}"
        txt = render_help(state(), all_help_rows())
        for cmd, desc in rows:
            assert txt.count(cmd) == 1, f"「{cmd}」在帮助里出现了 {txt.count(cmd)} 次：\n{txt}"
            assert desc in txt
    finally:
        EXTRA_HELP_ROWS[:] = keep


def t_register_help_rows_replaces_a_row_with_the_same_command():
    """条目名相同就是同一条指令 —— 描述改了要看得见，不能屏幕上留两行。"""
    keep = list(EXTRA_HELP_ROWS)
    try:
        EXTRA_HELP_ROWS.clear()
        register_help_rows([("模型 查看", "旧说明")])
        register_help_rows([("模型 查看", "新说明：含次选")])
        assert [c for c, _ in all_help_rows() if c == "模型 查看"] == ["模型 查看"]
        assert "新说明" in render_help(state(), all_help_rows())
        assert "旧说明" not in render_help(state(), all_help_rows())
    finally:
        EXTRA_HELP_ROWS[:] = keep


def t_the_builtin_help_never_lands_in_the_registry():
    """注册表只装外来的；把 HELP_ROWS 自己塞进去会让每条指令都出现两遍。"""
    keep = list(EXTRA_HELP_ROWS)
    try:
        EXTRA_HELP_ROWS.clear()
        register_help_rows(HELP_ROWS)
        assert EXTRA_HELP_ROWS == [], f"自带指令不该进注册表：{EXTRA_HELP_ROWS}"
    finally:
        EXTRA_HELP_ROWS[:] = keep


def t_help_groups_keep_every_row_once():
    """分组不能吞掉任何一条：新插件注册的指令必须落到「其它」组里出现。"""
    rows = all_help_rows() + [("天气 北京", "查天气"), ("某个新指令", "新来的")]
    groups = grouped_help_rows(rows)
    flat = [cmd for _, items in groups for cmd, _ in items]
    assert sorted(flat) == sorted(cmd for cmd, _ in rows), "分组后有指令丢失或重复"
    assert "其它" in [name for name, _ in groups], "未知指令应归到「其它」"


def t_help_group_matches_prefixed_commands():
    """`限流 默认 <次数>` 这类带参数的全称也要归到限流组。"""
    groups = dict(grouped_help_rows(all_help_rows()))
    limit_cmds = [cmd for cmd, _ in groups.get("限流", [])]
    assert any(cmd.startswith("限流 默认") for cmd in limit_cmds), limit_cmds
    assert all(cmd.startswith("限流") for cmd in limit_cmds), "限流组混进了别的指令"


def t_help_group_order_follows_declaration():
    """组顺序恒为 HELP_GROUPS 的声明顺序，与传入行的顺序无关。"""
    groups = grouped_help_rows(list(reversed(all_help_rows())))
    names = [name for name, _ in groups]
    expected = [name for name, _ in HELP_GROUPS if name in names]
    assert names == expected + (["其它"] if "其它" in names else []), names


def t_help_footer_reports_current_behaviour():
    """页脚要说真话：只@ 开与关的文案必须不同。"""
    on = help_footer({"group_at_only": True, "caps": {"default": 20}})
    off = help_footer({"group_at_only": False, "caps": {"default": UNLIMITED}})
    assert "只回 @" in on, on
    assert "叫名字" in off, off
    assert "不限" in off, off


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
