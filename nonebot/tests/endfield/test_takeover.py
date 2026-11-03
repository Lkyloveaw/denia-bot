"""Regression coverage for integrating the previously isolated implementation."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from tests.endfield.test_wiring import endfield as plugin, private_event, group_event
from tests.endfield.test_scheduler import FakeClient, _binding
from endfield import client as ec, cards
from endfield.config import EndfieldConfig
from endfield.store import BindingStore, SubscriptionStore
from endfield.scheduler import EndfieldScheduler, RecordingSink


def test_panel_key_wins_and_hot_reloads(tmp_path, monkeypatch):
    secret = tmp_path / 'secrets.json'
    monkeypatch.setattr(plugin, 'SECRETS_PATH', secret)
    monkeypatch.setenv('ENDFIELD_API_KEY', 'environment-test-key')
    secret.write_text(json.dumps({'api_key': 'panel-test-key'}))
    assert plugin._api_key() == 'panel-test-key'
    secret.write_text(json.dumps({'api_key': 'updated-test-key'}))
    assert plugin._api_key() == 'updated-test-key'
    secret.unlink()
    assert plugin._api_key() == 'environment-test-key'


def test_consent_cannot_be_accepted_from_group():
    plugin._pending_consent.add('123456')
    try:
        assert not plugin._rule(group_event('同意', at_self=True))
    finally:
        plugin._pending_consent.discard('123456')


def test_shared_command_stores_see_later_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'BINDINGS_PATH', tmp_path / 'bindings.json')
    monkeypatch.setattr(plugin, 'SUBSCRIPTIONS_PATH', tmp_path / 'subscriptions.json')
    async def scenario():
        scheduler_store = plugin._store()
        await scheduler_store.load()
        await plugin._store().set_consent('100')
        await plugin._store().add_binding('100', _binding())
        assert await scheduler_store.get_primary('100')
        scheduler_subs = plugin._subscriptions()
        await scheduler_subs.load()
        await plugin._subscriptions().set_personal('sanity', '100')
        assert '100' in await scheduler_subs.list_personal('sanity')
        await plugin._subscriptions().remove_personal('sanity', '100')
        assert not await scheduler_subs.list_personal('sanity')
    asyncio.run(scenario())


def test_invalid_numeric_role_selects_exact_server(tmp_path):
    async def scenario():
        store = BindingStore(tmp_path / 'bindings.json')
        await store.set_consent('100')
        await store.add_binding('100', {**_binding('123456789'), 'server_id': 1})
        await store.add_binding('100', {**_binding('123456789'), 'server_id': 2})
        await store.mark_invalid('100', '123456789', '401', server_id=2)
        rows = await store.list_bindings('100')
        assert [r['status'] for r in rows] == ['valid', 'invalid']
    asyncio.run(scenario())


def test_auth_creates_gateway_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'BINDINGS_PATH', tmp_path / 'bindings.json')
    monkeypatch.setattr(plugin, '_pending_auth', {'123456': 'request-test'})
    client = AsyncMock()
    client.get_authorization_status.return_value = {
        'status': 'approved', 'framework_token': 'fixture-token',
        'available_roles': [{'role_id': '123456789', 'server_id': 1,
                             'nickname': '测试角色', 'is_default': True}]}
    client.create_binding.return_value = {'id': 'gateway-binding', 'role_id': '123456789',
                                           'server_id': 1, 'nickname': '测试角色'}
    monkeypatch.setattr(plugin, '_client', lambda *args: client)
    monkeypatch.setattr(plugin, '_send', AsyncMock())
    async def scenario():
        await plugin._store().set_consent('123456')
        await plugin._handle_auth(private_event('授权登陆'), '123456')
        row = await plugin._store().get_primary('123456')
        assert row and row['binding_id'] == 'gateway-binding'
        assert row['role_id'] == '123456789'
        assert row['framework_token'] == 'fixture-token'
        assert '123456' not in plugin._pending_auth
    asyncio.run(scenario())


def test_auth_creates_gateway_binding_from_binding_info(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'BINDINGS_PATH', tmp_path / 'bindings.json')
    monkeypatch.setattr(plugin, '_pending_auth', {'123456': 'request-test'})
    client = AsyncMock()
    client.get_authorization_status.return_value = {
        'status': 'approved', 'framework_token': 'fixture-token',
        'user_info': {'nickname': 'account'},
        'binding_info': {'role_id': '123456789', 'server_id': '2', 'nickname': 'role'},
    }
    client.create_binding.return_value = {
        'id': 'gateway-binding', 'role_id': '123456789', 'server_id': 2, 'nickname': 'role',
    }
    monkeypatch.setattr(plugin, '_client', lambda *args: client)
    monkeypatch.setattr(plugin, '_send', AsyncMock())

    async def scenario():
        await plugin._store().set_consent('123456')
        await plugin._handle_auth(private_event('授权登陆'), '123456')
        client.create_binding.assert_awaited_once()
        row = await plugin._store().get_primary('123456')
        assert row and row['binding_id'] == 'gateway-binding'
        assert row['role_id'] == '123456789'
        assert row['server_id'] == 2
        assert row['framework_token'] == 'fixture-token'
        assert '123456' not in plugin._pending_auth
    asyncio.run(scenario())


@pytest.mark.parametrize('note_succeeds', [True, False])
def test_auth_gateway_missing_token_falls_back_only_after_note_verification(tmp_path, monkeypatch, note_succeeds):
    monkeypatch.setattr(plugin, 'BINDINGS_PATH', tmp_path / 'bindings.json')
    monkeypatch.setattr(plugin, '_pending_auth', {'123456': 'request-test'})
    client = AsyncMock()
    client.get_authorization_status.return_value = {
        'status': 'approved', 'framework_token': 'fixture-token',
        'binding_info': {'role_id': '123456789', 'server_id': '2', 'nickname': 'role'},
    }
    client.create_binding.side_effect = ec.EndfieldAuthError('请提供 framework_token', status=400)
    if not note_succeeds:
        client.get_note.side_effect = ec.EndfieldTransientError('verification failed')
    else:
        client.get_note.return_value = {'stamina': {'current': 1}}
    monkeypatch.setattr(plugin, '_client', lambda *args: client)
    send = AsyncMock()
    monkeypatch.setattr(plugin, '_send', send)

    async def scenario():
        await plugin._store().set_consent('123456')
        await plugin._handle_auth(private_event('授权登陆'), '123456')
        client.get_note.assert_awaited_once_with('fixture-token', '123456789', 2)
        row = await plugin._store().get_primary('123456')
        if note_succeeds:
            assert row and row['role_id'] == '123456789'
            assert row['framework_token'] == 'fixture-token'
            assert row['binding_id'] == ''
            assert '本地' in send.await_args.args[1]
            assert '123456' not in plugin._pending_auth
        else:
            assert row is None
            assert '未能验证' in send.await_args.args[1]
    asyncio.run(scenario())


def test_auth_other_gateway_auth_failure_does_not_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'BINDINGS_PATH', tmp_path / 'bindings.json')
    monkeypatch.setattr(plugin, '_pending_auth', {'123456': 'request-test'})
    client = AsyncMock()
    client.get_authorization_status.return_value = {
        'status': 'approved', 'framework_token': 'fixture-token',
        'binding_info': {'role_id': '123456789', 'server_id': '2', 'nickname': 'role'},
    }
    client.create_binding.side_effect = ec.EndfieldAuthError('invalid credential', status=403)
    monkeypatch.setattr(plugin, '_client', lambda *args: client)
    monkeypatch.setattr(plugin, '_send', AsyncMock())

    async def scenario():
        await plugin._store().set_consent('123456')
        await plugin._handle_auth(private_event('授权登陆'), '123456')
        client.get_note.assert_not_awaited()
        assert await plugin._store().get_primary('123456') is None
    asyncio.run(scenario())


def test_local_fallback_preserves_existing_gateway_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'BINDINGS_PATH', tmp_path / 'bindings.json')
    monkeypatch.setattr(plugin, '_pending_auth', {'123456': 'request-test'})
    client = AsyncMock()
    client.get_authorization_status.return_value = {
        'status': 'approved', 'framework_token': 'new-token',
        'binding_info': {'role_id': '123456789', 'server_id': '1', 'nickname': 'role'},
    }
    client.create_binding.side_effect = ec.EndfieldAuthError('请提供 framework_token', status=400)
    client.get_note.return_value = {'stamina': {'current': 1}}
    monkeypatch.setattr(plugin, '_client', lambda *args: client)
    monkeypatch.setattr(plugin, '_send', AsyncMock())

    async def scenario():
        await plugin._store().set_consent('123456')
        await plugin._store().add_binding('123456', {**_binding('123456789'), 'binding_id': 'old-gateway'})
        await plugin._handle_auth(private_event('授权登陆'), '123456')
        row = await plugin._store().get_primary('123456')
        assert row['binding_id'] == 'old-gateway'
        assert row['framework_token'] == 'new-token'
        client.delete_binding.assert_not_awaited()
    asyncio.run(scenario())


def test_expired_auth_clears_session(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'BINDINGS_PATH', tmp_path / 'bindings.json')
    monkeypatch.setattr(plugin, '_pending_auth', {'123456': 'expired-request'})
    client = AsyncMock()
    client.get_authorization_status.return_value = {'status': 'expired'}
    monkeypatch.setattr(plugin, '_client', lambda *args: client)
    monkeypatch.setattr(plugin, '_send', AsyncMock())
    async def scenario():
        await plugin._store().set_consent('123456')
        await plugin._handle_auth(private_event('授权登陆'), '123456')
        assert '123456' not in plugin._pending_auth
    asyncio.run(scenario())


@pytest.mark.parametrize('config', [EndfieldConfig(enabled=False), EndfieldConfig(enable_queries=False)])
def test_disabled_queries_do_not_dispatch(config, monkeypatch):
    monkeypatch.setattr(plugin, '_gate', plugin.logic.UserGate())
    monkeypatch.setattr(plugin, '_config', lambda: config)
    monkeypatch.setattr(plugin, '_send', AsyncMock())
    handler = AsyncMock()
    monkeypatch.setattr(plugin, '_handle_query', handler)
    asyncio.run(plugin._handle_endfield(private_event('便签')))
    assert not handler.called


def test_scheduler_starts_without_key_and_when_disabled(monkeypatch):
    monkeypatch.setattr(plugin, '_api_key', lambda: '')
    monkeypatch.setattr(plugin, '_config', lambda: EndfieldConfig(enabled=False))
    async def scenario():
        await plugin._start_endfield_scheduler()
        try:
            assert plugin._scheduler_task is not None
        finally:
            await plugin._stop_endfield_scheduler()
    asyncio.run(scenario())


def test_poll_intervals_and_rate_limit_pause(tmp_path):
    async def scenario():
        bindings = BindingStore(tmp_path / 'bindings.json')
        subs = SubscriptionStore(tmp_path / 'subs.json')
        for qq in ['100', '101']:
            await bindings.set_consent(qq)
            await bindings.add_binding(qq, _binding())
            await subs.set_personal('sanity', qq)
        fake = FakeClient()
        calls = []
        async def rate_limited(*args):
            calls.append(args)
            raise ec.EndfieldRateLimitError('limit')
        fake.get_stamina = rate_limited
        scheduler = EndfieldScheduler(bindings=bindings, subscriptions=subs,
            client_factory=lambda qq: fake, sink=RecordingSink(),
            config_provider=lambda: EndfieldConfig())
        now = datetime(2026, 10, 31, 12)
        await scheduler.tick(now)
        assert len(calls) == 1
        await scheduler.tick(now + timedelta(minutes=1))
        assert len(calls) == 1
        await scheduler.tick(now + timedelta(minutes=21))
        assert len(calls) == 2
    asyncio.run(scenario())


def test_signin_failure_delivered_after_quiet_time(tmp_path):
    async def scenario():
        bindings = BindingStore(tmp_path / 'bindings.json')
        subs = SubscriptionStore(tmp_path / 'subs.json')
        await bindings.set_consent('100')
        await bindings.add_binding('100', _binding())
        await subs.set_personal('signin', '100')
        fake = FakeClient()
        fake.post_attendance = AsyncMock(side_effect=ec.EndfieldTransientError('timeout'))
        sink = RecordingSink()
        scheduler = EndfieldScheduler(bindings=bindings, subscriptions=subs,
            client_factory=lambda qq: fake, sink=sink, config_provider=lambda: EndfieldConfig())
        await scheduler.tick(datetime(2026, 10, 31, 2))
        assert not sink.private_messages
        await scheduler.tick(datetime(2026, 10, 31, 8))
        assert len(sink.private_messages) == 1
        await scheduler.tick(datetime(2026, 10, 31, 8, 1))
        assert len(sink.private_messages) == 1
        assert fake.post_attendance.await_count == 1
    asyncio.run(scenario())


def test_card_pagination_preserves_long_content():
    lines = [f'记录{i:03d}：' + '雷电' * 45 for i in range(45)]
    pages = cards.build_pages('抽卡记录', lines)
    from html.parser import HTMLParser
    class Reader(HTMLParser):
        def __init__(self):
            super().__init__(); self.in_row = False; self.rows = []
        def handle_starttag(self, tag, attrs):
            if tag == 'li': self.in_row = True
        def handle_endtag(self, tag):
            if tag == 'li': self.in_row = False
        def handle_data(self, value):
            if self.in_row: self.rows.append(value)
    reader = Reader()
    for page in pages:
        assert 1200 <= page.height <= 2400
        reader.feed(page.html)
    assert ''.join(reader.rows).replace('\n', '') == ''.join(lines)


def test_manual_signin_covers_all_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'BINDINGS_PATH', tmp_path / 'bindings.json')
    fake = FakeClient()
    monkeypatch.setattr(plugin, '_client', lambda *args: fake)
    replies = []
    async def send(event, text): replies.append(text)
    monkeypatch.setattr(plugin, '_send', send)
    async def scenario():
        store = plugin._store()
        await store.set_consent('123456')
        await store.add_binding('123456', _binding('first'))
        await store.add_binding('123456', _binding('second'))
        await plugin._handle_query(private_event('签到'), '123456', plugin.logic.Command('签到'))
        assert fake.attendance_calls == [('first', 1), ('second', 1)]
        assert '角色first' in replies[0] and '角色second' in replies[0]
    asyncio.run(scenario())


def test_delete_cleans_gateway_after_local_removal(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'BINDINGS_PATH', tmp_path / 'bindings.json')
    fake = AsyncMock()
    monkeypatch.setattr(plugin, '_client', lambda *args: fake)
    monkeypatch.setattr(plugin, '_send', AsyncMock())
    async def deletion(binding_id, qq):
        assert not await plugin._store().list_bindings(qq)
        assert binding_id == 'gateway-1'
        return True
    fake.delete_binding.side_effect = deletion
    async def scenario():
        await plugin._store().set_consent('123456')
        await plugin._store().add_binding('123456', {**_binding(), 'binding_id': 'gateway-1'})
        await plugin._handle_binding(private_event('删除绑定 1'), '123456', plugin.logic.Command('删除绑定', '1'))
        assert not await plugin._store().list_bindings('123456')
        assert fake.delete_binding.await_count == 1
    asyncio.run(scenario())


def test_authorization_link_recalled_and_session_expires(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(plugin.nonebot, 'get_bot', lambda ident: fake)
    monkeypatch.setattr(plugin.asyncio, 'sleep', AsyncMock())
    monkeypatch.setattr(plugin, '_pending_auth', {'123456': 'request-1'})
    asyncio.run(plugin._expire_auth_link(private_event('授权登陆'), 42, '123456', 'request-1'))
    fake.delete_msg.assert_awaited_once_with(message_id=42)
    assert '123456' not in plugin._pending_auth
