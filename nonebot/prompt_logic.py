r"""群聊提示词的「谁在说话」规范化 —— 纯逻辑，不 import nonebot / muicebot。

所以 `tests/test_prompt_logic.py` 秒级能跑完，`plugins/prompt_guard.py` 只负责接线。

## 为什么要有这个文件

MuiceBot 在群聊里把每条消息拼成 `<发送人昵称> 正文` 再发给模型
（`muice.py:174` 当前轮、`muice.py:212` 历史），昵称来自 `nonebot_plugin_userinfo`
（群名片优先，没有就用 QQ 昵称）。QQ 昵称是随便起的 —— 本次线上事故：主人的昵称
正好是「对乙酰氨基酚」，于是「@达妮娅 娅娅」到了模型眼里变成

    <对乙酰氨基酚> 娅娅

而人格模板从头到尾没解释过尖括号是什么，模型就老老实实回答起用药问题了
（回复：「嗯……你是想问对乙酰氨基酚吗？它用于退烧、缓解疼痛……」）。

署名本身是有用的：群里多人同时聊，她得分清哪句是谁说的。所以这里**不删署名，
而是把它变成一个说得出是署名的东西**：

* 换成 `〔发言人:某某〕` + 换行 + 正文 —— 尖括号像标签/代码，方括号配中文键名更像注脚；
* 熟人改用 `configs/templates.yml` 里给他配的称呼（主人 → 漂泊者），怪名字根本不进提示词；
* 名片为空或是一串数字（`nonebot_plugin_userinfo` 拿不到名字时会回落到 QQ 号）时，
  写成 `群友<QQ号>`，模型手里就没剩什么可当成话题的东西了。

## 顺手修掉的另外两件事（同一条链路上的，都在 `_prepare_history`）

1. **同一条消息出现两遍**：`get_user_history` 按 QQ 号查、`get_group_history` 按群查，
   两边的行只有「有没有 `<署名>` 前缀」这一个差别，`muice.py:214` 用 `list(set(...))`
   合并 —— 而 `Message.__hash__` 取的是 `hash(self.id)`，`id` 恒为 `None`（`crud._convert`
   不带 id），于是所有行哈希相同、互相不等，去重一条都没去掉。
2. **顺序是乱的，截断于是也在乱截**：同一句「哈希全撞」让 `set()` 的输出顺序跟时间无关，
   末尾的 `[-max_history_epoch:]` 取到的是任意子集 —— 日志里见过 15:21:42 排在 15:21:39 前面。
   更要紧的是 `get_user_history` 不分群，把人**在别的群、别的私聊**里的对话也带进本群
   （新群的第一句就带着旧群的记录）。

这里的顺序是刻意的：**先剥署名 → 再去重 → 再按时间排 → 再按场景筛 → 最后截断**。
先去重则两份前缀不同的行并不掉；先截断则丢的还是最近那几轮。
筛场景时拿不到当前群号**放行不过滤**：宁可多带点历史，也不能把上下文清空。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, NamedTuple

# 剥署名用：句首的 `<某某>`（MuiceBot 的历史格式）或 `〔发言人:某某〕`（本模块的新格式）。
# 允许空（`<>`）—— 线上真出现过 3 次拿不到名片的情况。只认行首：句中出现的尖括号是
# 人家在说东西（「<对乙酰氨基酚>」当药名提起来），不是署名。
LEADING_LABEL = re.compile(
    r"^(?:<([^<>\n]{0,40})>|〔发言人:([^\n〕]{0,40})〕)[ \t\r\n，,、。.!！?？~～]*",
)

UNKNOWN_PREFIX = "群友"
"""拿不到名片也没配称呼时，用这个前缀 + QQ 号当署名。"""

TIME_FMT = "%Y.%m.%d %H:%M:%S"
"""MuiceBot `Message.time` 的格式（`models.py:68`）。"""


class Turn(NamedTuple):
    """一条历史轮次，跟 muicebot 的 `Message` 解耦，方便脱离框架测。

    `body` 交给进来时**可以还带着署名**，`normalize_turns` 自己会剥；
    `label` 是算出来的署名；`src` 原样揣着上游对象，接线层拿 `copy.copy(src)` 改回去。
    """

    time: str = ""
    userid: str = ""
    groupid: str = "-1"
    body: str = ""
    respond: str = ""
    label: str = ""
    index: int = 0
    src: Any = None


def split_speaker(text: str) -> tuple[str, str]:
    """拆出句首署名和正文，返回 `(label, body)`；没有署名则 label 为空串。

    叠着写两层的（「<a> <b> 你好」）只认第一层，剩下的留给 `strip_leading_labels`。
    """
    s = (text or "").strip()
    label = ""
    while True:
        m = LEADING_LABEL.match(s)
        if not m:
            break
        got = (m.group(1) or m.group(2) or "").strip()
        if got and not label:
            label = got
        s = s[m.end():].strip()
    return label, s


def strip_leading_labels(text: str) -> str:
    """只要正文：把句首一层层署名都剥掉，`@` 和 `[at:qq=…]` 不归这里（kb_logic 管）。"""
    return split_speaker(text)[1]


def is_noise_label(label: str) -> bool:
    """这个署名能不能直接给模型看？空、纯数字（QQ 号回落）都算噪声。"""
    s = (label or "").strip()
    return not s or s.isdigit()


def resolve_speaker(userid: str, raw_label: str, known: dict[str, str] | None = None) -> str:
    """定这一个人在提示词里叫什么：熟人表 > 群名片 > 群友<QQ号>。

    熟人表（`configs/templates.yml` 的 `userinfos`）排在最前面是有意的：
    那个人爱改昵称玩，而「她对主人是什么语气」是人格的一部分，不该跟着名片漂。
    """
    uid = str(userid or "").strip()
    name = ((known or {}).get(uid) or "").strip()
    if name:
        return name
    label = (raw_label or "").strip()
    if label and not is_noise_label(label):
        return label
    return f"{UNKNOWN_PREFIX}{uid}" if uid else ""


def label_turn(label: str, body: str) -> str:
    """拼成给模型看的最终形态：署名独占一行，正文在下面。"""
    if not label:
        return body
    return f"〔发言人:{label}〕\n{body}"


def parse_time(value: str) -> datetime | None:
    """解 MuiceBot 的时间串；解不了返回 None（调用方按最旧排，别抛）。"""
    try:
        return datetime.strptime((value or "").strip(), TIME_FMT)
    except ValueError:
        return None


def group_token(groupid: str) -> str:
    """从落库的会话 id 里取出群号：`qq_OneBot V11_3892894069_922192732` → `922192732`。

    私聊是 `-1`（`muicebot/models.py:75`）。
    """
    s = (groupid or "").strip()
    return s.rsplit("_", 1)[-1] if s else ""


def normalize_turns(
    turns: Iterable[Turn],
    current_group: str | None = None,
    limit: int = 0,
    known: dict[str, str] | None = None,
) -> list[Turn]:
    """整理历史：剥署名、去重、按时间排、按场景筛、截断。顺序的理由见模块开头。

    `current_group`：群聊传本群群号，私聊传 `"-1"`，传 `None` 表示「认不出来，别筛」。
    `limit`：`<= 0` 表示不限制（对齐 `MAX_HISTORY_EPOCH=0` 的语义）。
    """
    out: list[Turn] = []
    seen: set[tuple] = set()
    for pos, t in enumerate(turns):
        raw_label, body = split_speaker(t.body)
        key = (t.time, t.userid, t.groupid, body, t.respond)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            t._replace(
                body=body,
                label=resolve_speaker(t.userid, raw_label or t.label, known),
                index=pos,
            )
        )

    _OLDEST = datetime.min
    out.sort(key=lambda t: (parse_time(t.time) or _OLDEST, t.index))

    if current_group is not None:
        want = str(current_group).strip()
        out = [t for t in out if group_token(t.groupid) == want]

    return out[-limit:] if limit and limit > 0 else out


def speaker_map(userinfos: Iterable[Any]) -> dict[str, str]:
    """把 `templates.yml` 里的 `userinfos` 转成 `{QQ号: 称呼}`。

    收 pydantic 模型也收普通 dict —— 上游给的是哪种取决于它走没走 model_dump。
    """
    out: dict[str, str] = {}
    for row in userinfos or []:
        if isinstance(row, dict):
            uid, name = row.get("id"), row.get("name")
        else:
            uid, name = getattr(row, "id", None), getattr(row, "name", None)
        if str(uid or "").strip() and str(name or "").strip():
            out[str(uid).strip()] = str(name).strip()
    return out


def current_group_id(event: Any) -> str | None:
    """从事件里取本场景该筛成哪一组：群聊给群号，私聊给 `-1`，认不出给 None（放行）。

    先认 `group_id` 这个字段而不是 `get_message_type()` —— OneBot v11 的事件类
    根本没实现后者（基类返回空串），拿它判断会把每条群聊都当成「认不出」，
    于是筛选静默失效、跨群串台照旧（接线测试就是这么抓到的）。
    """
    gid = getattr(event, "group_id", None)
    if gid is None:
        detail = getattr(event, "detail", None)  # OneBot v12 把场景放在 detail 里
        gid = getattr(detail, "group_id", None) if detail is not None else None
    if gid not in (None, ""):
        return str(gid).strip() or None
    mtype = str(getattr(event, "message_type", "") or "").lower()
    if mtype in ("private", "c2c", "direct"):
        return "-1"
    try:
        if str(event.get_message_type()).lower() == "private":  # type: ignore[attr-defined]
            return "-1"
    except Exception:  # noqa: BLE001 - 拿不到就当认不出
        pass
    return None
