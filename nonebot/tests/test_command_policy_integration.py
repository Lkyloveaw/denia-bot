"""Exercise the installed rules in a fresh process with the real bot entry point."""
import subprocess
import sys
from pathlib import Path


def test_real_bot_installs_rules_and_builtin_dot_commands():
    root = Path(__file__).resolve().parents[1]
    script = '''
import bot
import asyncio
from nonebot import get_driver
from command_policy import has_command_prefix, denied_command_fallback
from nonebot.matcher import matchers
from muicebot.builtin_plugins import moe_chat
from nonebot_plugin_alconna.uniseg import At, Text
from plugins import endfield, gate, kb, media_parser, weather
from tests.endfield.test_wiring import group_event, private_event

assert get_driver().config.command_start == {'.'}
assert any(any(check.call is denied_command_fallback for check in matcher.rule.checkers)
           and matcher.block for matcher in matchers[98])
async def check_rules():
    assert await endfield.matcher.rule(None, private_event('.授权登陆', user='10000001'), {})
    assert not await endfield.matcher.rule(None, private_event('授权登陆', user='10000001'), {})
    endfield._pending_consent.add('10000001')
    try:
        assert await endfield.matcher.rule(None, private_event('.同意', user='10000001'), {})
        assert await endfield.matcher.rule(None, private_event('.取消', user='10000001'), {})
        assert not await endfield.matcher.rule(None, private_event('.同意', user='123456'), {})
        assert not await endfield.matcher.rule(None, group_event('.同意', at_self=True, user='10000001'), {})
    finally:
        endfield._pending_consent.discard('10000001')
    assert '「.同意」' in endfield.logic.consent_text()
    assert '「.取消」' in endfield.logic.consent_text()
    assert await endfield.matcher.rule(None, private_event('.便签'), {})
    assert not await endfield.matcher.rule(None, private_event('便签'), {})
    assert not await endfield.matcher.rule(None, private_event(' .便签'), {})
    assert await endfield.matcher.rule(None, group_event('.便签', at_self=True), {})
    assert not await endfield.matcher.rule(None, group_event('.便签'), {})
    assert await gate.help_cmd.rule(None, private_event('.help'), {})
    assert not await gate.help_cmd.rule(None, private_event(' /help'), {})
asyncio.run(check_rules())
for matcher in (endfield.matcher, gate.help_cmd, kb.kb, media_parser.control, weather.matcher,
                moe_chat.command_poke, moe_chat.command_hug, moe_chat.command_stick):
    assert any(check.call is has_command_prefix for check in matcher.rule.checkers)
for matcher, name in ((moe_chat.command_poke, '敲'), (moe_chat.command_hug, '抱'),
                      (moe_chat.command_stick, '贴')):
    command = matcher.command()
    assert command.parse([Text('.' + name + ' '), At('user', '123')]).matched
    assert not command.parse([Text('/' + name + ' '), At('user', '123')]).matched
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=root, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_chat_matchers_silence_unhandled_commands():
    root = Path(__file__).resolve().parents[1]
    script = '''
import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, patch

import bot
from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import StopPropagation
from nonebot.message import _run_matcher, check_and_run_matcher
from nonebot_plugin_alconna.uniseg import UniMessage
from nonebot_plugin_alconna.uniseg.constraint import UNISEG_MESSAGE
from muicebot.onebot import at_event, nickname_event
from plugins import endfield, gate
from tests.endfield.test_wiring import private_event

robot = Bot(get_driver()._adapters['OneBot V11'], '3892894069')

async def check():
    for matcher in (at_event, nickname_event):
        for text in ('.unknown', '/unknown', '.restricted'):
            with patch.object(matcher, 'run', new_callable=AsyncMock) as run:
                with suppress(StopPropagation):
                    await _run_matcher(matcher, robot, private_event(text, user='10000001'),
                                       {UNISEG_MESSAGE: UniMessage(text)})
                assert not run.await_count, (matcher, text)
        with patch.object(matcher, 'run', new_callable=AsyncMock) as run:
            with suppress(StopPropagation):
                await _run_matcher(matcher, robot, private_event('你好'),
                                   {UNISEG_MESSAGE: UniMessage('你好')})
            assert run.await_count == 1, matcher
    for user in ('10000001', '2000'):
        for text in ('.unknown', '/unknown', '.restricted'):
            with patch.object(at_event, 'run', new_callable=AsyncMock) as run:
                with suppress(StopPropagation):
                    await check_and_run_matcher(at_event, robot, private_event(text, user=user), {})
                assert not run.await_count, (user, text)
    for matcher, text in ((gate.help_cmd, '.help'), (endfield.matcher, '.授权登陆')):
        with patch.object(matcher, 'run', new_callable=AsyncMock) as run:
            with suppress(StopPropagation):
                await check_and_run_matcher(matcher, robot, private_event(text, user='10000001'), {})
            assert run.await_count == 1, (matcher, text)

asyncio.run(check())
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=root, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
