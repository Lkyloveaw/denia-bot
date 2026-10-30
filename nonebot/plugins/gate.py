"""群聊闸门 —— 群里只回 @ 我的消息，外加每人每群每天的回复上限。

两个动作分开做：

1. **拦**：`driver.on_startup` 时把 `gate_rule` 与到 MuiceBot 的两个触发器
   （`nickname_event` / `at_event`）的规则上。只有这一层能在**调模型之前**把消息挡掉 ——
   MuiceBot 的钩子没有取消语义，流式回复一旦开跑就已经在往外发了。
2. **数**：`on_finish_chat` 钩子里计数。它只在「模型真的回出了非空内容」之后才跑，
   所以数出来的是「机器人确实回了几次」，不是「收到几条消息」。

上限、日切、指令解析全在 `nonebot/gate_logic.py`（纯逻辑，`tests/test_gate_logic.py` 直接断言），
这个文件只做接线，出问题时看日志里的 `[gate]` 前缀。

指令（主人私聊发，`.限流` / `/限流` / 直接打「限流」都认）：

    限流 查看 [群号]                      今天谁被回了几次
    限流 默认 <次数|不限>                  兜底上限
    限流 群上限 <群号> <次数|不限>         整群上限
    限流 上限 <群号> <QQ号> <次数|不限>    某群某人上限
    限流 清零 [群号 [QQ号]]                手动归零
    限流 群内只@ 开|关                     群里是不是只回 @ 我的消息

「@ 她」怎么算：QQ 点「回复」时会替被引用的那个人自动补一个 @，那一下**也算**在叫她 ——
`[reply:id][at:qq=她]正文` 照样回（2026-09-28 主人改的口径：引用她时这个 @ 和手打的在协议里
长得一样，分不出来，只能选一边）。整条一个 at 段都没有的纯引用回复仍然拦。
开关在 `.env`：`QUOTE_AT_COUNTS=0` 收紧回「得自己再 @ 一次」，改完要重启。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from nonebot import get_driver, logger, on_message
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER
from muicebot.models import Message as ChatMessage  # noqa: F401  (钩子靠类型注解注入)
from muicebot.plugin import PluginMetadata
from muicebot.plugin.hook import on_finish_chat

try:
    import gate_logic as gl
except ImportError:  # 不是从 nonebot\ 目录启动的，把工程根补进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import gate_logic as gl

__plugin_meta__ = PluginMetadata(
    name="gate",
    description="群里只回 @ 的消息；每人每群每天的回复次数可设上限",
    usage="私聊发：限流 查看 | 限流 默认 <次数|不限> | 限流 上限 <群号> <QQ号> <次数|不限> | 限流 清零",
)

driver = get_driver()
STATE_PATH = gl.DEFAULT_PATH


# ---------------------------------------------------------------- 环境与身份


def _nicknames() -> tuple[str, ...]:
    """MuiceBot 的唤醒词，用来认「达妮娅 限流 查看」这种带呼叫的写法。"""
    raw = getattr(driver.config, "muice_nicknames", None) or []
    names = tuple(str(n).strip() for n in raw if str(n).strip())
    return names or ("达妮娅",)


def _superusers() -> set[str]:
    return {str(u) for u in getattr(driver.config, "superusers", None) or set()}


def _text(event: Event) -> str:
    try:
        return event.get_plaintext() or ""
    except Exception:  # noqa: BLE001 - 不是消息事件就没文本
        return ""


def _flag(name: str, default: bool = False) -> bool:
    """.env 里的开关（`QUOTE_AT_COUNTS` 这类）。每次调用现读，测试才换得动。"""
    try:
        raw = getattr(driver.config, name, None)
    except Exception:  # noqa: BLE001 - 驱动没起来（测试里直接调 gate_rule）
        return default
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "关")


def _count_auto_at() -> bool:
    """QQ「回复」自动补的那个 @ 算不算在叫她。默认**算**（`.env` 写 `QUOTE_AT_COUNTS=0` 才收紧）。"""
    return _flag("quote_at_counts", default=True)


def _reply_author(event: Event) -> str | None:
    """被引用那条是谁发的。协议端没给 `reply` 详情就返回 None（只按段位置判）。"""
    sender = getattr(getattr(event, "reply", None), "sender", None)
    uid = getattr(sender, "user_id", None)
    return str(uid) if uid else None


def _is_private(event: Event) -> bool:
    return getattr(event, "message_type", None) == "private"


def _segments(event: Event) -> list[tuple[str, str]]:
    """原始消息段 `(type, qq)`。必须用 `original_message`：`event.message` 里的 @ 会被剥掉。"""
    msg = getattr(event, "original_message", None)
    if msg is None:
        try:
            msg = event.get_message()
        except Exception:  # noqa: BLE001
            return []
    return [(s.type, str(s.data.get("qq", ""))) for s in msg]


# ---------------------------------------------------------------- 状态存取

_state_cache: dict | None = None


def state() -> dict:
    """内存里那份状态。跨天了重读一次盘：counts 自然清零，caps 留着。"""
    global _state_cache
    day = gl.today()
    if _state_cache is None or _state_cache.get("day") != day:
        _state_cache = gl.load_state(STATE_PATH, day=day)
    return _state_cache


def _persist() -> None:
    gl.save_state(STATE_PATH, state())


# ---------------------------------------------------------------- 拦


async def gate_rule(bot: Bot, event: Event) -> bool:
    """挂到 MuiceBot 触发器上的那层规则。返回 False 就是「这条消息根本不调模型」。"""
    try:
        if event.get_type() != "message":
            # MuiceBot 的 Alconna 匹配器不限事件类型，心跳/通知事件也会跑到这条规则上，
            # 而它们连 user_id 都没有。不归闸门管，安静放行。
            return True
        user = str(event.get_user_id())
        group = str(getattr(event, "group_id", "") or "")
        at_self, auto_at = False, False
        if not _is_private(event):
            segs = _segments(event)
            author = _reply_author(event)
            # 「回复」时 QQ 会替被引用的人补一个 @。2026-09-28 主人定的口径是**那也算**：
            # 引用她 + @ 在她名下就是在叫她（这两段在协议里和手打的完全一样，分不出来）。
            # 想退回严格口径（得自己再 @ 一次）在 `.env` 写 `QUOTE_AT_COUNTS=0`，改完重启。
            count_auto = _count_auto_at()
            at_self = gl.mentions_self(segs, bot.self_id, ignore_auto_at=not count_auto,
                                       reply_author=author)
            drop = None if count_auto else gl.auto_at_index(segs, author)
            auto_at = drop is not None and str(segs[drop][1]) == str(bot.self_id)
            if auto_at:
                logger.info("[gate] 这个 @ 是引用自动补的（被引用的就是她），按没 @ 处理；"
                            "要她回请手动 @ 一次")
            elif not at_self and event.is_tome():
                # 群里 to_me 为真却一个 at 段都没有 —— 只有一种情况：引用回复她的消息，
                # 而 QQ 连补 @ 都没补（回复自己发的那条时会这样，见 17:46 实测）。
                logger.info("[gate] 群里 to_me 为真但没有 @ 我的 at 段（引用回复），按没 @ 处理")
        ok, reason = gl.decide(
            state(),
            private=_is_private(event),
            at_self=at_self,
            group=group,
            user=user,
            exempt=user in _superusers(),
            auto_at=auto_at,
        )
        if not ok:
            logger.info(f"[gate] 拦下：{reason}")
        else:
            logger.debug(f"[gate] {reason}")
        return ok
    except Exception as e:  # noqa: BLE001 - 判定本身出错时宁可放行，别让整个机器人装死
        logger.warning(f"[gate] 判定出错，这条先放行：{type(e).__name__}: {e}")
        return True


@driver.on_startup
async def install_gate() -> None:
    """把闸门挂上去。MuiceBot 升级后内部名字若变了，这里必须出声，不能默默失效。"""
    try:
        from muicebot.onebot import at_event, nickname_event
    except Exception as e:  # noqa: BLE001
        logger.error(f"[gate] 拿不到 MuiceBot 的触发器，闸门未装上，群里仍会随意插话：{e}")
        return

    for label, matcher in (("nickname_event", nickname_event), ("at_event", at_event)):
        if gate_rule in {c.call for c in matcher.rule.checkers}:
            logger.warning(f"[gate] {label} 上已经有闸门了，跳过重复安装")
            continue
        matcher.rule = matcher.rule & gate_rule
        logger.info(f"[gate] 已挂到 {label}（现在 {len(matcher.rule.checkers)} 条规则）")

    s = state()
    owners = _superusers()
    logger.success(
        "[gate] 闸门已生效"
        f"｜群里只@：{'开' if s['group_at_only'] else '关'}"
        f"｜引用补的 @：{'算' if _count_auto_at() else '不算'}"
        f"｜每人每群每天 {gl.fmt_cap(s['caps']['default'])} 次"
        f"｜状态文件 {STATE_PATH}"
    )
    if not owners:
        logger.warning("[gate] SUPERUSERS 是空的：没人能改设置，也没人豁免 @ 与上限")


# ---------------------------------------------------------------- 数


@on_finish_chat(priority=50)
async def count_group_reply(event: Event, message: ChatMessage) -> None:
    """机器人回完了，才记一次数。私聊不记 —— 限流管的是群里刷屏。"""
    try:
        if getattr(event, "message_type", None) != "group":
            return
        group = str(getattr(event, "group_id", "") or "")
        user = str(event.get_user_id())
        if not group:
            return
        s = state()
        used = gl.bump(s, group, user)
        _persist()
        cap = gl.resolve_cap(s, group, user)
        tail = "（已到达上限，之后不再回）" if cap != gl.UNLIMITED and used >= cap else ""
        logger.info(f"[gate] 群 {group} 的 {user}：今天 {used}/{gl.fmt_cap(cap)} 次{tail}")
    except Exception as e:  # noqa: BLE001 - 计数失败绝不能连累回复
        logger.warning(f"[gate] 计数失败：{type(e).__name__}: {e}")


# ---------------------------------------------------------------- 私聊指令


def _gate_args(event: Event) -> list[str] | None:
    if not _is_private(event):
        return None
    return gl.parse_gate_command(_text(event), gl.GATE_HEADS, _nicknames())


async def is_gate_command(event: Event) -> bool:
    return _gate_args(event) is not None


def run_command(args: list[str]) -> str:
    """执行一条设置指令（args 已去掉「限流」本身），改了就落盘，回一句给人看的话。"""
    changed, reply = gl.apply_command(state(), args)
    if changed:
        _persist()
    logger.info(f"[gate] 指令 {'限流 ' + ' '.join(args)} → {'已改' if changed else '未改'}")
    return reply


gate_cmd = on_message(rule=is_gate_command, priority=1, block=True, permission=SUPERUSER)


@gate_cmd.handle()
async def handle_gate_command(event: Event) -> None:
    args = _gate_args(event) or []
    await gate_cmd.finish(run_command(args[1:]))


# ---------------------------------------------------------------- 指令列表


def help_text() -> str:
    """`.help` 的纯文本版：渲染图片失败时兜底用。"""
    return gl.render_help(state(), gl.all_help_rows())


def help_image():
    """`.help` 的图片菜单；失败返回 None 由调用方回退文本。"""
    import help_image as hi

    return hi.render_help_image(
        gl.grouped_help_rows(gl.all_help_rows()),
        title='达妮娅 · 指令菜单',
        subtitle='写成 .指令 或 /指令；私聊直接打词也行',
        footer=gl.help_footer(state()),
    )


async def is_help_command(event: Event) -> bool:
    return (
        gl.parse_gate_command(
            _text(event), gl.HELP_HEADS, _nicknames(), allow_bare=gl.BARE_HELP_HEADS
        )
        is not None
    )


# 优先级必须小于 MuiceBot 自己那条 command_help（90），block=True 才会拦得住它
help_cmd = on_message(rule=is_help_command, priority=80, block=True)


@help_cmd.handle()
async def handle_help() -> None:
    # 图片菜单优先；渲染或发送失败就退回纯文本，绝不能不回。
    # 注意不能把 matcher.finish() 放进 try：它抛的 FinishedException 继承 Exception，
    # 会被当成「发送失败」吃掉，结果两条路都不生效、消息一条也不发。
    path = None
    try:
        path = await asyncio.to_thread(help_image)
    except Exception:  # noqa: BLE001 - 渲染炸了就走文本
        logger.debug('[gate] 帮助菜单图片渲染失败，回退文本', exc_info=True)

    if path is not None:
        from nonebot.adapters.onebot.v11 import MessageSegment
        from nonebot.exception import FinishedException

        logger.debug(f'[gate] 帮助菜单图片：{path}')
        try:
            await help_cmd.finish(MessageSegment.image(path))
        except FinishedException:
            raise          # 正常结束，必须放行，否则两条路都发不出去
        except Exception as exc:  # noqa: BLE001 - 真的发送失败才回退文本
            logger.warning(f'[gate] 帮助菜单图片发送失败（{type(exc).__name__}: {exc}），回退文本')
    await help_cmd.finish(help_text())
