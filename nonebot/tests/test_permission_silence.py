import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests._bootstrap import ensure_nonebot

ensure_nonebot()

from command_policy import denied_command_fallback
from game_event_store import Store
from nonebot.adapters.onebot.v11 import MessageSegment
from plugins import disaster_warning, endfield, game_events, media_parser, statuscard
from tests.endfield.test_wiring import group_event, private_event
from tests.test_game_event_plugin import bot, event


@pytest.mark.parametrize('command', ['.地震源状态', '.灾害源状态', '.地震测试'])
def test_disaster_permission_denied_even_when_service_unavailable(command):
    with patch.object(disaster_warning, '_is_admin', return_value=False), \
         patch.object(disaster_warning, '_runtime', None), \
         patch.object(disaster_warning.matcher, 'finish', new_callable=AsyncMock) as finish:
        asyncio.run(disaster_warning._handle_command(group_event(command)))
    finish.assert_not_awaited()


def test_status_and_parser_do_not_send_or_execute():
    with patch.object(statuscard, '_is_admin', return_value=False), \
         patch.object(statuscard.matcher, 'finish', new_callable=AsyncMock) as finish, \
         patch.object(statuscard, 'send_status_card', new_callable=AsyncMock) as send:
        asyncio.run(statuscard.handle_command(None, group_event('.查看状态')))
    finish.assert_not_awaited()
    send.assert_not_awaited()
    with patch.object(media_parser, 'is_superuser', return_value=False), \
         patch.object(media_parser.control, 'finish', new_callable=AsyncMock) as finish:
        asyncio.run(media_parser.control_parser(None, group_event('.开启解析')))
    finish.assert_not_awaited()


@pytest.mark.parametrize('command', ['.订阅公告', '.取消订阅公告'])
def test_endfield_group_announcement_permission_denied_before_configuration(command):
    with patch.object(endfield, '_superusers', return_value=set()), \
         patch.object(endfield, '_send', new_callable=AsyncMock) as send, \
         patch.object(endfield, '_config') as config:
        asyncio.run(endfield._handle_endfield(group_event(command, at_self=True)))
    send.assert_not_awaited()
    config.assert_not_called()


@pytest.mark.parametrize('command', ['活动状态', '活动刷新', '活动订阅 鸣潮', '活动私人提醒 移除'])
def test_game_events_permission_denied_has_no_reply_or_mutation(tmp_path, command):
    store = Store(tmp_path / 'db')
    robot = bot()
    with patch.object(game_events, 'store', store), patch.object(game_events, 'is_admin', return_value=False):
        asyncio.run(game_events.handle(robot, event(command)))
    robot.send_group_msg.assert_not_awaited()
    assert store.subscriptions() == [] and store.urgent_targets() == []


def test_other_users_reminder_cannot_be_changed_or_replied_to(tmp_path):
    store = Store(tmp_path / 'db')
    robot = bot()
    message = event('活动私人提醒 添加 鸣潮', role='admin')
    message.message.append(MessageSegment.at(3000))
    with patch.object(game_events, 'store', store), patch.object(game_events, 'is_admin', return_value=False):
        asyncio.run(game_events.handle(robot, message))
    robot.send_group_msg.assert_not_awaited()
    assert store.urgent_targets() == []


@pytest.mark.parametrize('command', ['.限流 查看', '.模型 查看', '.群话题 开 123', '.load',
                                     '.reload', '.schedule', '.model list', '.chat', '.migrate', '.store list'])
def test_permission_fallback_consumes_restricted_commands(command):
    driver = ensure_nonebot()
    with patch.object(driver.config, 'superusers', {'1000'}):
        assert asyncio.run(denied_command_fallback(private_event(command, user='2000')))
        assert not asyncio.run(denied_command_fallback(private_event(command, user='1000')))


def test_permission_fallback_preserves_public_and_unknown_commands():
    driver = ensure_nonebot()
    with patch.object(driver.config, 'superusers', {'1000'}):
        for item in (group_event('.群话题 查看', user='2000'), private_event('.help', user='2000'),
                     private_event('.unknown', user='2000'), private_event('模型 查看', user='2000')):
            assert not asyncio.run(denied_command_fallback(item))
