"""Token 消耗统计 —— 旁路记账，供管理面板展示总量与模型排名。

MuiceBot 自带的消息表只存一个总 token 整数，没有模型维度，也拆不出按天，
所以这里在 `on_finish_chat` 钩子里另记一份「天 × 模型」的账。

能看到什么取决于 provider 有没有回报用量：走 OpenAI 兼容接口时多数字段是
`total_tokens`（流式下由最后一个 chunk 带回来）；拿不到就是 -1，本插件只累计
调用次数，token 记 0，不会把账弄脏。
"""

import sys
from pathlib import Path

from nonebot import get_driver, logger
from muicebot.models import Message as ChatMessage
from muicebot.plugin import PluginMetadata
from muicebot.plugin.hook import on_finish_chat

try:
    import token_logic
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import token_logic

__plugin_meta__ = PluginMetadata(
    name='token_usage',
    description='统计 token 消耗与模型排名',
    usage='管理后台查看；指令：token 今日 | token 排名',
)

ROOT = Path(__file__).resolve().parent.parent
LEDGER = token_logic.TokenLedger(ROOT / 'data' / 'token-usage.json')


def current_model() -> str:
    """当前回话用的模型名。

    `muice.model` 是当前 provider 实例；它的 `model` 属性就是发请求时用的名字
    （`model_pref` 也是改这个字段来换模型）。取不到就记「未知模型」。
    """
    try:
        from muicebot.muice import Muice

        muice = Muice()
        name = getattr(getattr(muice, 'model', None), 'model', None)
        if isinstance(name, str) and name.strip():
            return name.strip()
    except Exception:  # noqa: BLE001 - 拿不到名字不影响记账
        pass
    return token_logic.UNKNOWN_MODEL


@on_finish_chat(priority=90)
async def record_usage(message: ChatMessage) -> None:
    """每完成一轮对话记一笔。优先级靠后，等别的钩子都跑完。"""
    try:
        usage = getattr(message, 'usage', -1)
        LEDGER.record(current_model(), int(usage))
    except Exception:  # noqa: BLE001 - 记账永不影响聊天
        logger.debug('[token] 记账失败', exc_info=True)


def summary(days: int = 7):
    return LEDGER.summary(days=days)


@get_driver().on_startup
async def report_once() -> None:
    try:
        data = summary(7)
        totals = data['totals']
        logger.info(
            f'[token] 近 7 天 {totals["week"]} tokens，'
            f'今日 {totals["today"]}；账本 {LEDGER.path}'
        )
    except Exception:  # noqa: BLE001
        logger.debug('[token] 启动汇报失败', exc_info=True)
