r"""被引用消息渲染的纯断言 —— 不起 NoneBot、不连 QQ，改完直接跑。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_quote_logic.py

管的是「这句被引用的话该渲染成什么样」；「插件真的去渲染了、MuiceBot 真的不崩了」
在 `test_quote_wiring.py`。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quote_logic as ql  # noqa: E402

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


class Seg:
    """顶替 nonebot 的 MessageSegment：只要 `.type` / `.data`。"""

    def __init__(self, type_, **data):
        self.type = type_
        self.data = data


def t_text_is_kept_verbatim():
    got = ql.render_segments([Seg("text", text="唔……霞月，你再转下去，泡泡都要被绕晕了。")])
    assert got == "唔……霞月，你再转下去，泡泡都要被绕晕了。", got


def t_at_uses_the_configured_name():
    """@3892894069 模型看不出是在叫她；@达妮娅 才看得出。"""
    got = ql.render_segments([Seg("at", qq="3892894069"), Seg("text", text="你还能回来吗？")],
                             {"3892894069": "达妮娅"})
    assert got == "@达妮娅 你还能回来吗？", got


def t_at_falls_back_to_the_segment_name_then_the_qq():
    assert ql.at_text("239166553", {"name": "不正经的小维生素"}, {}) == "@不正经的小维生素"
    assert ql.at_text("706069795", {}, None) == "@706069795"
    assert ql.at_text("all", {"name": "全体成员"}, {}) == "@全体成员"


def t_non_text_segments_become_markers_not_silence():
    got = ql.render_segments([Seg("image", url="https://x"), Seg("record", file="f"),
                              Seg("text", text="老大，没b怎么填")])
    assert got == "[图片] [语音] 老大，没b怎么填", got
    assert "https://x" not in got, "URL 不该进提示词"


def t_unknown_segment_is_marked_not_dropped():
    """认不出的段留个记号：全静默的话，回头没法解释「引用明明有东西，模型却说没有」。"""
    assert ql.render_segments([Seg("magic", foo="1")]) == "[magic]"


def t_nested_quote_pointer_is_skipped():
    """引用里套引用：只留最外层那条的正文，不把链条全灌进来。"""
    got = ql.render_segments([Seg("reply", id="-1107119364"), Seg("at", qq="706069795"),
                              Seg("text", text=" 我")])
    assert got == "@706069795 我", got


def t_blocks_are_labeled_the_way_prompt_guard_labels():
    got = ql.quoted_block([Seg("text", text="我在。连接很稳定。")], {}, sender_card="守岸人",
                          sender_id="3958874605")
    assert got == "〔发言人:守岸人〕\n我在。连接很稳定。", repr(got)


def t_known_person_gets_the_configured_name_not_the_qq_nickname():
    """跟 prompt_guard 同一套优先级：熟人表 > 名片/昵称 > 群友<QQ号>。"""
    got = ql.quoted_block([Seg("text", text="娅娅")], {"10000001": "漂泊者"},
                          sender_card="对乙酰氨基酚", sender_id="10000001")
    assert got == "〔发言人:漂泊者〕\n娅娅", repr(got)


def t_digital_card_degrades_to_a_groupmate_tag():
    got = ql.quoted_block([Seg("text", text="在吗")], {}, sender_card="706069795",
                          sender_id="706069795")
    assert got == "〔发言人:群友706069795〕\n在吗", repr(got)


def t_no_sender_at_all_still_renders_the_body():
    got = ql.quoted_block([Seg("text", text="就一句正文")])
    assert got == "就一句正文", repr(got)


def t_string_message_passes_through():
    assert ql.quoted_block("  已经渲染过的引用  ") == "已经渲染过的引用"


def t_empty_content_returns_empty_string():
    """空 msg / 只有引用指针 / None —— 调用方据此干脆不带引用，别拼出「被引用的消息: None」。"""
    assert ql.quoted_block(None) == ""
    assert ql.quoted_block([]) == ""
    assert ql.quoted_block([Seg("text", text="   ")]) == ""
    assert ql.quoted_block([Seg("reply", id="1")]) == ""


def t_long_quote_is_capped():
    got = ql.quoted_block([Seg("text", text="哈" * 500)])
    assert len(got) <= ql.MAX_CHARS + 1, len(got)
    assert got.endswith("…"), got[-10:]


def t_segment_view_accepts_tuples_for_testing():
    assert ql.segment_view(("text", {"text": "hi"})) == ("text", {"text": "hi"})
    assert ql.segment_view(Seg("at", qq="1")) == ("at", {"qq": "1"})


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
