r"""闸门接线的断言测试 —— 用 OneBot 真实事件对象喂 `gate_rule`，看它放不放行。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_gate_wiring.py

跟 `test_gate_logic.py` 的分工：那边管「上限算得对不对」，这边管
「拿到真实的 `GroupMessageEvent`、真实的 `Message` 段，规则还认不认」。
只有 `gate_rule` 返回 False，模型才会被跳过、token 才不会烧。

不连 QQ、不发网络请求：直接构造事件对象调规则。
MuiceBot 那套 Alconna 匹配器要等 NoneBot 的 TrieRule 预处理器把 state 填好才干活，
那是 NoneBot 自己的事，这里不掺和 —— 只证明闸门确实挂在了两个触发器的规则上。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)  # MuiceBot 按 ./plugins 找插件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot  # noqa: E402
from nonebot.log import logger  # noqa: E402

try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot
ensure_nonebot()
import gate_logic as gl  # noqa: E402
import plugins.gate as gate  # noqa: E402
from nonebot.adapters.onebot.v11 import (  # noqa: E402
    GroupMessageEvent,
    HeartbeatMetaEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Reply, Sender  # noqa: E402
from muicebot.models import Message as ChatMessage  # noqa: E402
from muicebot.onebot import at_event, nickname_event  # noqa: E402

SELF = "3892894069"
MASTER = "10000001"
GROUP = 741096253
FAILED: list[str] = []


class FakeBot:
    self_id = SELF


def _tmp_state():
    """把状态文件挪到临时目录，别拿真 data/gate.json 做实验。"""
    path = Path(tempfile.mkdtemp()) / "gate.json"
    gate.STATE_PATH = path
    gate._state_cache = None
    return path


def _msg(*segments) -> Message:
    m = Message()
    for s in segments:
        m += s
    return m


def group_event(text: str, at_self: bool = False, at_other: bool = False, user: str = "123456",
                segments: Message | None = None):
    m = segments if segments is not None else _msg(
        *([MessageSegment.at(SELF)] if at_self else []),
        *([MessageSegment.at("999999")] if at_other else []),
        *([MessageSegment.text(text)] if text else []),
    )
    return GroupMessageEvent(
        time=1700000000,
        self_id=int(SELF),
        post_type="message",
        sub_type="normal",
        user_id=int(user),
        group_id=GROUP,
        message_type="group",
        message_id=1,
        message=m,
        raw_message=str(m),
        font=0,
        sender={"card": "", "nickname": "t", "role": "member", "title": ""},
        to_me=at_self,
    )


def private_event(text: str, user: str = "123456"):
    m = _msg(MessageSegment.text(text)) if text else Message()
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


def allowed(event, bot=None) -> bool:
    """闸门对这条消息的判断结果。"""
    return asyncio.run(gate.gate_rule(bot or FakeBot(), event))


# ---------------------------------------------------------------- 挂载


def t_install_attaches_gate_rule_once():
    _tmp_state()
    asyncio.run(gate.install_gate())
    for name, matcher in (("nickname_event", nickname_event), ("at_event", at_event)):
        calls = [getattr(c, "call", None) for c in matcher.rule.checkers]
        assert gate.gate_rule in calls, f"闸门没挂到 {name} 上"
    n = len(nickname_event.rule.checkers)
    asyncio.run(gate.install_gate())
    assert len(nickname_event.rule.checkers) == n, "重复安装不该套两层"


# ---------------------------------------------------------------- 群里要 @


def t_non_message_events_pass_quietly():
    """MuiceBot 那两个 Alconna 匹配器不限事件类型（matcher.type 是空串），
    所以心跳、生命周期事件也会跑我们的规则。这些事件连 user_id 都没有，
    闸门必须安静地放行 —— 不能每 30 秒叫一声 WARNING。"""
    _tmp_state()
    ev = HeartbeatMetaEvent(time=1700000000, self_id=int(SELF), post_type="meta_event",
                            meta_event_type="heartbeat", interval=5000,
                            status={"online": True, "good": True})
    assert allowed(ev) is True, "心跳事件该放行（它根本不该被闸门管）"
    seen: list[str] = []
    hid = logger.add(seen.append, level="WARNING",
                     filter=lambda r: "[gate]" in r["message"])
    try:
        for _ in range(3):
            allowed(ev)
    finally:
        logger.remove(hid)
    assert not seen, f"心跳不该产生告警，实际：{seen}"


def t_group_message_without_at_is_blocked():
    _tmp_state()
    assert allowed(group_event("今天天气不错")) is False, "群里没 @ 就该拦"


def t_calling_by_name_alone_is_not_an_at():
    _tmp_state()
    assert allowed(group_event("达妮娅 今天天气不错")) is False, "光叫名字不算 @"


def t_group_message_with_at_passes():
    _tmp_state()
    assert allowed(group_event("你好呀", at_self=True)) is True, "@ 了就该放行"


def t_at_in_the_middle_still_counts():
    _tmp_state()
    ev = group_event("", segments=_msg(
        MessageSegment.text("顺便问一句 "),
        MessageSegment.at(SELF),
        MessageSegment.text(" 这个副本怎么打"),
    ))
    assert allowed(ev) is True, "@ 在中间也该算"


def t_at_someone_else_does_not_count():
    _tmp_state()
    assert allowed(group_event("达妮娅 你看他", at_other=True)) is False, "@ 别人不算 @ 我"


def t_at_all_does_not_count():
    _tmp_state()
    ev = group_event("", segments=_msg(MessageSegment.at("all"), MessageSegment.text("全员集合")))
    ev.to_me = True
    assert allowed(ev) is False, "@ 全员不该把机器人叫出来"


def t_quoted_reply_without_at_is_blocked():
    """引用我的消息但不 @ —— 设计里定的按没 @ 处理，只留一行日志。"""
    _tmp_state()
    ev = group_event("这个我也想知道")
    ev.to_me = True  # OneBot 会因为引用把 to_me 置真，但消息里没有 at 段
    assert allowed(ev) is False, "引用回复不该触发回复"


# ---------------------------------------------------------- 引用自动补的那个 @


def quote_event(text: str, at_qq: str | None = None, quoted_author: str | None = None,
                extra_at: str | None = None, user: str = "123456"):
    """造一条真机形状的「回复」事件：`[reply:id][at:qq=被引用者]正文`。

    `at_qq=None` 是「回复自己发的那条」（17:46 实测：QQ 不补 @）；
    `quoted_author=None` 是协议端没给 reply 详情，只认得出段位置。
    """
    segs = [MessageSegment.reply(55)]
    if at_qq:
        segs.append(MessageSegment.at(at_qq))
    segs.append(MessageSegment.text(text))
    if extra_at:
        segs += [MessageSegment.text(" 还有 "), MessageSegment.at(extra_at)]
    ev = group_event("", user=user, segments=_msg(*segs))
    ev.original_message = _msg(*segs)  # 闸门读的是这份，别让它落到空默认值上
    ev.to_me = bool(at_qq) or quoted_author == SELF
    if quoted_author:
        # 用真的 Reply 对象，别塞 dict —— 这些事件模型没开 validate_assignment，
        # 塞 dict 测的就是「接线代码顺手兼容 dict」，不是协议端真给的东西。
        ev.reply = Reply(
            time=1700000000, message_type="group", message_id=55, real_id=55,
            sender=Sender(user_id=int(quoted_author)),
            message=_msg(MessageSegment.text("被引用的原文")),
        )
    return ev


def t_auto_at_from_a_quote_counts_as_an_at():
    """2026-09-28 改的口径：引用回复她 + 那个 @ 落在她名下，就算在叫她。

    群里的人习惯点「回复」接着她的话说（下午连着四条都是这么被静默的），
    而引用她时 QQ 补的那个 @ 和人手打的一模一样，分不出来 —— 主人选了「算」。
    """
    _tmp_state()
    assert allowed(quote_event("那你看今天西格莉卡来了吗", SELF, quoted_author=SELF)) is True, \
        "引用她 + @ 她，该放行"


def t_the_strict_switch_puts_the_old_gate_back():
    """`QUOTE_AT_COUNTS=0` 退回严格口径：自动补的那个 @ 不算，要自己再 @ 一次。"""
    _tmp_state()
    nonebot.get_driver().config.quote_at_counts = "0"
    try:
        assert allowed(quote_event("那你再讲讲", SELF, quoted_author=SELF)) is False, \
            "开关关回去时只有引用带的 @ 该拦"
    finally:
        del nonebot.get_driver().config.quote_at_counts


def t_the_code_default_counts_the_quote_at():
    """代码默认也得是「算」：`.env` 少这一行（换机器、别人接手）时口径不能自己变回去。"""
    _tmp_state()
    cfg = nonebot.get_driver().config
    had = getattr(cfg, "quote_at_counts", None)
    if had is not None:
        del cfg.quote_at_counts  # nonebot.init() 会把真 .env 灌进来，先当它没写
    try:
        assert allowed(quote_event("那你再讲讲", SELF, quoted_author=SELF)) is True, \
            "`.env` 没写这行时，引用她 + @ 她也该回"
    finally:
        if had is not None:
            cfg.quote_at_counts = had


def t_the_shipped_env_agrees_with_the_new_default():
    """.env 那行还写着 0 的话，真跑起来仍是旧口径 —— 把「配置」也钉住，别只钉代码。"""
    text = (Path(__file__).resolve().parent.parent / ".env").read_text(encoding="utf-8")
    got = [line.split("=", 1)[1].strip() for line in text.splitlines()
           if line.strip().startswith("QUOTE_AT_COUNTS=")]
    assert got in ([], ["1"]), \
        f"引用补的 @ 要算：.env 得留 `QUOTE_AT_COUNTS=1`（现在是 {got or '没写'}）"


def t_manual_at_after_a_quote_still_passes():
    """严格口径下手打的第二个 @ 照样算数 —— 只吃自动补的那一个，别一条都不认。"""
    _tmp_state()
    nonebot.get_driver().config.quote_at_counts = "0"
    try:
        assert allowed(quote_event("在吗", SELF, quoted_author=SELF, extra_at=SELF)) is True
    finally:
        del nonebot.get_driver().config.quote_at_counts


def t_quoting_someone_else_and_atting_herself_passes():
    _tmp_state()
    assert allowed(quote_event("他说的这句怎么说", "1597516006",
                              quoted_author="1597516006", extra_at=SELF)) is True


def t_at_that_is_not_the_quoted_author_is_hands_on():
    """被引用的人对不上 → 这个 @ 是人家自己打的，别当自动补的吃掉。"""
    _tmp_state()
    assert allowed(quote_event("在吗", SELF, quoted_author="1597516006")) is True


def t_quote_of_own_message_has_no_at_at_all():
    """17:46 真实场景：回复自己那条 @ 过她的消息，整条没有 at 段 → 拦（连日志都不同一句）。"""
    _tmp_state()
    assert allowed(quote_event("好叭 你好好休息", None, quoted_author=MASTER)) is False


def t_master_is_not_affected_by_the_auto_at_rule():
    """豁免写在 @ 判定之前：这条改的是「别人」的门槛，主人不受影响（别以为收紧到他了）。"""
    _tmp_state()
    assert allowed(quote_event("好叭 你好好休息", None, quoted_author=MASTER, user=MASTER)) is True


def t_at_segments_come_from_the_unmodified_copy():
    """上游会把 `event.message` 里的 at 段剥掉，判断必须读 `original_message` 那份。"""
    _tmp_state()
    ev = group_event("在吗", at_self=True)
    assert gate._segments(ev) == [("at", SELF), ("text", "")]
    ev.message = _msg(MessageSegment.text("在吗"))  # 模拟被剥干净的现消息
    assert gate._segments(ev) == [("at", SELF), ("text", "")], "读到了被改过的那份消息"
    assert allowed(ev) is True, "剥了 at 段也不该把这条拦掉"


# ---------------------------------------------------------------- 上限


def t_group_over_cap_is_blocked():
    _tmp_state()
    ev = group_event("在吗", at_self=True)
    assert allowed(ev) is True
    s = gate.state()
    for _ in range(gl.DEFAULT_CAP):
        gl.bump(s, str(GROUP), "123456")
    assert allowed(ev) is False, "到了默认 20 次就该静默"


def t_cap_is_per_group_and_per_person():
    _tmp_state()
    s = gate.state()
    s["caps"]["member"][f"{GROUP}|123456"] = 1
    gl.bump(s, str(GROUP), "123456")
    assert allowed(group_event("在吗", at_self=True)) is False, "这个人该停了"
    assert allowed(group_event("在吗", at_self=True, user="654321")) is True, "别人不受影响"


def t_private_never_hits_the_cap():
    _tmp_state()
    s = gate.state()
    s["caps"]["member"][f"{GROUP}|123456"] = 1
    gl.bump(s, str(GROUP), "123456")
    assert allowed(private_event("在吗")) is True, "私聊不看群里的量"


def t_master_is_exempt_from_at_and_cap():
    _tmp_state()
    s = gate.state()
    for _ in range(gl.DEFAULT_CAP * 2):
        gl.bump(s, str(GROUP), MASTER)
    assert allowed(group_event("达妮娅 帮我看看", user=MASTER)) is True, "主人该随时能喊"


def t_rule_failure_fails_open_not_silent():
    """判定本身出岔子要放行这条：机器人突然全体装死，比多回一句难查得多。"""
    _tmp_state()

    class Boom:
        self_id = SELF

        @property
        def __getattr__(self, _):  # 任何属性都炸
            raise RuntimeError("bot 坏了")

    assert allowed(group_event("在吗", at_self=True), Boom()) is True


# ---------------------------------------------------------------- 计数


def t_count_hook_only_counts_group_replies():
    path = _tmp_state()
    s = gate.state()
    reply = ChatMessage(userid="123456", groupid=str(GROUP), message="在吗", respond="在的")
    asyncio.run(gate.count_group_reply(group_event("在吗", at_self=True), reply))
    assert gl.usage(s, str(GROUP), "123456") == 1, "群里回完该记一次"
    asyncio.run(gate.count_group_reply(private_event("在吗"), ChatMessage(userid="123456", groupid="-1")))
    assert len(s["counts"]) == 1, "私聊不该记账"
    assert gl.load_state(path)["counts"][f"{GROUP}|123456"] == 1, "计完要落盘，不然重启就忘了"


def t_state_rolls_over_on_a_new_day():
    """盘上还是昨天的账，内存换新一天时该重读并清空 counts，但留着 caps。"""
    path = _tmp_state()
    old = gl.new_state("2000-01-01", default_cap=5)
    gl.bump(old, str(GROUP), "123456")
    old["caps"]["member"][f"{GROUP}|123456"] = 1
    gl.save_state(path, old)
    gate._state_cache = dict(old)  # 假装内存里还留着昨天那份
    s = gate.state()
    assert gl.usage(s, str(GROUP), "123456") == 0, "跨天该从头数"
    assert s["caps"]["member"][f"{GROUP}|123456"] == 1, "上限是配置，不该跟着清零"
    assert s["caps"]["default"] == 5, "默认上限也该留着"


# ---------------------------------------------------------------- 指令与帮助


def t_gate_command_only_in_private():
    assert asyncio.run(gate.is_gate_command(private_event("限流 查看"))) is True
    assert asyncio.run(gate.is_gate_command(private_event("限流默认5"))) is True
    assert asyncio.run(gate.is_gate_command(private_event("今天天气不错"))) is False
    ev = group_event("限流 查看", at_self=True)
    assert asyncio.run(gate.is_gate_command(ev)) is False, "群里不许公开调上限"


def t_command_run_persists_and_replies():
    path = _tmp_state()
    reply = gate.run_command(["默认", "5"])
    assert "5 次/天" in reply, f"该回一句确认：{reply}"
    assert gl.load_state(path)["caps"]["default"] == 5, "改了就该在盘上"
    reply = gate.run_command(["默认"])
    assert "用法" in reply and gl.load_state(path)["caps"]["default"] == 5, "打错了别改东西"


def t_help_needs_prefix_for_latin_word():
    assert asyncio.run(gate.is_help_command(private_event(".help"))) is True
    assert asyncio.run(gate.is_help_command(private_event("/帮助"))) is True
    assert asyncio.run(gate.is_help_command(private_event("帮助"))) is True
    assert asyncio.run(gate.is_help_command(group_event("help me", at_self=True))) is False, \
        "别把 help me 当指令"


def t_help_lists_every_command_with_current_settings():
    path = _tmp_state()
    gate.run_command(["默认", "3"])
    gate.run_command(["群内只@", "关"])
    text = gate.help_text()
    for need in ("help", "status", "kb", "限流", "3", "叫名字"):
        assert need in text, f"帮助里少了「{need}」：\n{text}"
    assert "群里只回 @ 我的消息" not in text, "开关已经关了，帮助里别写反话"
    assert gl.load_state(path)["group_at_only"] is False


def t_help_and_command_matchers_outrank_muicebot():
    """优先级不对，我们的指令就会被 MuiceBot 自己的匹配器抢先（或抢不到）。"""
    assert gate.help_cmd.priority < 90, "MuiceBot 的 command_help 是 90"
    assert gate.gate_cmd.priority < 90
    assert nickname_event.priority == 99 and at_event.priority == 100


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
