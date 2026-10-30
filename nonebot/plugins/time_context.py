"""Expose the current time and historical turn times to the model."""

import copy
from datetime import datetime, timedelta, timezone

from muicebot.llm import ModelRequest
from muicebot.plugin import PluginMetadata
from muicebot.plugin.hook import on_before_completion

__plugin_meta__ = PluginMetadata(name='time_context', description='为对话提供时间上下文', usage='自动生效')
BEIJING = timezone(timedelta(hours=8))


@on_before_completion(priority=8)
async def add_time_context(request: ModelRequest) -> None:
    now = datetime.now(BEIJING).strftime('%Y.%m.%d %H:%M:%S')
    request.system = (request.system or '') + (
        f'\n当前北京时间：{now}。历史对话的时间标注适用于该轮用户发言及回复；'
        '结合当前时间判断先后和间隔，时间未知时不要猜测，避免把久前的对话说成刚刚发生。'
    )
    history = []
    for item in request.history or []:
        message = copy.copy(item)
        try:
            stamp = datetime.strptime(item.time or '', '%Y.%m.%d %H:%M:%S').strftime('%Y.%m.%d %H:%M:%S')
            label = f'北京时间 {stamp}'
        except (TypeError, ValueError):
            label = '时间未知'
        message.message = f'〔对话时间：{label}〕\n{item.message or ""}'
        history.append(message)
    request.history = history
