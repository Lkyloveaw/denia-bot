"""Behavior regressions found while reviewing the four Endfield branches."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from endfield.client import EndfieldAuthError, EndfieldClient, EndfieldProtocolError, EndfieldRateLimitError, EndfieldTransientError
from endfield.config import EndfieldConfig
from endfield.scheduler import EndfieldScheduler, RecordingSink
from endfield.store import BindingStore, SubscriptionStore
from tests.endfield.test_scheduler import FakeClient, _binding
from tests.endfield.test_wiring import endfield as plugin, group_event, private_event, Message, MessageSegment, SELF


def test_reply_without_self_at_does_not_trigger():
    for event in [group_event('便签'), group_event('便签', at_other=True)]:
        event.to_me = True
        assert not plugin._rule(event)


def test_original_at_survives_adapter_normalization():
    event = group_event('便签', at_self=True)
    event.original_message = event.message.copy()
    event.message = Message('便签')
    event.to_me = False
    assert plugin._rule(event)


def test_reply_auto_at_respects_gate_setting(monkeypatch):
    event = group_event('便签')
    event.original_message = Message([MessageSegment.reply(42), MessageSegment.at(SELF), MessageSegment.text('便签')])
    monkeypatch.setattr(plugin, 'driver', SimpleNamespace(config=SimpleNamespace(quote_at_counts='0')))
    assert not plugin._rule(event)
    event.original_message += MessageSegment.at(SELF)
    assert plugin._rule(event)
    plugin.driver.config.quote_at_counts = '1'
    assert plugin._rule(event)


def test_bad_query_parameters_do_not_invalidate_credentials():
    async def scenario():
        async with EndfieldClient('fixture-key', transport=httpx.MockTransport(
            lambda request: httpx.Response(400, json={'code': 400, 'message': 'invalid serverId'})
        )) as client:
            with pytest.raises(EndfieldProtocolError):
                await client.get_note('valid-token', 'role-1', 1)
    asyncio.run(scenario())


async def setup_scheduler(folder, kind, *, two_accounts=False, sink=None):
    bindings = BindingStore(folder / 'bindings.json')
    await bindings.set_consent('100')
    await bindings.add_binding('100', _binding('first'))
    if two_accounts:
        await bindings.add_binding('100', _binding('second'))
    subs = SubscriptionStore(folder / 'subscriptions.json')
    await subs.set_personal(kind, '100')
    fake = FakeClient()
    sink = sink or RecordingSink()
    config = EndfieldConfig(signin_time='00:00', signin_jitter_minutes=0, push_retry=0)

    def fresh():
        return EndfieldScheduler(bindings=BindingStore(folder / 'bindings.json'),
            subscriptions=SubscriptionStore(folder / 'subscriptions.json'),
            client_factory=lambda qq: fake, sink=sink, config_provider=lambda: config)
    return fresh, fake, sink


@pytest.mark.parametrize('repeat_limit', [False, True])
def test_signin_429_restart_does_not_repeat_success(tmp_path, monkeypatch, repeat_limit):
    sleep = AsyncMock()
    monkeypatch.setattr('endfield.scheduler.asyncio.sleep', sleep)
    async def scenario():
        fresh, fake, sink = await setup_scheduler(tmp_path, 'signin', two_accounts=True)
        calls = []
        async def attendance(token, role, server):
            calls.append(role)
            if role == 'second' and (calls.count(role) == 1 or repeat_limit):
                raise EndfieldRateLimitError('limited', status=429)
            return {'ok': True}
        fake.post_attendance = attendance
        now = datetime(2026, 10, 31, 12)
        await fresh().tick(now)
        assert calls == ['first', 'second']
        sleep.assert_awaited_once_with(1.5)
        await fresh().tick(now + timedelta(seconds=30))
        assert calls == ['first', 'second']
        await fresh().tick(now + timedelta(minutes=1))
        await fresh().tick(now + timedelta(minutes=2))
        await fresh().tick(now + timedelta(hours=1))
        assert calls == ['first', 'second', 'second']
        row = (await fresh().subscriptions.list_personal('signin'))['100']
        assert row['last_run_date'] == '2026-10-31'
        assert row['last_result'] == ('partial' if repeat_limit else 'ok')
        assert len(sink.private_messages) == int(repeat_limit)
    asyncio.run(scenario())


def test_interrupted_signin_is_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr('endfield.scheduler.asyncio.sleep', AsyncMock())
    async def scenario():
        fresh, fake, sink = await setup_scheduler(tmp_path, 'signin', two_accounts=True)
        calls = []
        async def attendance(token, role, server):
            calls.append(role)
            if role == 'first':
                raise asyncio.CancelledError()
            return {'ok': True}
        fake.post_attendance = attendance
        now = datetime(2026, 10, 31, 12)
        with pytest.raises(asyncio.CancelledError):
            await fresh().tick(now)
        await fresh().tick(now + timedelta(minutes=1))
        assert calls == ['first', 'second']
        assert '未确认' in sink.private_messages[0][1]
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['sanity', 'tickets'])
def test_reminder_cooldown_survives_restart(tmp_path, kind):
    async def scenario():
        fresh, fake, sink = await setup_scheduler(tmp_path, kind)
        now = datetime(2026, 10, 31, 12)
        await fresh().tick(now)
        assert len(sink.private_messages) == 1
        full_domain = fake.domain
        fake.stamina = {'current': 1, 'max': 120}
        fake.domain = {'domain': []}
        await fresh().tick(now + timedelta(minutes=20))
        fake.stamina = {'current': 120, 'max': 120}
        fake.domain = full_domain
        await fresh().tick(now + timedelta(minutes=40))
        assert len(sink.private_messages) == 1
        await fresh().tick(now + timedelta(hours=4))
        assert len(sink.private_messages) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('kind,method', [('sanity', 'get_stamina'), ('tickets', 'get_domain')])
def test_invalid_binding_notifies_once_with_delivery_retry(tmp_path, kind, method):
    async def scenario():
        fresh, fake, sink = await setup_scheduler(tmp_path, kind, sink=RecordingSink(fail_private=True))
        query = AsyncMock(side_effect=EndfieldAuthError('invalid', status=403))
        setattr(fake, method, query)
        now = datetime(2026, 10, 31, 12)
        await fresh().tick(now)
        sink.fail_private = False
        await fresh().tick(now + timedelta(minutes=20))
        await fresh().tick(now + timedelta(minutes=40))
        assert query.await_count == 1
        assert len(sink.private_messages) == 1
        assert '授权登陆' in sink.private_messages[0][1]
        assert (await fresh().bindings.list_bindings('100'))[0]['status'] == 'invalid'
    asyncio.run(scenario())


def test_signin_notification_failures_disable_subscription(tmp_path):
    async def scenario():
        fresh, fake, sink = await setup_scheduler(tmp_path, 'signin', sink=RecordingSink(fail_private=True))
        fake.post_attendance = AsyncMock(side_effect=EndfieldTransientError('timeout'))
        now = datetime(2026, 10, 31, 12)
        for minute in range(4):
            await fresh().tick(now + timedelta(minutes=minute))
        row = (await fresh().subscriptions.list_personal('signin'))['100']
        assert not row['enabled']
        assert row['fail_count'] == 3
        assert fake.post_attendance.await_count == 1
        await fresh().tick(now + timedelta(days=1))
        assert fake.post_attendance.await_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['sanity', 'tickets', 'signin', 'announcements'])
def test_resubscription_starts_new_failure_budget(tmp_path, kind):
    async def scenario():
        fresh, fake, sink = await setup_scheduler(tmp_path, 'signin' if kind == 'announcements' else kind,
            sink=RecordingSink(fail_private=True, fail_group=True))
        if kind == 'announcements':
            await fresh().subscriptions.remove_personal('signin', '100')
            await fresh().subscriptions.add_announcement('200', '100')
        fake.post_attendance = AsyncMock(side_effect=EndfieldTransientError('timeout'))
        now = datetime(2026, 10, 31, 12)
        for minutes in [0, 20, 40]:
            await fresh().tick(now + timedelta(minutes=minutes))
        if kind == 'announcements':
            assert not (await fresh().subscriptions.list_announcements())['200']['enabled']
            await fresh().subscriptions.add_announcement('200', '100')
        else:
            assert not (await fresh().subscriptions.list_personal(kind))['100']['enabled']
            await fresh().subscriptions.set_personal(kind, '100')
        await fresh().tick(now + timedelta(minutes=60))
        row = ((await fresh().subscriptions.list_announcements())['200'] if kind == 'announcements'
               else (await fresh().subscriptions.list_personal(kind))['100'])
        assert row['enabled']
        assert row['fail_count'] == 1
    asyncio.run(scenario())


def test_reauthorization_clears_stale_invalid_notification(tmp_path):
    async def scenario():
        fresh, fake, sink = await setup_scheduler(tmp_path, 'sanity', sink=RecordingSink(fail_private=True))
        fake.get_stamina = AsyncMock(side_effect=EndfieldAuthError('expired'))
        now = datetime(2026, 10, 31, 12)
        await fresh().tick(now)
        await fresh().bindings.add_binding('100', _binding('first', token='replacement-token'))
        sink.fail_private = False
        await fresh().tick(now + timedelta(minutes=20))
        assert not sink.private_messages
        row = (await fresh().subscriptions.list_personal('sanity'))['100']
        assert not row['pending_auth_notification']
    asyncio.run(scenario())


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_reauthorization_then_delete_uses_new_gateway_id(tmp_path, monkeypatch, cleanup_fails):
    monkeypatch.setattr(plugin, 'BINDINGS_PATH', tmp_path / 'bindings.json')
    monkeypatch.setattr(plugin, '_pending_auth', {'123456': 'request-new'})
    monkeypatch.setattr(plugin, '_auth_deadlines', {})
    client = AsyncMock()
    client.get_authorization_status.return_value = {
        'status': 'approved', 'framework_token': 'new-token',
        'available_roles': [{'role_id': 'role', 'server_id': 1, 'nickname': '测试角色'}]}
    client.create_binding.return_value = {'id': 'new-binding'}
    async def delete(binding_id, qq):
        if cleanup_fails and binding_id == 'old-binding':
            raise EndfieldTransientError('unavailable')
        return True
    client.delete_binding.side_effect = delete
    monkeypatch.setattr(plugin, '_client', lambda *args: client)
    send = AsyncMock()
    monkeypatch.setattr(plugin, '_send', send)
    async def scenario():
        await plugin._store().set_consent('123456')
        await plugin._store().add_binding('123456', {**_binding('role'), 'binding_id': 'old-binding'})
        await plugin._handle_auth(private_event('授权登陆'), '123456')
        row = await plugin._store().get_primary('123456')
        assert row['binding_id'] == 'new-binding'
        assert row['framework_token'] == 'new-token'
        assert ('旧网关绑定未能清理' in send.call_args.args[1]) == cleanup_fails
        await plugin._handle_binding(private_event('删除绑定 1'), '123456', plugin.logic.Command('删除绑定', '1'))
        assert [call.args[0] for call in client.delete_binding.await_args_list] == ['old-binding', 'new-binding']
        assert not await plugin._store().list_bindings('123456')
    asyncio.run(scenario())
