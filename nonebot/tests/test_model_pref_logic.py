r"""`model_pref_logic` 的断言测试 —— 不 import nonebot、不碰 QQ、不调模型，直接跑：

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_model_pref_logic.py

为什么先测这一层：真正会出错的是两个判断 ——
「这条指令到底改不改得动状态」和「这一回合到底算不算首选失败了」。
前者写错就是把机器人切到一个不存在的条目上（她直接不回话），
后者写错要么该降级时不降级（还是已读不回），要么不该降级时乱降级
（首选明明在说话，却被换掉，同一轮还可能重说一遍）。
这两个判断都是纯函数，不该等到真机上才验。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_pref_logic import (  # noqa: E402
    DEFAULT_TIMEOUT,
    apply_command,
    bump,
    drive,
    help_rows,
    load_state,
    need_failover,
    new_state,
    noise,
    parse_model_command,
    pick_template,
    render_state,
    resolve_target,
    save_state,
    usable,
    with_rescue,
)

FAILED: list[str] = []
NAMES = ["relay", "deepseek", "echo-test"]
#: 上游 `/v1/models` 给的真模型名（实测 AxonHub 后面那批，2026-09-28）。
#: 形态是 `(从哪个条目拉到的, [模型名])` —— 清单本身是跟着条目（中转地址/Key）走的。
UPSTREAM = ["gpt-5.6-sol", "gpt-5.6-terra", "grok-4.7", "Qwen3.8-Flash"]
PULLED = ("relay", UPSTREAM)
DETAILS = {
    "relay": {"model_name": "gpt-5.6-sol", "provider": "openai", "template": "Denia", "stream": True},
    "deepseek": {"model_name": "deepseek-chat", "provider": "openai", "template": "Denia", "stream": True},
    "echo-test": {"model_name": "", "provider": "_echo", "template": "Muice", "stream": False},
}


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
    s = new_state()
    s.update(over)
    return s


def cmd(args, s=None, **kw):
    """跑一条指令，返回 (改了吗, 回复)。默认给三个可选条目。"""
    return apply_command(s if s is not None else state(), args, NAMES,
                         details=DETAILS, **kw)


# ---------------------------------------------------------------- 状态存取


def t_new_state_has_no_models_chosen_yet():
    s = new_state()
    assert s["primary"] is None, "刚装好时不该假装已有首选"
    assert s["secondary"] is None, "次选默认是没有 —— 没设就等于不降级"
    assert s["swaps"] == {}, "顶班次数从 0 开始数"


def t_state_round_trips_through_disk():
    path = Path(tempfile.mkdtemp()) / "model_pref.json"
    s = state(primary="relay", secondary="deepseek")
    bump(s, "deepseek")
    save_state(path, s)
    back = load_state(path, day=s["day"])
    assert back["primary"] == "relay" and back["secondary"] == "deepseek", f"没存住：{back}"
    assert back["swaps"] == {"deepseek": 1}, f"顶班次数没存住：{back}"


def t_broken_state_file_falls_back_to_empty():
    path = Path(tempfile.mkdtemp()) / "model_pref.json"
    path.write_text("{ 这不是 json", encoding="utf-8")
    s = load_state(path)
    assert s["primary"] is None and s["swaps"] == {}, f"坏文件要当没有处理：{s}"


def t_unknown_fields_are_dropped():
    path = Path(tempfile.mkdtemp()) / "model_pref.json"
    path.write_text(json.dumps({"primary": 12345, "secondary": ["a"], "swaps": {"relay": "很多次"}}),
                    encoding="utf-8")
    s = load_state(path)
    assert s["primary"] is None, "首选必须是字符串名字，数字不算"
    assert s["secondary"] is None, "次选写成列表不算"
    assert s["swaps"] == {}, "次数必须是正整数"


def t_rescue_counts_reset_on_a_new_day_but_choices_do_not():
    path = Path(tempfile.mkdtemp()) / "model_pref.json"
    s = state(primary="relay", secondary="deepseek", swaps={"deepseek": 3})
    s["day"] = "2026-09-26"
    save_state(path, s)
    back = load_state(path, day="2026-09-27")
    assert back["primary"] == "relay", "首选要跨天留着，重启后也得照用"
    assert back["swaps"] == {}, "「今天顶了几次」跨天要归零"


# ---------------------------------------------------------------- 指令


def t_view_lists_every_available_entry():
    changed, txt = cmd(["查看"])
    assert not changed, "查看不该改状态"
    for n in NAMES:
        assert n in txt, f"列表里要能看到每个可选条目：\n{txt}"
    assert "次选" in txt and "未设置" in txt, f"没设次选要说真话：\n{txt}"


def t_view_shows_the_current_setting_and_the_live_entry():
    s = state(primary="relay", secondary="deepseek")
    bump(s, "deepseek")
    _, txt = cmd(["查看"], s=s, current="relay")
    assert "relay" in txt and "deepseek" in txt, f"要看出现在用的和备胎：\n{txt}"
    assert "当前生效" in txt, f"要说实际生效的是哪个：\n{txt}"
    assert "1" in txt, f"今天顶过一次班就该看得见：\n{txt}"


def t_view_names_the_kill_switch():
    _, txt = cmd(["查看"])
    assert "MODEL_FAILOVER" in txt, "开关名要写在查看里，下次找得到"
    assert f"{DEFAULT_TIMEOUT:g}" in txt, "首字超时也要露出来"


def t_set_primary_needs_a_known_entry():
    s = state()
    changed, txt = cmd(["首选", "不存在"], s=s)
    assert not changed and s["primary"] is None, "认不出的条目名绝不能写进状态"
    for n in NAMES:
        assert n in txt, f"打错了要把可选项报出来：\n{txt}"


def t_set_primary_writes_and_confirms():
    s = state()
    changed, txt = cmd(["首选", "relay"], s=s)
    assert changed and s["primary"] == "relay", f"应改成功：{s}"
    assert "relay" in txt, "回话里要说清现在的首选"


def t_set_primary_without_argument_shows_usage():
    s = state()
    changed, txt = cmd(["首选"], s=s)
    assert not changed and s["primary"] is None, "光说「首选」不给名字，不能改"
    assert "用法" in txt or "模型 首选" in txt, f"要教怎么写：\n{txt}"


def t_secondary_can_be_set_and_cleared():
    s = state(primary="relay")
    changed, txt = cmd(["次选", "deepseek"], s=s)
    assert changed and s["secondary"] == "deepseek", f"次选没设上：{s}"
    changed2, txt2 = cmd(["次选", "无"], s=s)
    assert changed2 and s["secondary"] is None, f"「无」要能清掉次选：{s}"
    assert "降级" in txt2 or "不降级" in txt2, f"清掉之后要说清后果：\n{txt2}"


def t_secondary_refuses_to_be_the_same_as_primary():
    s = state(primary="relay")
    changed, txt = cmd(["次选", "relay"], s=s)
    assert not changed, "次选跟首选同名，降级永远不会发生"
    assert "同一个" in txt, f"要说清为什么不让设：\n{txt}"


def t_changing_primary_away_from_the_secondary_is_allowed():
    s = state(primary="relay", secondary="deepseek")
    changed, txt = cmd(["首选", "deepseek"], s=s)
    assert changed and s["primary"] == "deepseek", f"首选该改成 deepseek：{s}"
    assert s["secondary"] is None, f"改完两个同名了，次选要自动让位：{s}"
    assert "次选" in txt, f"顺手清了次选必须说出来，不能悄悄改：\n{txt}"


def t_unknown_subcommand_shows_usage():
    s = state()
    changed, txt = cmd(["换个"], s=s)
    assert not changed, "认不出的子指令不许改状态"
    assert "查看" in txt and "首选" in txt and "次选" in txt, f"用法表要齐全：\n{txt}"


def t_entry_names_are_case_insensitive():
    s = state()
    changed, _ = cmd(["首选", "RELAY"], s=s)
    assert changed and s["primary"] == "relay", f"大小写不该挡住设置：{s}"


def t_parses_prefixed_bare_and_called_forms():
    cases = [".模型 查看", "/模型 查看", "模型 查看", "达妮娅，.模型 次选 relay", "娅 模型 首选 relay"]
    for text in cases:
        got = parse_model_command(text, ("达妮娅", "小娅", "娅"))
        assert got is not None, f"这种写法要认：{text}"
    assert parse_model_command(".模型", ("达妮娅",))[1:] == [], "光一个「.模型」也算指令（回状态表）"
    assert parse_model_command(".模型 次选 relay", ("达妮娅",))[1:] == ["次选", "relay"]
    assert parse_model_command("帮我看看模型 怎么样", ("达妮娅",)) is None, "句子里夹着「模型」不算指令"
    assert parse_model_command("模型 换个", ("达妮娅",)) is not None, "子指令认不出交给 apply 报用法"


def t_glued_subcommands_are_split_too():
    """「模型首选relay」这种不打空格的也得认 —— 限流那边就认，两边口径要一致。"""
    assert parse_model_command("模型查看", ()) == ["模型", "查看"]
    assert parse_model_command("模型首选relay", ()) == ["模型", "首选", "relay"]
    assert parse_model_command("模型次选无", ()) == ["模型", "次选", "无"]
    assert parse_model_command("模型说的不是你", ()) is None, "认不出子指令就当普通聊天，别抢话"


# ---------------------------------------------------------------- 什么时候该降级


def t_failover_stays_quiet_when_the_switch_is_off():
    ok, why = need_failover(state(primary="relay", secondary="deepseek"),
                            enabled=False, error="RuntimeError: 炸了")
    assert not ok and "MODEL_FAILOVER" in why, f"开关关着就不能动：{ok} {why}"


def t_failover_needs_a_secondary_first():
    ok, why = need_failover(state(primary="relay"), error="RuntimeError: 炸了")
    assert not ok, "没设次选时不该降级"
    assert "次选" in why, f"要说清是没设次选：{why}"


def t_failover_refuses_the_same_entry_twice():
    s = state(primary="relay")
    s["secondary"] = "relay"
    ok, _ = need_failover(s, error="RuntimeError: 炸了")
    assert not ok, "同一条目再跑一遍没有意义"


def t_failover_triggers_on_an_error():
    ok, why = need_failover(state(primary="relay", secondary="deepseek"),
                            error="RuntimeError: 模型调用器返回的值应至少包含一个元素")
    assert ok, "首选抛异常必须顶上"
    assert "RuntimeError" in why, f"原因要带上异常，日志才好查：{why}"


def t_failover_triggers_when_nothing_usable_came_out():
    s = state(primary="relay", secondary="deepseek")
    for text in ("", "   ", "\n\n", "模型回复为空，可能是模型未能正确处理请求，请检查模型配置或输入内容。"):
        ok, _ = need_failover(s, text=text)
        assert ok, f"这种产出等于没回话，该降级：{text!r}"


def t_no_failover_when_the_primary_actually_answered():
    s = state(primary="relay", secondary="deepseek")
    ok, why = need_failover(s, text="嗯，我在的。")
    assert not ok, f"首选明明回上了，不许换人：{why}"


def t_failover_never_restarts_once_she_started_talking():
    s = state(primary="relay", secondary="deepseek")
    # 只在「确实失败了」的前提下才谈吐没吐过字 —— 正常回话时理由必须是空的，
    # 否则每回复一次都要往日志里塞一行废话。
    ok, why = need_failover(s, streamed=True, text="前半句话已经发出去了",
                             error="RuntimeError: 流到一半断了")
    assert not ok, "已经吐过字就不能重跑，否则同一轮说两遍"
    assert "吐过字" in why or "两遍" in why, f"要说清为什么忍住：{why}"
    quiet = need_failover(s, streamed=True, text="完整的一句话")[1]
    assert quiet == "", f"成功那一趟不该给理由：{quiet}"


def t_usable_sees_through_boilerplate():
    assert not usable(""), "空的不算"
    assert not usable("  \n "), "空白不算"
    assert not usable("模型未加载"), "MuiceBot 的兜底文案不算产出"
    assert not usable("模型回复为空，可能是模型未能正确处理请求，请检查模型配置或输入内容。")
    assert usable("达妮娅在的"), "有正文才算回了话"


def t_noise_flags_only_the_boilerplate_chunks():
    """降级时要把这行扣住 —— 不然次选的话前面会多一句系统提示。"""
    assert noise("模型回复为空，可能是模型未能正确处理请求，请检查模型配置或输入内容。")
    assert noise("  模型未加载")
    assert not noise(""), "空 chunk（只带资源的）要照转"
    assert not noise("\n\n")
    assert not noise("达妮娅在的")
    assert not noise("模型很好，继续说吧"), "别把正常说话里带「模型」两个字当兜底文案"


# ---------------------------------------------------------------- 人设不能跟着模型一起换


def t_fallback_inherits_the_personality_when_the_entry_says_nothing():
    assert pick_template("Denia", "Muice") == "Denia", "次选条目没专门配人设就沿用首选的"
    assert pick_template("Denia", None) == "Denia", "次选没 template 字段也要沿用"
    assert pick_template("Denia", "Other") == "Other", "次选自己配了人设就别抢"
    assert pick_template(None, "Muice") is None, "首选自己都没配就别硬塞"


# ---------------------------------------------------------------- 跑一趟模型（drive）


class Chunk:
    """MuiceBot 的 `ModelStreamCompletions` 只需要 `.chunk` 这一点被用到。"""

    def __init__(self, chunk=""):
        self.chunk = chunk


async def _agen(words=(), boom=None, delay_first=0.0, delay_later=0.0):
    if delay_first:
        await asyncio.sleep(delay_first)
    for i, w in enumerate(words):
        yield Chunk(w)
        if delay_later and i == 0:
            await asyncio.sleep(delay_later)
    if boom:
        raise boom


def collect(source):
    """跑完一个异步生成器，把转出去的 chunk 文本和它写在 record 里的结论一起拿回来。"""
    seen, rec = [], {}

    async def go():
        async for c in source(rec):
            seen.append(c.chunk)

    asyncio.run(go())
    return "".join(seen), rec


def t_drive_passes_chunks_through_and_collects_the_text():
    def source(rec):
        return drive(_agen(["嗯", "，我在的"]), rec)

    text, rec = collect(source)
    assert text == "嗯，我在的", "chunk 要原样、按序、立刻转出去（流式不能憋着）"
    assert rec["text"] == "嗯，我在的" and rec["streamed"] is True, f"要记下产出了什么：{rec}"
    assert not rec["error"], f"顺利时不该有失败原因：{rec}"


def t_drive_blames_a_raising_provider():
    def source(rec):
        return drive(_agen(boom=RuntimeError("模型调用器返回的值应至少包含一个元素")), rec)

    text, rec = collect(source)
    assert text == "", "抛之前没出过字"
    assert "RuntimeError" in rec["error"], f"异常类型和话都要留下来给日志：{rec}"


def t_drive_treats_a_silent_stream_as_no_output():
    def source(rec):
        return drive(_agen([]), rec)

    _, rec = collect(source)
    assert rec["streamed"] is False and not rec["error"], f"整条流一个 chunk 都没有：{rec}"
    ok, _ = need_failover(state(primary="relay", secondary="deepseek"), text=rec["text"])
    assert ok, "这种「静默失败」正是这次要救的场景"


def t_drive_times_out_when_the_first_chunk_never_arrives():
    def source(rec):
        return drive(_agen(["太晚了"], delay_first=0.3), rec, timeout=0.05)

    text, rec = collect(source)
    assert text == "", "超时之后不该再转出半个字"
    assert "超时" in rec["error"], f"要说清是等不到首字：{rec}"


def t_drive_does_not_time_out_between_chunks():
    def source(rec):
        return drive(_agen(["第一", "第二"], delay_later=0.2), rec, timeout=0.05)

    text, rec = collect(source)
    assert text == "第一第二", "首字到了之后，中间慢不该被掐"
    assert not rec["error"], rec


# ---------------------------------------------------------------- 次选顶上（with_rescue）


def rescue_state(**over):
    s = state(primary="relay", secondary="deepseek")
    s.update(over)
    return s


def run_rescue(s, first_words, *, make_rescue=None, enabled=True, timeout=None, boom=None):
    """跑一次完整的「首选 → 该不该换人 → 次选」，把过程中的痕迹都收在 dict 里返回。"""
    rec = {"notes": [], "cleaned": 0}

    def make_first():
        return _agen(first_words, boom=boom)

    def note(ok, why):
        rec["notes"].append((ok, why))

    def cleanup():
        rec["cleaned"] += 1

    async def go():
        out = []
        async for c in with_rescue(
            s, make_first,
            make_rescue=make_rescue, enabled=enabled, timeout=timeout,
            note=note, cleanup=cleanup,
        ):
            out.append(c.chunk)
        rec["text"] = "".join(out)

    asyncio.run(go())
    return rec


def t_with_rescue_leaves_the_primary_alone_when_it_answered():
    def build():
        raise AssertionError("首选明明在说话，不该换人")

    r = run_rescue(rescue_state(), ["嗯，我在的"], make_rescue=build)
    assert r["text"] == "嗯，我在的", r
    assert r["cleaned"] == 0, r
    assert not r["notes"], f"没降级就别留痕：{r}"


def t_with_rescue_runs_the_secondary_when_the_primary_said_nothing():
    hits = []

    def build():
        hits.append("次选跑了一趟")
        return _agen(["（次选救场）"])

    r = run_rescue(rescue_state(), [], make_rescue=build)
    assert r["text"] == "（次选救场）", f"次选的话要转出去：{r}"
    assert hits == ["次选跑了一趟"], "该降级时必须真的跑一次"
    assert r["cleaned"] == 1, f"跑完要把全局状态恢复回首选：{r}"
    assert r["notes"] and r["notes"][0][0] is True, f"降级要留痕：{r}"


def t_with_rescue_runs_the_secondary_when_the_primary_raised():
    def build():
        return _agen(["补上的回复"])

    r = run_rescue(rescue_state(), [], make_rescue=build,
                   boom=RuntimeError("模型调用器返回的值应至少包含一个元素"))
    assert r["text"] == "补上的回复", r
    assert "RuntimeError" in r["notes"][0][1], f"理由要带异常：{r}"


def t_with_rescue_never_restarts_a_stream_that_already_talked():
    called = []

    def build():
        called.append(1)
        return _agen(["再来一整段"])

    r = run_rescue(rescue_state(), ["半截话已经发出去了"], make_rescue=build,
                   boom=RuntimeError("流到一半断了"))
    assert r["text"] == "半截话已经发出去了", f"已经吐过字就不重跑：{r}"
    assert not called, "次选不该被叫起来"
    assert r["cleaned"] == 0, "没换过人，也就没什么要恢复"


def t_with_rescue_does_nothing_without_a_secondary_or_the_switch():
    def build():
        raise AssertionError("不该跑次选")

    r = run_rescue(rescue_state(secondary=None), [], make_rescue=build)
    assert r["text"] == "" and r["cleaned"] == 0, "没设次选就保持原来的静默"
    r2 = run_rescue(rescue_state(), [], make_rescue=build, enabled=False)
    assert r2["text"] == "", "MODEL_FAILOVER=0 时一律不动"


def t_with_rescue_still_cleans_up_when_the_rescue_also_fails():
    def build():
        return _agen(boom=RuntimeError("次选也连不上"))

    r = run_rescue(rescue_state(), [], make_rescue=build)
    assert r["cleaned"] == 1, f"次选也崩了也要恢复首选，不能让她一直用次选：{r}"
    assert r["text"] == "", "两边都没产出就维持沉默，不发「模型出错」"


# ---------------------------------------------------------------- 次数与帮助


def t_bump_counts_each_rescue():
    s = state()
    assert bump(s, "deepseek") == 1
    assert bump(s, "deepseek") == 2
    assert bump(s, "relay") == 1, "不同条目分开数"


def t_help_rows_list_the_model_commands():
    rows = help_rows()
    assert len(rows) >= 3, "查看 / 首选 / 次选 至少三行"
    joined = " ".join(c for c, _ in rows)
    for need in ("模型", "查看", "首选", "次选"):
        assert need in joined, f"指令表漏了「{need}」"
    assert all("主人" in d for _, d in rows), "这些都是主人指令，得标出来"


# ---------------------------------------------------------------- 名字到底指条目还是真模型
#
# 「首选 gpt-5.6-terra」里的 terra 不是 models.yml 的条目，是上游那个真模型名。
# 这一组判断把两种写法都接下来，但**只在名字真的存在时**才写盘。


def t_an_entry_name_is_resolved_as_an_entry():
    got = resolve_target("relay", NAMES, UPSTREAM, base_entry="relay")
    assert got["kind"] == "entry" and got["entry"] == "relay", got
    assert got["model"] is None, "点条目名就是把覆盖清掉，别把上一个模型名拖着"


def t_a_real_model_name_rides_on_the_current_entry():
    got = resolve_target("gpt-5.6-terra", NAMES, UPSTREAM, base_entry="relay")
    assert got["kind"] == "model", got
    assert got["entry"] == "relay", f"中转地址/Key/人设都跟着条目走：{got['entry']}"
    assert got["model"] == "gpt-5.6-terra", got


def t_the_tuple_shape_lets_the_source_entry_decide_by_itself():
    """`list_upstream()` 返回的就是 `(来源条目, 清单)` —— 直接喂进来也该认，不必再拆一遍。

    真名挂在**清单是从哪条拉来的**那个条目上才是对的：那个条目带着中转地址和 Key，
    而 `base_entry` 只是「当前首选」，跟上游清单未必来自同一条。
    """
    got = resolve_target("gpt-5.6-terra", NAMES, PULLED)
    assert got["kind"] == "model", got
    assert got["entry"] == "relay", f"该认清单自己的来源：{got}"
    # 显式给了 base_entry 时仍以来源条目为准（否则等于把两个不同条目的中转和名字拼一起）
    assert resolve_target("gpt-5.6-terra", NAMES, ("deepseek", UPSTREAM))["entry"] == "deepseek"


def t_the_entry_name_wins_when_both_sides_spelled_it():
    """上游哪天也给一个叫 `relay` 的模型时，条目优先 —— 条目是主人已经认下的那层意思。"""
    got = resolve_target("relay", NAMES, ["relay", "gpt-5.6-terra"], base_entry="relay")
    assert got["kind"] == "entry", got


def t_spelling_case_does_not_matter_but_the_official_spelling_is_kept():
    got = resolve_target("GPT-5.6-TERRA", NAMES, UPSTREAM, base_entry="relay")
    assert got["kind"] == "model" and got["model"] == "gpt-5.6-terra", f"要存上游那份写法：{got}"
    assert resolve_target("Echo-Test", NAMES, UPSTREAM, base_entry="relay")["entry"] == "echo-test"


def t_a_name_that_is_neither_shows_both_lists():
    got = resolve_target("gpt-9-nope", NAMES, UPSTREAM, base_entry="relay")
    assert got["kind"] == "error", got
    why = got["why"]
    assert "relay" in why and "gpt-5.6-terra" in why, f"两张清单都得摆出来：{why}"
    assert "条目" in why and "上游" in why, f"要说清哪边是哪边：{why}"


def t_a_model_name_without_an_entry_to_ride_on_is_refused():
    """次选可以没有可依附的条目（首选还没设），这时点模型名只能拒 —— 存进去下一回合切不过去。"""
    got = resolve_target("gpt-5.6-terra", NAMES, UPSTREAM, base_entry=None)
    assert got["kind"] == "error" and "首选" in got["why"], got


def t_an_unreachable_upstream_still_accepts_entry_names():
    """网关没起也只该关掉「点真模型」这一半，条目名不依赖网络。"""
    got = resolve_target("relay", NAMES, None, base_entry="relay")
    assert got["kind"] == "entry", got
    miss = resolve_target("gpt-5.6-terra", NAMES, None, base_entry="relay")
    assert miss["kind"] == "error", miss
    assert "没拉到" in miss["why"] or "拉不到" in miss["why"], f"要说明是拉取失败：{miss['why']}"


# ---------------------------------------------------------------- 指令：真模型名这一路


def t_setting_the_primary_by_real_model_name_records_the_override():
    s = state()
    changed, reply = cmd(["首选", "gpt-5.6-terra"], s, upstream=PULLED)
    assert changed, reply
    assert s["primary"] == "relay" and s["primary_model"] == "gpt-5.6-terra", s
    assert "gpt-5.6-terra" in reply and "relay" in reply, reply


def t_the_real_model_override_is_written_to_disk_and_comes_back():
    s = state(primary="relay", primary_model="gpt-5.6-terra")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "model_pref.json"
        save_state(p, s)
        back = load_state(p)
    assert back["primary_model"] == "gpt-5.6-terra", back
    assert back["primary"] == "relay", back


def t_a_hand_edited_override_that_is_not_a_string_is_dropped_not_crashed():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "model_pref.json"
        p.write_text('{"primary": "relay", "primary_model": 42}', encoding="utf-8")
        back = load_state(p)
    assert back["primary_model"] is None, f"数字不是模型名，当成没设：{back}"


def t_picking_the_entry_itself_clears_the_override():
    s = state(primary="relay", primary_model="gpt-5.6-terra")
    changed, reply = cmd(["首选", "relay"], s, upstream=PULLED)
    assert changed and s["primary_model"] is None, f"点条目就该回到 models.yml 写的名字：{s}"


def t_an_unknown_name_writes_nothing():
    s = state(primary="relay")
    changed, reply = cmd(["首选", "gpt-9-nope"], s, upstream=PULLED)
    assert not changed, "认不出的名字绝不能落盘"
    assert s == state(primary="relay"), s
    assert "gpt-5.6-terra" in reply, f"要把上游可点的都列出来：{reply}"


def t_the_secondary_accepts_a_real_model_name_too():
    s = state(primary="relay")
    changed, reply = cmd(["次选", "grok-4.7"], s, upstream=PULLED)
    assert changed, reply
    assert s["secondary"] == "relay" and s["secondary_model"] == "grok-4.7", s
    assert "gpt-5.6-terra" in reply or "grok" in reply, reply


def t_an_override_naming_what_the_entry_already_uses_is_stored_clean():
    """`首选 gpt-5.6-sol` 本来就是 relay 那个名字 —— 别把「等于默认」记成覆盖，
    否则次选填 relay 会被当成两种不同选择，降级成了空转。"""
    s = state()
    changed, reply = cmd(["首选", "gpt-5.6-sol"], s, upstream=PULLED)
    assert changed, reply
    assert s["primary"] == "relay" and s["primary_model"] is None, s


def t_a_model_name_is_refused_when_the_upstream_list_did_not_arrive():
    s = state(primary="relay")
    changed, reply = cmd(["首选", "gpt-5.6-terra"], s, upstream=None)
    assert not changed, "拉不到清单就别写：存个切不过去的名字，下一回合整晚不回话"
    assert "relay" in reply and "deepseek" in reply, f"本地条目还是要列出来：{reply}"


def t_the_view_shows_the_effective_real_model_and_the_upstream_list():
    s = state(primary="relay", primary_model="gpt-5.6-terra")
    text = render_state(s, NAMES, details=DETAILS, upstream=UPSTREAM)
    assert "gpt-5.6-terra" in text, text
    assert "gpt-5.6-sol" in text, f"该看得出 models.yml 原本写的是谁：{text}"
    assert "覆盖" in text, f"要说清这是覆盖不是条目本身：{text}"


def t_the_view_without_a_live_upstream_call_never_touches_the_network():
    """`模型 查看` 不带 upstream 参数时照常出字 —— 断网/网关挂了都不能把指令噎住，
    也不许冒出一行「上游清单」假装刚才去拉过（USAGE 里提「上游」是正常的，别看错）。"""
    text = render_state(state(primary="relay"), NAMES, details=DETAILS)
    assert "relay" in text, text
    assert "上游可选真模型" not in text, text


# ---------------------------------------------------------------- 同一条目里的两个模型


def t_failover_can_be_a_different_model_inside_the_same_entry():
    """首选 gpt-5.6-sol 坏了，次选 gpt-5.6-terra —— 同一个中转条目、两个真模型，这是合法降级。"""
    s = state(primary="relay", primary_model="gpt-5.6-sol",
              secondary="relay", secondary_model="gpt-5.6-terra")
    ok, why = need_failover(s, streamed=False, text="")
    assert ok, f"真模型名不同就该能救场：{why}"


def t_the_identical_model_is_not_its_own_rescue():
    s = state(primary="relay", primary_model="gpt-5.6-terra",
              secondary="relay", secondary_model="gpt-5.6-terra")
    ok, why = need_failover(s, streamed=False, text="")
    assert not ok, "同一个模型重跑一遍不叫降级"
    assert "同一个" in why, why


def t_the_same_entry_with_no_override_on_either_side_is_still_one_model():
    s = state(primary="relay", secondary="relay")
    ok, why = need_failover(s, streamed=False, text="")
    assert not ok and "同一个" in why, f"{s} → {why}"


def t_setting_the_secondary_equal_to_the_primary_real_model_is_refused():
    s = state(primary="relay", primary_model="gpt-5.6-terra")
    changed, reply = cmd(["次选", "gpt-5.6-terra"], s, upstream=PULLED)
    assert not changed and "同一个" in reply, f"{changed}｜{reply}"
    assert s["secondary"] is None, s


def t_help_rows_mention_the_real_model_name_option():
    joined = " ".join(f"{c} {d}" for c, d in help_rows())
    assert "上游" in joined or "真模型" in joined, f"help 要让人知道能直接点模型名：{joined}"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
