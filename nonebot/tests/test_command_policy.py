from types import SimpleNamespace

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from command_policy import has_command_prefix


def event(message):
    return SimpleNamespace(original_message=Message(message), self_id='123')


@pytest.mark.parametrize('body', ['.help', '.天气 北京'])
def test_dot_starts_original_message(body):
    assert has_command_prefix(event(body))


@pytest.mark.parametrize('body', ['help', '/help', ' .help', '\n.help', '昵称 .help', '。help'])
def test_rejects_other_prefixes(body):
    assert not has_command_prefix(event(body))


def test_at_self_can_precede_command():
    assert has_command_prefix(event(MessageSegment.at('123') + MessageSegment.text(' .便签')))
    assert not has_command_prefix(event(MessageSegment.at('456') + MessageSegment.text(' .便签')))
    assert not has_command_prefix(event(MessageSegment.at('123') + MessageSegment.text(' x .便签')))
    assert not has_command_prefix(event(MessageSegment.image('https://example.invalid/a') + MessageSegment.text('.help')))
    assert not has_command_prefix(event(MessageSegment.reply('1') + MessageSegment.text('.help')))


def test_help_displays_prefix_for_alternatives():
    from gate_logic import display_help_command
    assert display_help_command('便签 / 理智 / 签到') == '.便签 / .理智 / .签到'
