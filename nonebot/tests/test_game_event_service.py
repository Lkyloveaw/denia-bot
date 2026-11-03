import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from game_event_logic import event_record
from game_event_store import Store
from game_event_service import GameEventService


NOW = 1800000000.0


class Source:
    def __init__(self, events):
        self.events = events

    async def fetch(self, game_id, **kwargs):
        return {'game_id': game_id, 'source': 'sra', 'activities': self.events,
                'version': '1', 'checked_at': NOW, 'fetched_at': NOW,
                'stale': False, 'error': '', 'complete': True}


def setup(tmp_path, events):
    store = Store(tmp_path / 'events.db')
    sub = store.save_subscription({'bot_id': '100', 'kind': 'group', 'target_id': '200', 'games': ['ww']})
    sent = []

    async def sender(**kwargs):
        sent.append(kwargs)

    return store, sub, sent, GameEventService(store, Source(events), sender, clock=lambda: NOW, send_interval=0)


def test_initial_overview_is_a_baseline_and_new_event_notifies_once(tmp_path):
    old = event_record('ww', '旧活动', NOW - 100, NOW + 10000)
    new = event_record('ww', '新活动', NOW - 50, NOW + 5000)
    store, sub, sent, service = setup(tmp_path, [old])

    async def run():
        await service.cycle()
        await service.cycle()
        assert len(sent) == 1
        service.sources.events.append(new)
        await service.cycle()
        await service.cycle()
        assert len(sent) == 2
        assert sent[-1]['events'][0]['event']['name'] == '新活动'
    asyncio.run(run())


def test_failed_initial_overview_is_retried_without_losing_baseline(tmp_path):
    event = event_record('ww', '活动', NOW - 100, NOW + 10000)
    store, sub, sent, service = setup(tmp_path, [event])
    attempts = 0

    async def sender(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('offline')
        sent.append(kwargs)
    service.sender = sender

    async def run():
        await service.cycle()
        assert store.game_state(sub['id'], 'ww')['status'] == 'initializing'
        await service.cycle()
        await service.cycle()
        assert len(sent) == 1 and attempts == 2
    asyncio.run(run())


def test_policy_change_during_fetch_blocks_every_delivery(tmp_path):
    event = event_record('ww', '活动', NOW - 100, NOW + 10000)
    store, sub, sent, service = setup(tmp_path, [event])
    original = service.sources.fetch

    async def fetch(*args, **kwargs):
        snapshot = await original(*args, **kwargs)
        cfg = store.settings()
        store.save_settings({'blacklist': ['group:200']}, cfg['revision'])
        return snapshot
    service.sources.fetch = fetch
    asyncio.run(service.cycle())
    assert sent == []


def test_data_only_refresh_never_sends(tmp_path):
    event = event_record('ww', '活动', NOW - 100, NOW + 10000)
    store, sub, sent, service = setup(tmp_path, [event])
    asyncio.run(service.cycle(mode='data', games=['ww']))
    assert store.snapshot('ww')['activities'][0]['name'] == '活动'
    assert sent == []


def test_urgent_respects_origin_and_target_policy_and_ack(tmp_path):
    event = event_record('ww', '活动', NOW - 100, NOW + 10000)
    store, sub, sent, service = setup(tmp_path, [event])
    store.save_settings({'urgent_enabled': True, 'quiet_start': 0, 'quiet_end': 0}, 0)
    store.save_urgent({'bot_id': '100', 'user_id': '300', 'games': ['ww'], 'origin_session': 'group:200'})

    async def run():
        await service.cycle()
        assert len([x for x in sent if x['kind'] == 'private']) == 1
        title = next(x['title'] for x in sent if x['kind'] == 'private')
        assert '.我已知晓' in title and '.活动私人提醒 停止' in title
        assert store.ack_urgent('100', '300', '不存在', NOW) == []
        assert store.ack_urgent('100', '300', '活动', NOW) == [event['uid']]
        service.clock = lambda: NOW + 8000
        await service.urgent_cycle()
        assert len([x for x in sent if x['kind'] == 'private']) == 1
        cfg = store.settings()
        store.save_settings({'blacklist': ['group:200']}, cfg['revision'])
        assert not service.urgent_allowed(store.urgent_target('100', '300'), store.settings())
    asyncio.run(run())


def test_global_reminder_change_during_build_invalidates_ready_plan(tmp_path):
    old = event_record('ww', '旧活动', NOW - 100, NOW + 10000)
    store, sub, sent, service = setup(tmp_path, [old])
    validity = []
    async def run():
        await service.cycle()
        service.sources.events.append(event_record('ww', '新活动', NOW - 50, NOW + 20000))
        async def sender(**kwargs):
            settings = store.settings()
            store.save_settings({'notify_new': False, 'thresholds': []}, settings['revision'])
            validity.append(kwargs['valid']())
            raise RuntimeError('blocked')
        service.sender = sender
        await service.cycle()
    asyncio.run(run())
    assert validity == [False]


def test_urgent_expiring_or_entering_quiet_hours_during_build_is_blocked(tmp_path):
    from datetime import datetime
    from game_event_logic import TZ
    now = datetime(2026, 10, 31, 22, 59, tzinfo=TZ).timestamp()
    event = event_record('ww', '临期', now - 100, now + 30)
    store, sub, sent, service = setup(tmp_path, [event])
    service.clock = lambda: now
    store.save_settings({'urgent_enabled': True}, 0)
    store.save_urgent({'bot_id': '100', 'user_id': '300', 'games': ['ww']})
    store.save_snapshot({'game_id': 'ww', 'activities': [event], 'checked_at': now, 'complete': True})
    validity = []
    async def sender(**kwargs):
        service.clock = lambda: now + 61
        validity.append(kwargs['valid']())
        raise RuntimeError('blocked')
    service.sender = sender
    asyncio.run(service.urgent_cycle())
    assert validity == [False]
    assert not store.urgent_budget('100', '300', '2026-10-31')['count']


def test_partial_initial_success_survives_service_restart(tmp_path):
    events = [event_record('ww', f'活动{i}', NOW-100, NOW+10000+i) for i in range(12)]
    store, sub, sent, service = setup(tmp_path, events)
    async def sender(**kwargs):
        if sent:
            raise RuntimeError('second segment failed')
        sent.append(kwargs)
    service.sender = sender
    asyncio.run(service.cycle())
    delivered = {e['uid'] for e in sent[0]['events']}
    retry = []
    async def successful(**kwargs):
        retry.extend(kwargs['events'])
    restarted = GameEventService(Store(tmp_path / 'events.db'), Source(events), successful, clock=lambda: NOW, send_interval=0)
    asyncio.run(restarted.cycle())
    assert {e['uid'] for e in retry} == {e['uid'] for e in events} - delivered
    assert store.game_state(sub['id'], 'ww')['status'] == 'ready'


def test_scoped_check_does_not_send_cached_urgent_from_other_games(tmp_path):
    store, sub, sent, service = setup(tmp_path, [])
    store.save_settings({'urgent_enabled': True, 'quiet_start': 0, 'quiet_end': 0}, 0)
    store.save_urgent({'bot_id': '100', 'user_id': '300', 'games': ['ys']})
    store.save_snapshot({'game_id': 'ys', 'activities': [event_record('ys', '其它游戏', NOW-100, NOW+10000)],
                         'checked_at': NOW, 'complete': True})
    asyncio.run(service.cycle(games=['ww']))
    assert not sent


def test_expired_during_build_is_not_valid_for_normal_delivery(tmp_path):
    store, sub, sent, service = setup(tmp_path, [event_record('ww', '即将结束', NOW-100, NOW+1)])
    validity = []
    async def sender(**kwargs):
        service.clock = lambda: NOW + 2
        validity.append(kwargs['valid']())
        raise RuntimeError('blocked')
    service.sender = sender
    asyncio.run(service.cycle())
    assert validity == [False]


def test_stale_urgent_summary_exposes_cache_timestamp(tmp_path):
    store, sub, sent, service = setup(tmp_path, [])
    store.save_settings({'urgent_enabled': True, 'quiet_start': 0, 'quiet_end': 0}, 0)
    store.save_urgent({'bot_id': '100', 'user_id': '300', 'games': ['ww']})
    store.save_snapshot({'game_id': 'ww', 'activities': [event_record('ww', '缓存活动', NOW-100, NOW+10000)],
                         'checked_at': NOW-1000, 'complete': True, 'stale': True})
    asyncio.run(service.urgent_cycle())
    assert '缓存' in sent[0]['title']
