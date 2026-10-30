"""群聊署名规范化 + 历史整理 —— 拦在调模型之前，把 MuiceBot 拼好的提示词修干净。

    Muice 建好 `ModelRequest` → 跑 BEFORE_MODEL_COMPLETION 钩子 → **把同一个对象**交给模型
    （`muice.py:255-262`，流式路径 `muice.py:325-332` 同理）。
    钩子按 priority **升序**跑（`plugin/hook/manager.py:92`），所以 `priority=1` 稳在
    kb 的 `5` 之前 —— 署名在这里规范化完，kb 拿到的才是干净问句。

三件事（根因都在 `交接文档` §16 排查过的那次「答非所问」里）：

1. `<对乙酰氨基酚> 娅娅` 这类**群名片被当成话题**：改写成 `〔发言人:漂泊者〕` + 换行 + 正文，
   熟人用 `configs/templates.yml` 里配的称呼，名片空/是 QQ 号时写 `群友<QQ号>`。
2. 同一条历史出现两遍、顺序还是乱的（`muice.py:214` 的 `list(set(...))`）：重排 + 去重。
3. 别的群、别的私聊的对话漏进本群（`get_user_history` 不分群）：按场景筛。

策略全在 `nonebot/prompt_logic.py`（纯逻辑，`tests/test_prompt_logic.py` 直接断言），
这个文件只做接线。出问题时看日志里的 `[guard]` 前缀 —— **必须看这行**：
MuiceBot 自己那句 `模型调用参数：Prompt: ..., History: ...` 打在钩子之前（`muice.py:259`），
它反映不了这里的任何改动。

开关在 `nonebot/.env`（改了要重启，插件不热加载）：

    PROMPT_GUARD=1          # 0 = 整个插件不动提示词（回滚用）
    PROMPT_GUARD_ISOLATE=1  # 0 = 历史不按场景筛，跨群/跨私聊记忆照带
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from nonebot import get_driver, logger
from nonebot.adapters import Event  # 钩子的注入锚点，别删（见 guard 的签名注释）
from muicebot.llm import ModelRequest
from muicebot.models import Message as ChatMessage  # noqa: F401  (钩子靠类型注解注入)
from muicebot.plugin import PluginMetadata
from muicebot.plugin.hook import on_before_completion

try:
    import prompt_logic as pl
except ImportError:  # 不是从 nonebot\ 目录启动的，把工程根补进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import prompt_logic as pl

__plugin_meta__ = PluginMetadata(
    name="prompt_guard",
    description="群聊署名规范化（别让 QQ 昵称当话题）+ 历史去重/排序/按场景隔离",
    usage="无需指令；开关在 nonebot/.env 的 PROMPT_GUARD / PROMPT_GUARD_ISOLATE",
)

NOTE = (
    "\n\n【群聊消息的读法】群里的消息第一行是〔发言人:某某〕，第二行才是他说的话。"
    "方括号只是署名，不是话题，也不是要你对暗号：只看第二行。"
    "名字可能很怪（药名、外号、一串数字），那都只是别人给他起的称呼 —— "
    "不复述、不追问、不因为它开新话题；只有对方自己提起这个称呼时才管它。"
    "「〔发言人:对乙酰氨基酚〕在吗」是在问你在不在，不是在问药。"
)


def _flag(name: str, default: bool = True) -> bool:
    """读 .env 里的开关。NoneBot 的 DriverConfig 是 extra=allow，自定义键直接落在上面。"""
    try:
        raw = getattr(get_driver().config, name, None)
    except Exception:  # noqa: BLE001 - 驱动还没起来（测试里直接调钩子函数）
        return default
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "关")


def _max_history() -> int:
    try:
        return int(getattr(get_driver().config, "max_history_epoch", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _known_names() -> dict[str, str]:
    """`configs/templates.yml` 里的 userinfos → {QQ号: 称呼}。每次读，改文件即生效。"""
    try:
        from muicebot.templates.loader import load_templates_config

        cfg = load_templates_config() or {}
        return pl.speaker_map(cfg.get("userinfos") or [])
    except Exception as e:  # noqa: BLE001 - 拿不到熟人表最多退化成群名片，不该影响回复
        logger.debug(f"[guard] 熟人称呼表没读到（用群名片兜底）: {e}")
        return {}


def _label_of(msg: ChatMessage, known: dict[str, str]) -> str:
    """某条历史里这个人叫什么（测试和日志用）。"""
    return pl.resolve_speaker(msg.userid, pl.split_speaker(msg.message or "")[0], known)


@on_before_completion(priority=1)
async def guard(request: ModelRequest, event: Event) -> None:
    """`event` 必须写成 `Event` 注解 —— MuiceBot 的钩子是按**类型注解**注入参数的
    （`plugin/hook/manager.py:53` 的 `if not param_type: continue`）。
    少写注解不是「注入不到」而是**根本不传**，于是调用点变成 `guard(request=...)`，
    当场 `TypeError: missing 1 required positional argument: 'event'`；
    这条异常出在 `ask_stream` 里、由后台任务消化，日志上什么痕迹都不留，
    表现是「闸门放行了，但模型一次都没被调用」。2026-09-27 16:11-17:2x 就是这么全线静默的。
    """
    if not _flag("prompt_guard"):
        return

    known = _known_names()

    group = pl.current_group_id(event)
    is_group = bool(group) and group != "-1"
    isolate = _flag("prompt_guard_isolate")
    want_group = group if (isolate and group is not None) else None

    # 1) 历史：剥署名 → 去重 → 按时间排 → 按场景筛 → 截断
    before = list(request.history or [])
    turns = [
        pl.Turn(
            time=m.time or "",
            userid=str(m.userid or ""),
            groupid=str(m.groupid or "-1"),
            body=m.message or "",
            respond=m.respond or "",
            src=m,
        )
        for m in before
    ]
    kept = pl.normalize_turns(turns, current_group=want_group, limit=_max_history(), known=known)
    if want_group is not None and not kept and turns:
        # 一条都不剩 = 场景筛选把上下文清空了。落库的 groupid 格式万一跟预期不一致，
        # 代价不该是「她突然什么都不记得」—— 放行不过滤，并且把这行留成证据。
        logger.warning(f"[guard] 按场景 {want_group} 筛完历史为空（原有 {len(turns)} 条），本次不筛")
        kept = pl.normalize_turns(turns, current_group=None, limit=_max_history(), known=known)
    labelled: list[ChatMessage] = []
    for t in kept:
        m = copy.copy(t.src)
        # 私聊是一对一，署名是纯噪声；群聊才需要分清谁说的
        m.message = pl.label_turn(t.label, t.body) if is_group else t.body
        labelled.append(m)
    request.history = labelled

    # 2) 当前这句。认不出署名就不动 —— `template_mode: user` 时正文前还压着一段指令，
    #    别把那段当成署名吃了（当前配置是 system，走不到这条，留着当保险）。
    raw_prompt = request.prompt or ""
    label, body = pl.split_speaker(raw_prompt)
    me = ""
    if raw_prompt.strip() != body:
        # 句首确实有个署名 —— 哪怕它是空的（userinfo 拿不到名字时上游会拼出 `<> 正文`），
        # 那种也得换掉：留着 `<>` 模型只会更困惑。
        me = pl.resolve_speaker(str(event.get_user_id()), label, known)
        request.prompt = pl.label_turn(me, body) if is_group else body

    # 3) 给模型补一句「方括号只是署名」
    if NOTE not in (request.system or ""):
        request.system = (request.system or "") + NOTE

    dropped = len(turns) - len(kept)
    span = f" {kept[0].time[11:]}→{kept[-1].time[11:]}" if kept else ""
    scope = "私聊" if group == "-1" else (f"群{group}" if group else "未识别(不过滤)")
    logger.info(
        f"[guard] {scope} 署名={me or '—'} 历史 {len(turns)}→{len(kept)}"
        f"（去掉重复/越界 {max(dropped, 0)} 条）{span}"
    )

