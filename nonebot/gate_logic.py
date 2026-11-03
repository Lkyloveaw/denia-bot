r"""群聊发言闸门的纯逻辑 —— 上限取值、日切、计数、私聊指令解析。

这个文件**故意不 import nonebot / muicebot**：闸门真正生效要靠 NoneBot 的事件传播，
非得整条链跑起来才能验；但「谁的上限是多少、什么时候清零、指令有没有打错」
是纯算错就会让机器人在群里装死的地方，所以全部下沉到这里，用
`nonebot\tests\test_gate_logic.py` 直接断言。

存储（默认 `nonebot\data\gate.json`）：

    {
      "day": "2026-09-27",                                  # 计数所属的自然日
      "counts": {"群号|QQ号": 3},                            # 今天机器人真的回了几次
      "caps": {"default": 20, "group": {"群号": 5}, "member": {"群号|QQ号": 0}},
      "group_at_only": true                                  # 群里是否只认 @
    }

上限 0（`UNLIMITED`）= 不限。取值优先次序 member > group > default。
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

UNLIMITED = 0
"""特殊上限：不计数限制（仍然记用量，方便你看谁在刷屏）。"""

DEFAULT_CAP = 20
DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "gate.json"

GROUP_HELP_LIMIT = 5
GROUP_HELP_WINDOW = 60


class HelpRateLimiter:
    """Per-group sliding window. Call on the event loop before yielding to render.

    Rejected requests do not extend the window. State is process-local and expires
    after 60 seconds; expired groups are removed as new requests arrive.
    """

    def __init__(self):
        self._groups: dict[str, deque[float]] = {}

    def acquire(self, group: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        cutoff = now - GROUP_HELP_WINDOW
        for key in list(self._groups):
            if self._groups[key][-1] <= cutoff:
                del self._groups[key]
        requests = self._groups.setdefault(str(group), deque())
        while requests and requests[0] <= cutoff:
            requests.popleft()
        if len(requests) >= GROUP_HELP_LIMIT:
            return False
        requests.append(now)
        return True

UNLIMITED_WORDS = {"不限", "无限", "不限制", "0", "off", "none"}
_ON_WORDS = {"开", "开启", "on", "true", "1", "是"}
_OFF_WORDS = {"关", "关闭", "off", "false", "0", "否"}

CAP_ALIASES = {"上限", "member", "限"}
GROUP_CAP_ALIASES = {"群上限", "群限", "group"}
DEFAULT_ALIASES = {"默认", "默认上限", "default"}
VIEW_ALIASES = {"查看", "状态", "看", "show", "list"}
RESET_ALIASES = {"清零", "清", "reset", "clear"}
ATONLY_ALIASES = {"群内只@", "只@", "群只@", "atonly", "at"}


# ---------------------------------------------------------------- 状态


def today() -> str:
    return time.strftime("%Y-%m-%d")


def new_state(day: str, default_cap: int = DEFAULT_CAP, group_at_only: bool = True) -> dict:
    return {
        "day": day,
        "counts": {},
        "caps": {"default": default_cap, "group": {}, "member": {}},
        "group_at_only": group_at_only,
    }


def _norm(src: Any, day: str, default_cap: int) -> dict:
    """把读到的原始 dict 收拾成结构完整、值可用的状态；坏字段丢掉而不是抛。"""
    s = new_state(day, default_cap)
    if not isinstance(src, dict):
        return s

    counts = src.get("counts")
    if isinstance(counts, dict):
        # 计数必须是正整数；被人手改过的、或者上一版写歪的一律不认
        s["counts"] = {
            str(k): v
            for k, v in counts.items()
            if isinstance(v, int) and not isinstance(v, bool) and v > 0
        }

    caps = src.get("caps")
    if isinstance(caps, dict):
        d = caps.get("default")
        if isinstance(d, int) and not isinstance(d, bool) and d >= UNLIMITED:
            s["caps"]["default"] = d
        for scope in ("group", "member"):
            table = caps.get(scope)
            if isinstance(table, dict):
                s["caps"][scope] = {
                    str(k): v
                    for k, v in table.items()
                    if isinstance(v, int) and not isinstance(v, bool) and v >= UNLIMITED
                }

    if isinstance(src.get("group_at_only"), bool):
        s["group_at_only"] = src["group_at_only"]
    if isinstance(src.get("day"), str):
        s["_file_day"] = src["day"]  # 留给日切判断用
    return s


def load_state(path: Path | str = DEFAULT_PATH, day: str | None = None,
               default_cap: int = DEFAULT_CAP) -> dict:
    """读状态。**跨天自动清零 counts，caps 保留。**"""
    day = day or today()
    raw: Any = None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None

    s = _norm(raw, day, default_cap)
    if s.pop("_file_day", day) != day:  # 日切：今天重新开始数
        s["counts"] = {}
    return s


def save_state(path: Path | str, state: dict) -> None:
    """原子替换：先写 .tmp 再 rename，中途断电也不会留下半个 json。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    payload = {k: state[k] for k in ("day", "counts", "caps", "group_at_only") if k in state}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- 判定


def member_key(group: str, user: str) -> str:
    return f"{group}|{user}"


def resolve_cap(state: dict, group: str, user: str) -> int:
    """上限取值：成员 > 群 > 默认。"""
    caps = state["caps"]
    k = member_key(group, user)
    if k in caps["member"]:
        return int(caps["member"][k])
    if str(group) in caps["group"]:
        return int(caps["group"][str(group)])
    return int(caps["default"])


def usage(state: dict, group: str, user: str) -> int:
    return int(state["counts"].get(member_key(group, user), 0))


def check(state: dict, group: str, user: str) -> tuple[bool, int, int]:
    """能不能回。返回 (放行?, 已用次数, 上限)；上限为 0 表示不限。"""
    cap, used = resolve_cap(state, group, user), usage(state, group, user)
    return (cap == UNLIMITED or used < cap), used, cap


def bump(state: dict, group: str, user: str) -> int:
    """记一次「机器人确实回了」。由回复完成钩子调用，不是由收到消息调用。"""
    k = member_key(group, user)
    state["counts"][k] = int(state["counts"].get(k, 0)) + 1
    return state["counts"][k]


def decide(state: dict, *, private: bool, at_self: bool, group: str, user: str,
           exempt: bool = False, auto_at: bool = False) -> tuple[bool, str]:
    """这条消息到底让不让 MuiceBot 回。返回 (放行?, 一句能写进日志的理由)。

    - 私聊：一律放行，且不查上限（限流只管群里刷屏）。
    - 主人（`exempt`）：放行，不查 @ 也不查用量。
    - 群聊：`group_at_only` 开着时必须真的 @ 了机器人；没 @ 直接拦，
      连模型都不调 —— 省 token，也不会出现「机器人突然插话」。
    - 然后才看到没到当天的量。

    `auto_at` 只影响说理由的那句话：`at_self` 为假、但这条消息里确实有个被当成
    引用自动补的 @（见 `auto_at_index`），日志就该写「只有引用带的 @」，
    别和「压根没 @」混成同一条 —— 排查时这两句差很远。
    """
    if private:
        return True, "私聊"
    if exempt:
        return True, "放行（主人豁免）"
    if state["group_at_only"] and not at_self:
        return False, "群里只有引用自动补的 @，没真的 @" if auto_at else "群里没 @ 机器人"
    ok, used, cap = check(state, group, user)
    if not ok:
        return False, f"群 {group} 的 {user} 今天 {used}/{fmt_cap(cap)} 已到上限"
    return True, f"放行 {used}/{fmt_cap(cap)}"


def auto_at_index(segments: Any, reply_author: str | int | None = None) -> int | None:
    """QQ「回复」自动补的那个 @ 排在第几段；不是这种写法就返回 None。

    QQ 客户端点「回复」时会把 `@被引用的人` 塞在正文最前面，真机上段的顺序是固定的：
    `[reply:id][at:qq=被引用者]正文`（logs/nonebot.out.log:59、:134 两条实测）。
    回复自己发的那条时它不补 @（:210），所以那种消息天生没有 at 段。

    光看位置就够判，但认得出被引用的人时再要它对上（`reply_author`）：
    对不上就说明这个 @ 是人手打的，不该吃掉。引文里自带的 @ 不算 —— 那是 reply 段
    里的一段文字，不会成为消息的段，这正是「回复一句 @ 过她的话」骗不过门禁的原因。
    """
    segs = list(segments or ())
    for i, seg in enumerate(segs[:-1]):
        if seg[0] != "reply":
            continue
        kind, qq = segs[i + 1][0], str(segs[i + 1][1])
        if kind != "at" or qq in ("all", "everyone"):
            return None
        if reply_author not in (None, "") and qq != str(reply_author):
            return None
        return i + 1
    return None


def mentions_self(segments: Any, self_id: str | int, *, ignore_auto_at: bool = False,
                  reply_author: str | int | None = None) -> bool:
    """消息里有没有 @ 机器人自己。

    segments 是 (type, qq) 二元组序列 —— 与 NoneBot 的 MessageSegment 解耦，好测。
    @ 出现在任何位置都算（NoneBot 原生的 to_me 只认首/尾，太反直觉），
    @ 全员与 @ 别人都不算。

    `ignore_auto_at=True` 时把 QQ 引用自动补的那一个 @ 剔掉 —— 那是「必须自己再 @ 一次」的
    严格口径，现在只有 `.env` 写 `QUOTE_AT_COUNTS=0` 才走。默认不剔：引用她 + 她名下那个 @
    就是在叫她（2026-09-28 主人的口径，这两段和手打的完全同形，分不出来）。剔也只剔那一个，
    同一条消息里再手打一个 @ 照样算数。
    """
    segs = list(segments or ())
    drop = auto_at_index(segs, reply_author) if ignore_auto_at else None
    target = str(self_id)
    for i, seg in enumerate(segs):
        if i == drop:
            continue
        kind, qq = seg[0], str(seg[1])
        if kind == "at" and qq not in ("all", "everyone") and qq == target:
            return True
    return False


# ---------------------------------------------------------------- 指令


def parse_cap(text: str) -> int | None:
    """指令里的次数参数 → 上限整数；`不限/0` → UNLIMITED；认不出来 → None。"""
    t = (text or "").strip()
    if not t:
        return None
    if t.lower() in UNLIMITED_WORDS:
        return UNLIMITED
    if t.isdigit():
        return int(t) or None  # 单独的 "0" 已被上面接走；防止 000 之类
    return None


USAGE = (
    "用法（私聊发送）：\n"
    "  .限流 上限 <群号> <QQ号> <次数|不限>   给某群某人单独设\n"
    "  .限流 群上限 <群号> <次数|不限>        给整群设\n"
    "  .限流 默认 <次数|不限>                 没被单独设过的人走这个\n"
    "  .限流 查看 [群号]                      看今天的用量\n"
    "  .限流 清零 [群号 [QQ号]]               手动归零\n"
    "  .限流 群内只@ 开|关                    群里是否只回 @ 的消息"
)


def apply_command(state: dict, args: list[str]) -> tuple[bool, str]:
    """执行一条私聊设置指令。返回 (改动了吗, 回给人看的话)。

    改没改决定要不要落盘 —— 查看类指令不写文件。
    """
    args = [a for a in (args or []) if a != ""]
    if not args:
        return False, USAGE

    head, rest = args[0], args[1:]

    if head in VIEW_ALIASES:
        return False, describe(state, group=rest[0] if rest else None)

    if head in ATONLY_ALIASES:
        if len(rest) != 1:
            return False, f"要写「限流 群内只@ 开」或「限流 群内只@ 关」。\n{USAGE}"
        word = rest[0].lower()
        if word in _ON_WORDS:
            state["group_at_only"] = True
            return True, "群里只回 @ 了机器人的消息。"
        if word in _OFF_WORDS:
            state["group_at_only"] = False
            return True, "群里恢复昵称前缀也能触发。"
        return False, f"「{rest[0]}」看不懂，只认 开 / 关。"

    if head in RESET_ALIASES:
        if not rest:
            state["counts"] = {}
            return True, "已清空今天的计数。"
        group = rest[0]
        if len(rest) >= 2:
            k = member_key(group, rest[1])
            had = state["counts"].pop(k, 0)
            return True, f"{group} 的 {rest[1]} 已清零（原本 {had} 次）。"
        removed = [k for k in state["counts"] if k.startswith(f"{group}|")]
        for k in removed:
            state["counts"].pop(k)
        return True, f"群 {group} 已清零（{len(removed)} 人）。"

    if head in DEFAULT_ALIASES:
        if len(rest) != 1:
            return False, f"「默认」只要一个参数。\n{USAGE}"
        cap = parse_cap(rest[0])
        if cap is None:
            return False, f"「{rest[0]}」不是次数。要说数字，或者「不限」。\n{USAGE}"
        state["caps"]["default"] = cap
        return True, _cap_text("默认上限", cap)

    if head in GROUP_CAP_ALIASES:
        if len(rest) != 2:
            return False, f"要写「限流 群上限 <群号> <次数|不限>」。\n{USAGE}"
        cap = parse_cap(rest[1])
        if cap is None:
            return False, f"「{rest[1]}」不是次数。\n{USAGE}"
        state["caps"]["group"][str(rest[0])] = cap
        return True, _cap_text(f"群 {rest[0]} 上限", cap)

    if head in CAP_ALIASES:
        if len(rest) != 3:
            return False, f"要写「限流 上限 <群号> <QQ号> <次数|不限>」。\n{USAGE}"
        cap = parse_cap(rest[2])
        if cap is None:
            return False, f"「{rest[2]}」不是次数。\n{USAGE}"
        state["caps"]["member"][member_key(rest[0], rest[1])] = cap
        return True, _cap_text(f"{rest[0]} 里的 {rest[1]}", cap)

    return False, f"不认识指令「{head}」。\n{USAGE}"


# ---------------------------------------------------------------- 指令识别

GATE_HEADS = {"限流", "限流设置", "gate"}
"""私聊里用来配置闸门的关键词。"""

HELP_HEADS = {"help", "帮助", "指令", "命令"}
"""用来问「你现在都会些什么」的关键词。"""

BARE_HELP_HEADS = {"帮助", "指令", "命令"}
"""其中允许不带 `.` `/` 前缀直接说的那几个 —— 拉丁词不带前缀会把 "help me" 当成指令。"""

_PREFIXES = {".", "/", "／"}

GATE_SUBCOMMANDS = (
    CAP_ALIASES | GROUP_CAP_ALIASES | DEFAULT_ALIASES | VIEW_ALIASES | RESET_ALIASES | ATONLY_ALIASES
)
_SUBS_BY_LEN = sorted(GATE_SUBCOMMANDS, key=len, reverse=True)


def _split_glued(rest: str) -> list[str] | None:
    """切「限流默认5」这种不带空格的写法：先按已知子指令词头吃，剩下的一律当参数。

    只认词表里有的子指令，认不出来的（「限流量很大」）返回 None 交回聊天，
    省得把正常说话当成打错的指令。
    """
    words: list[str] = []
    s = rest.strip()
    while s:
        for sub in _SUBS_BY_LEN:  # 长的优先：默认上限 要抢在 默认 前面
            if s.startswith(sub):
                s = s[len(sub):].strip()
                words.append(sub)
                if s:
                    words.extend(s.split())
                    s = ""
                break
        else:
            return None
    return words or None


def parse_gate_command(
    text: str,
    heads: set[str],
    nicknames: Iterable[str] = (),
    allow_bare: set[str] | None = None,
) -> list[str] | None:
    """把一条消息认成指令 → `[关键词, 参数...]`；不是指令返回 None。

    认这三种写法：`限流 查看`、`.限流 查看`、`/限流 查看`，不带空格的 `限流查看` 也认。
    私聊里 MuiceBot 也会拿昵称呼叫当普通聊天，所以「达妮娅 限流 查看」同样要能认，
    「达妮娅，.help」这种叫完人再打前缀的也认；
    但「@达妮娅 限流 查看」不算 —— 那是群里 @ 的写法，指令只在私聊用。

    `allow_bare` 限定「不带前缀时也认哪些词」；不传则都认。
    帮助指令要把 `help` 挡在裸写之外，否则群里谁喊一句 "help me" 就蹦出指令表。
    """
    s = (text or "").strip()
    if not s:
        return None
    prefixed = s[0] in _PREFIXES
    if prefixed:
        s = s[1:].lstrip()
    for nick in sorted(nicknames, key=len, reverse=True):  # 长名字优先：达妮娅 > 娅
        if nick and s.startswith(nick):
            s = s[len(nick):].lstrip(" \t，,、。!！?？~～")
            break
    if s[:1] in _PREFIXES:  # 「达妮娅，.help」：叫完人再打前缀，那个点是指令前缀不是标点
        prefixed = True
        s = s[1:].lstrip()
    if not s:
        return None

    bare_ok = heads if allow_bare is None else allow_bare

    def ok(head: str) -> bool:
        return prefixed or head in bare_ok

    parts = s.split()
    if parts[0] in heads:
        return parts if ok(parts[0]) else None
    for head in sorted(heads, key=len, reverse=True):
        if s.startswith(head):
            if not ok(head):
                return None
            glued = _split_glued(s[len(head):])
            return [head] + glued if glued else None
    return None


# ---------------------------------------------------------------- 帮助列表

HELP_ROWS: list[tuple[str, str]] = [
    ("help", "看这份指令列表；群内无需 @，每群每分钟最多 5 次"),
    ("status", "今日模型用量、运行时长"),
    ("about", "版本、已加载插件、当前模型"),
    ("whoami", "我自己在群里/私聊的身份"),
    ("refresh", "让模型重生成上一条回复"),
    ("reset", "清空和我的对话记忆"),
    ("undo", "撤回上一轮对话"),
    ("profile <存档名>", "切换消息存档"),
    ("敲 / 抱 / 贴 @用户", "在群聊中与指定用户互动"),
    ("migrate", "从旧数据库迁移（仅主人）"),
    ("load / reload / schedule / chat", "模型与聊天管理（仅主人）"),
    ("kb", "知识库注入：.kb ｜ .kb on ｜ .kb off ｜ .kb 查 <问题>"),
    ("限流 查看 [群号]", "今天每个人被回了几次（仅主人）"),
    ("限流 默认 <次数|不限>", "每人每群每天的回话上限（仅主人）"),
    ("限流 群上限 <群号> <次数|不限>", "给整群设上限（仅主人）"),
    ("限流 上限 <群号> <QQ号> <次数|不限>", "给某群某人单独设（仅主人）"),
    ("限流 清零 [群号 [QQ号]]", "把今天的计数归零（仅主人）"),
    ("限流 群内只@ 开|关", "群里是不是只回 @ 我的消息（仅主人）"),
]


#: 别的插件（`model_pref` 之类）把自己的指令挂进来的地方。
#: `HELP_ROWS` 是自带指令的字面表，运行时只读 —— 往它身上 append 会在重复加载时出现两行。
EXTRA_HELP_ROWS: list[tuple[str, str]] = []


def register_help_rows(rows: Iterable[tuple[str, str]]) -> int:
    """把几条指令挂进帮助表，返回实际新增/改动的条数。

    同一条令词（`模型 查看`）再来一次就**替换说明**而不是追加 —— 插件在导入时注册一遍、
    `on_startup` 再补一遍是这里的常规写法，注册表若会长重复行，`.help` 马上就难看。
    自带指令（在 `HELP_ROWS` 里）一律不进注册表，否则两表并起来每条都出现两次。
    """
    n = 0
    for cmd, desc in rows or ():
        if any(c == cmd for c, _ in HELP_ROWS):
            continue
        for i, (c, _) in enumerate(EXTRA_HELP_ROWS):
            if c == cmd:
                if EXTRA_HELP_ROWS[i][1] != desc:
                    EXTRA_HELP_ROWS[i] = (cmd, desc)
                    n += 1
                break
        else:
            EXTRA_HELP_ROWS.append((cmd, desc))
            n += 1
    return n


def all_help_rows() -> list[tuple[str, str]]:
    """`.help` 该渲染的完整指令表 = 自带 + 各插件注册的。"""
    return list(HELP_ROWS) + list(EXTRA_HELP_ROWS)


#: 图片菜单的分组。命令名与 `HELP_ROWS` / 注册表里的一致；
#: 没被任何组接住的指令会落到「其它」组，避免新插件注册的指令在菜单里消失。
HELP_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("对话", ("help", "status", "about", "whoami", "refresh", "reset", "undo", "profile", "敲", "migrate", "load")),
    ("知识库", ("kb",)),
    ("限流", ("限流 查看", "限流 默认", "限流 群上限", "限流 上限", "限流 清零", "限流 群内只@")),
    ("模型", ("模型", "model", "模型 首选", "模型 次选")),
    ("解析", ("开启解析", "关闭解析", "blogin", "登录b站")),
    ("天气", ("天气", "天气预报")),
    ("游戏活动", ("活动订阅", "活动退订", "活动查询", "活动游戏", "活动列表", "活动过滤", "活动封面", "活动开关", "活动阈值", "活动状态", "活动刷新", "活动私人提醒", "我已知晓")),
    ("Epic", ("喜加一", "Epic 测试")),
    ("终末地", ("授权登陆", "绑定列表", "便签", "抽卡记录", "干员列表", "同步抽卡", "危机合约", "帝江号建设", "日历", "订阅理智", "订阅列表")),
    ("地震", ("地震列表", "地震预警", "地震源状态", "地震测试")),
    ("灾害", ("气象预警", "台风", "海啸", "灾害源状态")),
    ("服务器状态", ("查看状态", "状态卡", "状态卡诊断", "测试通知", "立即检测", "预览日报", "预览周报")),
    ("话题与记忆", ("群话题", "记忆")),
]


def grouped_help_rows(rows: Iterable[tuple[str, str]]) -> list[tuple[str, list[tuple[str, str]]]]:
    """把扁平指令表按 `HELP_GROUPS` 分组，保持各组内原有顺序。

    匹配规则：命令等于组内任一项，或以「组内项 + 空格」开头。
    这样 `限流 默认 <次数>` 会归到限流组，而不需要把带参数的全称列一遍。
    """
    groups: dict[str, list[tuple[str, str]]] = {name: [] for name, _ in HELP_GROUPS}

    def bucket(cmd: str) -> str:
        for name, heads in HELP_GROUPS:
            for head in heads:
                if cmd == head or cmd.startswith(head + " "):
                    return name
        return "其它"

    for cmd, desc in rows:
        groups.setdefault(bucket(cmd), []).append((cmd, desc))

    ordered = [name for name, _ in HELP_GROUPS] + ["其它"]
    return [(name, groups[name]) for name in ordered if groups.get(name)]


def help_footer(state: dict) -> str:
    """菜单页脚：当前真实生效的配置。"""
    mode = "群里只回 @ 我的消息" if state["group_at_only"] else "群里叫名字就会回"
    return (f".help：群内无需 @，每群每 {GROUP_HELP_WINDOW} 秒最多 {GROUP_HELP_LIMIT} 次，超限静默。\n"
            f"普通对话：{mode}｜每人每群每天 {fmt_cap(state['caps']['default'])} 次。")


HELP_PREFIX_GUIDE = "指令以英文句点 . 开头；可先 @机器人，部分指令要求 @"


def render_help(state: dict, rows: Iterable[tuple[str, str]] = HELP_ROWS) -> str:
    """把指令表排成一屏，末尾带上闸门当前的真实行为 —— 列表要说真话。"""
    rows = list(rows)
    width = max((len(c) for c, _ in rows), default=0) + 2
    lines = [f"可用指令（{HELP_PREFIX_GUIDE}）："]
    lines += [f"  {display_help_command(cmd).ljust(width + 1)}{desc}" for cmd, desc in rows]
    lines.append(help_footer(state))
    return "\n".join(lines)


def display_help_command(command: str) -> str:
    return ' / '.join('.' + part.lstrip('.') for part in command.split(' / '))


def _cap_text(who: str, cap: int) -> str:
    return f"{who}已设为不限次数。" if cap == UNLIMITED else f"{who}已设为 {cap} 次/天。"


# ---------------------------------------------------------------- 概览


def describe(state: dict, group: str | None = None) -> str:
    lines = [
        f"日期 {state['day']}｜群内只@：{'开' if state['group_at_only'] else '关'}"
        f"｜默认上限 {fmt_cap(state['caps']['default'])}"
    ]
    if group is None:
        active = sorted(state["counts"].items(), key=lambda kv: -kv[1])[:10]
        lines.append(f"今天有来回的 {len(state['counts'])} 人，最多：")
        lines.extend(f"  {k} {v} 次" for k, v in active)
        if not active:
            lines.append("  （还没有人问过）")
        if state["caps"]["group"] or state["caps"]["member"]:
            lines.append("单独设过的上限：")
            lines.extend(f"  群 {g} → {fmt_cap(c)}" for g, c in state["caps"]["group"].items())
            lines.extend(f"  {mk} → {fmt_cap(c)}" for mk, c in state["caps"]["member"].items())
        return "\n".join(lines)

    g = str(group)
    rows = {k.split("|", 1)[1]: 0 for k in state["counts"] if k.startswith(f"{g}|")}
    rows |= {k.split("|", 1)[1]: 0 for k in state["caps"]["member"] if k.startswith(f"{g}|")}
    for uid in rows:
        rows[uid] = usage(state, g, uid)
    lines.append(f"群 {g}（{len(rows)} 人今天有记录）：")
    for uid, used in sorted(rows.items(), key=lambda kv: -kv[1]):
        cap = resolve_cap(state, g, uid)
        mark = "不限" if cap == UNLIMITED else ("已超限" if used >= cap else "未超限")
        lines.append(f"  {uid} {used}/{fmt_cap(cap)} {mark}")
    if not rows:
        lines.append("  （今天没人被回过）")
    return "\n".join(lines)


def fmt_cap(cap: int) -> str:
    return "不限" if cap == UNLIMITED else str(cap)
