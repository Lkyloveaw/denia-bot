"""引用/回复修复 —— 别人用 QQ 的「回复」功能 @ 她时，那条消息原本必死在调模型之前。

线上现场（2026-09-27，群 1108838060，两条都是这样没回的）：

    15:13:38  '[reply:id=1917583215][at:qq=3892894069] oiiaioiiiiai'   ← 闸门放行（真 @ 了）
    15:52:54  '[reply:id=-163983909][at:qq=3892894069] 你还能回来吗？'  ← 闸门放行
    File "…/muicebot/onebot.py", line 624, in handle_supported_adapters
        bot_message += UniMessage("\\n被引用的消息: ") + UniMessage(reply_message)
    TypeError: Unsupported type <class 'nonebot.adapters.onebot.v11.message.MessageSegment'>

原因链：QQ 的「回复」会自动在正文前带一个 @（@ 的是被引用那条的作者），所以
`gate.py` 认得出「在叫她」并放行；但 alconna 给 MuiceBot 的被引用消息是 **OneBot v11
原生 Message**（`uniseg/adapters/onebot11/builder.py:64-69`），MuiceBot 以为它是 UniMsg，
`UniMessage(原生 Message)` 当场抛 TypeError → handler 死掉 → 整条消息一个字都不回。
同一个人 15:54:23 把同一句话**不带引用**重发一遍，15:54:30 就回了。

修法（不动 site-packages）：给 `ReplyRecordExtension.get_reply` 包一层，把 `.msg` 换成
渲染好的字符串。MuiceBot 那句 `isinstance(reply_message, BotMessage)` 于是走 else 分支
（`UniMessage(f"\\n被引用的消息: {…}")`），引用内容照旧进提示词，而且带着署名。
渲染规则在 `nonebot/quote_logic.py`（纯逻辑，`tests/test_quote_logic.py` 直接断言）。

开关在 `nonebot/.env`（改了要重启，插件不热加载）：

    QUOTE_FIX=1    # 0 = 不打这层补丁（回滚用，引用回复会回到「必崩」的老样子）

出问题时看日志里的 `[quote]` 前缀。
"""

from __future__ import annotations

import sys
from pathlib import Path

import nonebot
from nonebot import get_driver, logger
from muicebot.plugin import PluginMetadata

try:
    import prompt_logic as pl
    import quote_logic as ql
except ImportError:  # 不是从 nonebot\ 目录启动的，把工程根补进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import prompt_logic as pl
    import quote_logic as ql

__plugin_meta__ = PluginMetadata(
    name="quote_fix",
    description="把被引用的消息渲染成文字再交给 MuiceBot，别再崩在它拼引用的那一行",
    usage="无需指令；开关在 nonebot/.env 的 QUOTE_FIX",
)

driver = get_driver()
FALLBACK_NICK = "达妮娅"


def _flag(name: str = "quote_fix", default: bool = True) -> bool:
    """读 .env 里的开关。NoneBot 的 DriverConfig 是 extra=allow，自定义键直接落在上面。"""
    try:
        raw = getattr(driver.config, name, None)
    except Exception:  # noqa: BLE001 - 驱动还没起来（测试里直接调这里的函数）
        return default
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "关")


def _bot_nick() -> str:
    names = getattr(driver.config, "muice_nicknames", None) or []
    for n in names:
        if str(n).strip():
            return str(n).strip()
    return FALLBACK_NICK


def _names() -> dict[str, str]:
    """{QQ号: 称呼}，用来把 @ 和被引用作者渲染成名字。跟 prompt_guard 同源。

    机器人自己也在表里 —— 被引用的常常正是她上一条，写成 `@3892894069` 她认不出是在说谁。
    """
    out: dict[str, str] = {}
    try:
        from muicebot.templates.loader import load_templates_config

        cfg = load_templates_config() or {}
        out |= pl.speaker_map(cfg.get("userinfos") or [])
    except Exception as e:  # noqa: BLE001 - 拿不到熟人表只是名字退化，不该影响回复
        logger.debug(f"[quote] 熟人称呼表没读到（用名片兜底）: {e}")
    try:
        for self_id in nonebot.get_bots():
            out.setdefault(str(self_id), _bot_nick())
    except Exception:  # noqa: BLE001
        pass
    return out


def fix_reply(reply):  # noqa: ANN001, ANN201 - 收 uniseg 的 Reply，返回同一个或 None
    """把缓存里的被引用消息收拾成 MuiceBot 能用（且不崩）的样子。

    判断照 MuiceBot 那句来：`isinstance(reply_message, BotMessage)` 为真那一支
    （`UniMessage(框架原生 Message)`）就是 624 行的崩点，只有它需要动。
    返回 None = 让 MuiceBot 当「这条没有引用」处理。`msg` 原本为空也要收成 None：
    留着它会拼出一行 `被引用的消息: None` 给模型看。
    """
    if reply is None:
        return None
    msg = getattr(reply, "msg", None)
    if msg is None:
        logger.debug("[quote] 事件里没有可解析的被引用消息，这条不带引用")
        return None

    from nonebot.adapters import Message as BotMessage

    if not isinstance(msg, BotMessage):
        # 字符串 / UniMessage 都走 MuiceBot 的 else 分支，格式化一下就行，不会崩。
        # （注意 `UniMsg` 从 uniseg 导出来是个 Annotated 依赖别名，不是类，不能拿去 isinstance。）
        return reply if str(msg).strip() else None

    origin = getattr(reply, "origin", None)  # OneBot v11 的 Reply 模型（sender 在里面）
    sender = getattr(origin, "sender", None)
    text = ql.quoted_block(
        msg,
        names=_names(),
        sender_card=str(getattr(sender, "card", "") or ""),
        sender_nickname=str(getattr(sender, "nickname", "") or ""),
        sender_id=str(getattr(sender, "user_id", None) or getattr(origin, "user_id", "") or ""),
    )
    if not text:
        logger.info("[quote] 被引用的那条渲染不出任何文字，这次不带引用")
        return None
    reply.msg = text  # 原地改：缓存里就是这一个对象，第二次取到也不会再走转换
    logger.info(f"[quote] 被引用的消息已渲染 {len(text)} 字：{text.splitlines()[0][:24]}")
    return reply


def install() -> bool:
    """包上 `get_reply`。已经包过就跳过（重启失败/重复加载都不能套两层）。"""
    try:
        from nonebot_plugin_alconna.builtins.extensions.reply import ReplyRecordExtension
    except Exception as e:  # noqa: BLE001 - alconna 换了这个类名必须出声，不能默默失效
        logger.error(f"[quote] 拿不到 ReplyRecordExtension，引用补丁未装上：{e}")
        return False

    orig = ReplyRecordExtension.get_reply
    if getattr(orig, "_quote_fixed", False):
        logger.warning("[quote] 引用补丁已经在了，跳过重复安装")
        return False

    def get_reply(self, message_id):  # noqa: ANN001, ANN201 - 与被替换的签名一致
        return fix_reply(orig(self, message_id))

    get_reply._quote_fixed = True
    ReplyRecordExtension.get_reply = get_reply
    logger.success("[quote] 引用补丁已生效（被引用的消息按「〔发言人:某某〕+ 正文」进提示词）")
    return True


@driver.on_startup
async def install_quote_fix() -> None:
    if not _flag():
        logger.info("[quote] QUOTE_FIX=0，未打引用补丁：引用回复仍会崩在 muicebot/onebot.py:624")
        return
    install()
