import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from tests._bootstrap import ensure_nonebot
except ImportError:
    from _bootstrap import ensure_nonebot
ensure_nonebot(superusers={'1000'})

from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment
from game_event_store import Store
from plugins import game_events as plugin


def event(text, *, private=False, user=2000, role='member'):
    args = dict(time=1800000000, self_id=100, post_type='message', user_id=user,
                message_id=1, message=Message(MessageSegment.text(text)), raw_message=text,
                font=0, sender={'nickname': '测试', 'role': role}, to_me=False)
    return (PrivateMessageEvent(message_type='private', sub_type='friend', **args) if private else
            GroupMessageEvent(message_type='group', sub_type='normal', group_id=555, **args))


def bot():
    return SimpleNamespace(self_id='100', send_group_msg=AsyncMock(), send_private_msg=AsyncMock(),
                           send_group_forward_msg=AsyncMock(), send_private_forward_msg=AsyncMock())


def test_subscription_errors_show_usable_commands(tmp_path):
    store = Store(tmp_path / 'db')
    robot = bot()
    with patch.object(plugin, 'store', store):
        asyncio.run(plugin.handle(robot, event('活动退订', private=True)))
        assert '.活动退订 全部' in str(robot.send_private_msg.call_args.kwargs['message'])
        asyncio.run(plugin.handle(robot, event('活动过滤 查看', private=True)))
        assert '.活动订阅 <游戏>' in str(robot.send_private_msg.call_args.kwargs['message'])


def test_blocked_command_is_consumed_without_reply_or_service(tmp_path):
    store = Store(tmp_path / 'db')
    store.save_settings({'blacklist': ['group:555']}, 0)
    robot = bot()
    with patch.object(plugin, 'store', store), patch.object(plugin, '_service', None):
        assert asyncio.run(plugin._rule(event('活动查询 鸣潮')))
        asyncio.run(plugin.handle(robot, event('活动查询 鸣潮')))
    robot.send_group_msg.assert_not_awaited()
    assert plugin.matcher.block


def test_private_user_can_subscribe_but_group_member_cannot(tmp_path):
    store = Store(tmp_path / 'db')
    with patch.object(plugin, 'store', store):
        asyncio.run(plugin.handle(bot(), event('活动订阅 鸣潮')))
        assert store.subscriptions() == []
        asyncio.run(plugin.handle(bot(), event('活动订阅 鸣潮', private=True)))
    assert store.subscriptions()[0]['kind'] == 'private'


def test_superuser_permission_is_live():
    driver = ensure_nonebot()
    before = driver.config.superusers
    try:
        driver.config.superusers = {'1000'}
        assert not plugin.is_admin(event('活动状态', user=2000))
        driver.config.superusers = {'2000'}
        assert plugin.is_admin(event('活动状态', user=2000))
    finally:
        driver.config.superusers = before


def test_stop_works_silently_in_blocked_group(tmp_path):
    store = Store(tmp_path / 'db')
    store.save_urgent({'bot_id': '100', 'user_id': '2000', 'games': ['ww'], 'origin_session': 'group:555'})
    store.save_settings({'blacklist': ['group:555']}, 0)
    robot = bot()
    with patch.object(plugin, 'store', store):
        asyncio.run(plugin.handle(robot, event('活动私人提醒 停止')))
    assert not store.urgent_target('100', '2000')['enabled']
    robot.send_group_msg.assert_not_awaited()


def test_sender_rechecks_after_build_and_uses_exact_bot(tmp_path):
    store = Store(tmp_path / 'db')
    robot = bot()
    valid = True
    async def build(*args, **kwargs):
        nonlocal valid
        valid = False
        return {'parts': [{'type': 'text', 'data': {'text': 'hello'}}], 'fallback': 'hello', 'nodes': []}
    builder = SimpleNamespace(build=build)
    with patch.object(plugin, 'store', store), patch.object(plugin, '_builder', builder), patch.object(plugin, 'get_bots', return_value={'100': robot}):
        import pytest
        with pytest.raises(plugin.DeliveryBlocked):
            asyncio.run(plugin.send_events(bot_id='100', kind='group', target_id='555', events=[], title='x',
                                           mode='off', settings=store.settings(), valid=lambda: valid))
        with pytest.raises(plugin.DeliveryBlocked):
            asyncio.run(plugin.send_events(bot_id='999', kind='group', target_id='555', events=[], title='x',
                                           mode='off', settings=store.settings(), valid=lambda: True))
    robot.send_group_msg.assert_not_awaited()


def test_sender_timeout_never_falls_back(tmp_path):
    store = Store(tmp_path / 'db')
    robot = bot()
    robot.send_group_msg.side_effect = TimeoutError('unknown receipt')
    builder = SimpleNamespace(build=AsyncMock(return_value={
        'parts': [{'type': 'text', 'data': {'text': '[CQ:at,qq=all]'}}], 'fallback': 'fallback', 'nodes': []}))
    with patch.object(plugin, 'store', store), patch.object(plugin, '_builder', builder), patch.object(plugin, 'get_bots', return_value={'100': robot}):
        import pytest
        with pytest.raises(TimeoutError):
            asyncio.run(plugin.send_events(bot_id='100', kind='group', target_id='555', events=[], title='x',
                                           mode='image', settings=store.settings(), valid=lambda: True))
    robot.send_group_msg.assert_awaited_once()
    assert robot.send_group_msg.call_args.kwargs['message'][0].type == 'text'


def test_instance_lock_has_single_owner(tmp_path):
    first = plugin.InstanceLock(tmp_path / 'lock')
    second = plugin.InstanceLock(tmp_path / 'lock')
    assert first.acquire()
    assert not second.acquire()
    first.close()
    assert second.acquire()
    second.close()


def test_explicit_rejection_falls_back_to_text_once(tmp_path):
    from nonebot.adapters.onebot.v11.exception import ActionFailed
    robot = bot()
    robot.send_group_msg.side_effect = [ActionFailed(retcode=100), None]
    builder = SimpleNamespace(build=AsyncMock(return_value={
        'parts': [{'type': 'image', 'data': {'file': 'base64://x'}}], 'fallback': '文字与链接', 'nodes': []}))
    with patch.object(plugin, 'store', Store(tmp_path / 'db')), patch.object(plugin, '_builder', builder), patch.object(plugin, 'get_bots', return_value={'100': robot}):
        asyncio.run(plugin.send_events(bot_id='100', kind='group', target_id='555', events=[], title='x',
                                       mode='image', settings={}, valid=lambda: True))
    assert robot.send_group_msg.await_count == 2
    assert robot.send_group_msg.call_args.kwargs['message'][0].data['text'] == '文字与链接'


def test_group_admin_cannot_manage_other_users_urgent_target(tmp_path):
    store = Store(tmp_path / 'db')
    message = event('活动私人提醒 添加 鸣潮', role='admin')
    message.message.append(MessageSegment.at(3000))
    with patch.object(plugin, 'store', store):
        asyncio.run(plugin.handle(bot(), message))
    assert store.urgent_targets() == []


def test_superuser_stops_explicit_target_instead_of_self(tmp_path):
    store = Store(tmp_path / 'db')
    store.save_urgent({'bot_id': '100', 'user_id': '1000', 'games': ['ww']})
    store.save_urgent({'bot_id': '100', 'user_id': '3000', 'games': ['ww']})
    message = event('活动私人提醒 停止', user=1000)
    message.message.append(MessageSegment.at(3000))
    with patch.object(plugin, 'store', store):
        asyncio.run(plugin.handle(bot(), message))
    assert store.urgent_target('100', '1000')['enabled']
    assert not store.urgent_target('100', '3000')['enabled']


def test_start_stop_owns_one_worker_and_closes_clients(tmp_path):
    source = SimpleNamespace(close=AsyncMock())
    builder = SimpleNamespace(close=AsyncMock())
    async def idle():
        await asyncio.Event().wait()
    async def run():
        await plugin._start()
        first = plugin._task
        await plugin._start()
        assert plugin._task is first
        await plugin._stop()
        assert first.done() and plugin._service is None
    with patch.object(plugin, 'ROOT', tmp_path), patch.object(plugin, 'store', Store(tmp_path / 'db')), \
            patch.object(plugin, 'SourceClient', return_value=source), patch.object(plugin, 'MessageBuilder', return_value=builder) as factory, \
            patch.object(plugin, 'get_driver', return_value=SimpleNamespace(config=SimpleNamespace(game_event_cover_dns='google'))), \
            patch.object(plugin, 'worker', idle):
        asyncio.run(run())
    assert factory.call_args.kwargs.get('dns_mode') == 'google'
    source.close.assert_awaited_once()
    builder.close.assert_awaited_once()
