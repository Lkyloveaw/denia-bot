"""OneBot entry point for the adapted upstream link parser."""

import sys
from pathlib import Path

from muicebot.plugin import PluginMetadata
from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment

from parser_logic import session_key

__plugin_meta__ = PluginMetadata(name='media_parser', description='多平台链接解析', usage='管理员：开启解析 / 关闭解析 / blogin')

driver = get_driver()
runtime = None
deps = Path(__file__).resolve().parent.parent / 'parser_deps'
if deps.is_dir() and str(deps) not in sys.path:
    sys.path.append(str(deps))


@driver.on_startup
async def start_parser():
    global runtime
    try:
        from media_parser.runtime import ParserRuntime
        runtime = ParserRuntime()
        await runtime.start()
    except Exception:
        logger.exception('[parser] 初始化失败，链接解析不可用')
        if runtime:
            await runtime.close()
        runtime = None


@driver.on_shutdown
async def stop_parser():
    if runtime:
        await runtime.close()


def command_text(event):
    text = event.get_plaintext().strip()
    return text.lstrip('./').strip().lower()


async def is_control(event: MessageEvent):
    return command_text(event) in {'开启解析', '关闭解析', 'blogin', '登录b站'}


control = on_message(rule=is_control, priority=1, block=True)


@control.handle()
async def control_parser(bot: Bot, event: MessageEvent):
    owners = {str(v) for v in getattr(driver.config, 'superusers', set())}
    role = getattr(event.sender, 'role', '')
    if str(event.user_id) not in owners and not (getattr(event, 'group_id', None) is not None and role in {'owner', 'admin'}):
        await control.finish('只有管理员可以使用这个指令。')
    if not runtime:
        await control.finish('解析器未就绪，请查看机器人日志。')
    command = command_text(event)
    if command in {'开启解析', '关闭解析'}:
        if command == '开启解析':
            runtime.cfg.remove_blacklist(session_key(event))
        else:
            runtime.cfg.add_blacklist(session_key(event))
        await control.finish('当前会话的解析已开启' if command == '开启解析' else '当前会话的解析已关闭')
    parser = runtime.bilibili()
    if parser is None:
        await control.finish('B站解析器未启用。')
    try:
        code = await parser.login.login_with_qrcode()
        await bot.send(event, MessageSegment.image(code))
        async for status in parser.login.check_qr_state():
            await bot.send(event, status)
    except Exception:
        logger.exception('[parser] B站登录失败')
        await control.finish('B站登录失败，请查看机器人日志。')


async def is_candidate(event: MessageEvent):
    return runtime is not None and runtime.match(event) is not None


matcher = on_message(rule=is_candidate, priority=4, block=False)


@matcher.handle()
async def parse_message(bot: Bot, event: MessageEvent):
    match = runtime.match(event) if runtime else None
    if match:
        logger.info(f'[parser] 已匹配 {runtime.parsers[match[0]].platform.name}，开始解析')
        runtime.dispatch(bot, event, match)
