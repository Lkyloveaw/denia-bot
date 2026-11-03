"""Real SQLite behavior: stale writes, durable progress, and concurrent actors."""
import importlib
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def store(tmp_path):
    module = importlib.import_module('game_event_store')
    return module.Store(tmp_path / 'events.sqlite3')


def sub(store, **patch):
    return store.save_subscription({'bot_id': '100', 'kind': 'group', 'target_id': '200', 'games': ['ww'], **patch})


def event(uid='a', end=10000):
    return {'uid': uid, 'game_id': 'ww', 'name': 'Festival ' + uid, 'start': 1, 'end': end}


def test_lazy_open_and_restart_detached_defaults(tmp_path):
    module = importlib.import_module('game_event_store')
    path = tmp_path / 'nested' / 'events.sqlite3'
    s = module.Store(path)
    assert not path.exists()
    cfg = s.settings()
    cfg['thresholds'].append(50)
    assert module.Store(path).settings()['thresholds'] == [7, 3, 1]
    assert s.settings()['urgent_enabled'] is False
    assert s.runtime() == {'bots': [], 'at': 0}


def test_unknown_schema_is_not_cleared(tmp_path):
    module = importlib.import_module('game_event_store')
    path = tmp_path / 'future.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version = 99')
        db.execute('CREATE TABLE precious (value TEXT)')
        db.execute("INSERT INTO precious VALUES ('saved')")
    with pytest.raises(ValueError):
        module.Store(path).settings()
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT value FROM precious').fetchone()[0] == 'saved'


def test_settings_cas_merges_and_validates_strictly(store):
    cfg = store.save_settings({'enabled': False, 'whitelist': ['group:1', 'group:1'], 'thresholds': []}, 0)
    assert cfg['revision'] == 1 and cfg['whitelist'] == ['group:1']
    assert cfg['thresholds'] == []
    with pytest.raises(ValueError):
        store.save_settings({'notify_new': False}, 0)
    assert store.save_settings({'notify_new': False}, 1)['enabled'] is False
    for patch in [{'revision': 1}, {'poll_minutes': True}, {'enabled': 1}, {'poll_minutes': 14}, {'thresholds': [True]}, {'thresholds': ['1']}, {'whitelist': ['group:01']}, {'aliases': {'missing': 'hi'}}, {'urgent_repeat_minutes': 4}]:
        with pytest.raises(ValueError):
            store.save_settings(patch, 2)
    assert store.settings()['revision'] == 2


def test_concurrent_settings_compare_and_swap_has_one_winner(store):
    store.settings()
    barrier = Barrier(2)
    def write(value):
        barrier.wait()
        try:
            store.save_settings({'enabled': value}, 0)
            return 'ok'
        except ValueError:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(write, [True, False])) == ['conflict', 'ok']
    assert store.settings()['revision'] == 1


def test_subscription_revision_validation_and_natural_identity(store):
    row = sub(store)
    assert row['thresholds'] is None and row['cover_mode'] is None
    assert store.subscription(row['id']) == row
    with pytest.raises(ValueError):
        sub(store)
    updated = store.save_subscription({'bot_id': '100', 'kind': 'group', 'target_id': '200', 'thresholds': []}, row['revision'])
    assert updated['id'] == row['id'] and updated['thresholds'] == []
    for patch in [{'target_id': '0'}, {'bot_id': 100}, {'kind': 'other'}, {'games': ['bad']}, {'enabled': 1}, {'include_keywords': ['x' * 65]}, {'bad': True}]:
        with pytest.raises(ValueError):
            store.save_subscription({'id': row['id'], **patch}, updated['revision'])
    with pytest.raises(ValueError):
        store.delete_subscription(row['id'], row['revision'])


def test_generation_reset_on_remove_readd_and_delete_preserves_logs(store):
    row = sub(store)
    generation = store.game_state(row['id'], 'ww')['generation']
    assert store.finish_initial(row['id'], 'ww', generation, {'a': {'new_done': True, 'tier': 3, 'end': 10000}})
    row = store.save_subscription({'id': row['id'], 'games': []}, row['revision'])
    assert store.game_state(row['id'], 'ww') is None
    row = store.save_subscription({'id': row['id'], 'games': ['ww']}, row['revision'])
    newgen = store.game_state(row['id'], 'ww')['generation']
    assert newgen != generation
    assert not store.record_plans(row['id'], 'ww', generation, [{'event': event(), 'reasons': ['new'], 'tier': 1}], 100)
    assert store.states(row['id'], 'ww', newgen) == {}
    store.log_delivery('100', 'group', '200', 'ww', 'success')
    assert store.delete_subscription(row['id'], row['revision'])
    assert store.subscription(row['id']) is None and len(store.deliveries()) == 1


def test_initial_progress_is_durable_and_config_reset_only_initializing(store):
    row = sub(store, games=['ww', 'ys'])
    gid = row['id']
    gen = store.game_state(gid, 'ww')['generation']
    ysgen = store.game_state(gid, 'ys')['generation']
    assert store.begin_initial(gid, 'ww', gen, [event()])
    assert not store.begin_initial(gid, 'ww', gen, [event('b')])
    assert store.initial_sent(gid, 'ww', gen, ['a'])
    assert store.initial_sent(gid, 'ww', gen, ['a', 'b'])
    assert store.game_state(gid, 'ww')['sent'] == ['a', 'b']
    assert store.finish_initial(gid, 'ys', ysgen, {})
    row = store.save_subscription({'id': gid, 'include_keywords': ['Festival']}, row['revision'])
    assert store.game_state(gid, 'ww')['initial'] is None
    assert store.game_state(gid, 'ww')['sent'] == []
    assert store.game_state(gid, 'ys')['status'] == 'ready'
    assert not store.finish_initial(gid, 'ww', gen, {})
    gen = store.game_state(gid, 'ww')['generation']
    assert store.finish_initial(gid, 'ww', gen, {'a': {'new_done': True, 'tier': 3, 'end': 10000}})
    assert not store.initial_sent(gid, 'ww', gen, ['c'])
    assert store.game_state(gid, 'ww')['status'] == 'ready'


def test_record_plans_merges_only_successes_and_never_rewinds_tiers(store):
    row = sub(store)
    gen = store.game_state(row['id'], 'ww')['generation']
    store.finish_initial(row['id'], 'ww', gen, {})
    store.record_plans(row['id'], 'ww', gen, [{'event': event(), 'reasons': ['new', 'd1'], 'tier': 1}], 50)
    store.record_plans(row['id'], 'ww', gen, [{'event': event(end=20000), 'reasons': ['d7'], 'tier': 7}, {'event': event('b'), 'reasons': ['d3'], 'tier': 3}], 60)
    states = store.states(row['id'], 'ww', gen)
    assert states['a'] == {'new_done': True, 'tier': 1, 'end': 20000}
    assert states['b'] == {'new_done': False, 'tier': 3, 'end': 10000}


def urgent(store, **patch):
    return store.save_urgent({'bot_id': '100', 'user_id': '300', 'games': ['ww'], **patch})


def test_urgent_confirm_no_match_disable_and_delete_state(store):
    row = urgent(store)
    store.observe_urgent('100', '300', [event('a'), event('b'), event('expired', end=10)])
    assert store.ack_urgent('100', '300', 'missing', 100) == []
    assert store.ack_urgent('100', '300', 'FESTIVAL A', 100) == ['a']
    store.observe_urgent('100', '300', [event('a', end=20000)])
    assert store.urgent_states('100', '300')['a']['acked'] is True
    row = urgent(store, enabled=False, revision=row['revision'])
    assert store.urgent_states('100', '300')['a']['acked'] is True
    assert store.ack_urgent('100', '300', '', 100) == ['b']
    assert store.delete_urgent('100', '300', row['revision'])
    assert store.urgent_states('100', '300') == {}


def test_urgent_success_budget_is_per_target_message_and_ack_not_overwritten(store):
    urgent(store)
    store.observe_urgent('100', '300', [event(), event('b')])
    store.ack_urgent('100', '300', 'a', 100)
    store.record_urgent_sent('100', '300', [event(), event('b')], 101, '2026-10-31')
    assert store.urgent_budget('100', '300', '2026-10-31') == {'count': 1, 'last_sent': 101}
    assert store.urgent_budget('100', '300', '2026-11-01') == {'count': 0, 'last_sent': 101}
    assert store.urgent_states('100', '300')['a']['acked'] is True
    assert store.urgent_budget('100', '301', '2026-10-31') == {'count': 0, 'last_sent': 0}


def test_urgent_summary_delivery_log_accepts_no_single_game(store):
    store.log_delivery('100', 'private', '300', '', 'success', at=100)
    assert store.deliveries()[0]['game_id'] == ''


def test_snapshots_errors_logs_runtime_and_prune(store):
    snap = {'game_id': 'ww', 'status': 'ok', 'activities': [event()], 'last_success': 100}
    store.save_snapshot(snap)
    snap['status'] = 'error'
    snap['error'] = 'offline'
    store.save_snapshot(snap)
    assert store.snapshot('ww')['activities'][0]['uid'] == 'a'
    assert len(store.snapshots()) == 1
    store.log_delivery('100', 'group', '200', 'ww', 'failed', 'x' * 500, at=100)
    store.log_delivery('100', 'group', '200', 'ww', 'blocked', at=4_000_000)
    assert len(store.deliveries()[1]['detail']) == 300
    store.set_runtime(['100', '100'], 500)
    assert store.runtime() == {'bots': ['100'], 'at': 500}
    row = sub(store)
    gen = store.game_state(row['id'], 'ww')['generation']
    store.finish_initial(row['id'], 'ww', gen, {'old': {'new_done': True, 'tier': 1, 'end': 1}, 'new': {'new_done': True, 'tier': None, 'end': 4_000_001}})
    urgent(store)
    store.observe_urgent('100', '300', [event('old', end=1), event('new', end=4_000_001)])
    store.prune(4_000_000)
    assert list(store.states(row['id'], 'ww', gen)) == ['new']
    assert list(store.urgent_states('100', '300')) == ['new']
    assert len(store.deliveries()) == 1 and len(store.subscriptions()) == 1


def test_refresh_claim_is_atomic_reclaims_and_cooldown(store, monkeypatch):
    module = importlib.import_module('game_event_store')
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    first = store.request_refresh('check', ['ww'], '100')
    assert store.request_refresh('check', ['ww'], '100')['id'] == first['id']
    barrier = Barrier(2)
    def claim(_):
        barrier.wait()
        return store.claim_job(1000)
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(claim, range(2)))
    assert sum(x is not None for x in claimed) == 1
    assert store.claim_job(1599) is None
    assert store.claim_job(1601)['id'] == first['id']
    store.finish_job(first['id'], result={'ok': True})
    with pytest.raises(ValueError):
        store.request_refresh()
    monkeypatch.setattr(module.time, 'time', lambda: 1060)
    assert store.request_refresh()['id'] != first['id']


def test_concurrent_subscription_edits_keep_one_revision(store):
    row = sub(store)
    barrier = Barrier(2)
    def edit(enabled):
        barrier.wait()
        try:
            store.save_subscription({'id': row['id'], 'enabled': enabled}, row['revision'])
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(edit, [True, False])) == 1
    assert store.subscription(row['id'])['revision'] == 1


def test_concurrent_successful_plans_merge_and_ack_survives_send(store):
    row = sub(store)
    gen = store.game_state(row['id'], 'ww')['generation']
    store.finish_initial(row['id'], 'ww', gen, {})
    urgent(store)
    store.observe_urgent('100', '300', [event()])
    barrier = Barrier(2)
    def write(which):
        barrier.wait()
        if which == 'ack':
            store.ack_urgent('100', '300', '', 100)
            tier, uid = 1, 'a'
        else:
            store.record_urgent_sent('100', '300', [event()], 101, '2026-10-31')
            tier, uid = 7, 'b'
        store.record_plans(row['id'], 'ww', gen, [{'event': event(uid), 'reasons': ['new'], 'tier': tier}], 101)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(write, ['ack', 'sent']))
    assert store.urgent_states('100', '300')['a']['acked'] is True
    assert set(store.states(row['id'], 'ww', gen)) == {'a', 'b'}


def test_global_policy_change_invalidates_only_initializing_generation(store):
    row = sub(store, games=['ww', 'ys'])
    ww = store.game_state(row['id'], 'ww')['generation']
    ys = store.game_state(row['id'], 'ys')['generation']
    store.begin_initial(row['id'], 'ww', ww, [event()])
    store.finish_initial(row['id'], 'ys', ys, {})
    store.save_settings({'include_description': True}, 0)
    assert store.game_state(row['id'], 'ww')['generation'] != ww
    assert not store.initial_sent(row['id'], 'ww', ww, ['a'])
    assert not store.finish_initial(row['id'], 'ww', ww, {})
    assert store.game_state(row['id'], 'ys')['generation'] == ys


def test_subscription_and_urgent_capacity_reject_without_losing_existing(store):
    for n in range(1, 201):
        store.save_subscription({'bot_id': '100', 'kind': 'group', 'target_id': str(n), 'games': ['ww']})
        store.save_urgent({'bot_id': '100', 'user_id': str(n), 'games': ['ww']})
    with pytest.raises(ValueError):
        sub(store, target_id='999')
    with pytest.raises(ValueError):
        urgent(store, user_id='999')
    assert len(store.subscriptions()) == 200
    assert len(store.urgent_targets()) == 200


def test_prune_expired_jobs_preserves_initial_snapshot_and_current_settings(store, monkeypatch):
    module = importlib.import_module('game_event_store')
    monkeypatch.setattr(module.time, 'time', lambda: 100)
    old = store.request_refresh()
    row = sub(store)
    gen = store.game_state(row['id'], 'ww')['generation']
    store.begin_initial(row['id'], 'ww', gen, [event(end=4_000_000)])
    store.initial_sent(row['id'], 'ww', gen, ['a'])
    store.prune(4_000_000)
    assert store.claim_job(4_000_000) is None
    assert store.game_state(row['id'], 'ww')['sent'] == ['a']
    assert store.game_state(row['id'], 'ww')['initial'][0]['uid'] == 'a'
    assert store.settings()['poll_minutes'] == 30
    monkeypatch.setattr(module.time, 'time', lambda: 4_000_000)
    assert store.request_refresh()['id'] != old['id']


def test_jobs_lists_newest_first_with_decoded_results_and_limit(store, monkeypatch):
    module = importlib.import_module('game_event_store')
    monkeypatch.setattr(module.time, 'time', lambda: 100)
    first = store.request_refresh('check', ['ww'], '100')
    store.claim_job(100)
    store.finish_job(first['id'], {'games': ['ww'], 'sent': 2})
    monkeypatch.setattr(module.time, 'time', lambda: 160)
    second = store.request_refresh('data', ['ys'], '100')
    rows = store.jobs()
    assert [row['id'] for row in rows] == [second['id'], first['id']]
    assert rows[0]['games'] == ['ys']
    assert rows[1]['result'] == {'games': ['ww'], 'sent': 2}
    assert len(store.jobs(1)) == 1
    rows[1]['result']['sent'] = 100
    assert store.jobs()[1]['result']['sent'] == 2
    with pytest.raises(ValueError):
        store.jobs(True)


def test_cover_settings_reject_limits_the_sender_cannot_honor(store):
    for patch in [{'cover_max_total_mb': 6}, {'cover_max_width': 721}]:
        with pytest.raises(ValueError):
            store.save_settings(patch, 0)
    saved = store.save_settings({'cover_max_total_mb': 5, 'cover_max_width': 720}, 0)
    assert saved['cover_max_width'] == 720 and saved['cover_max_total_mb'] == 5


@pytest.mark.parametrize('identity', [{'bot_id': '101'}, {'kind': 'private'}, {'target_id': '201'}])
def test_existing_subscription_recipient_cannot_change_or_reuse_history(store, identity):
    row = sub(store)
    gen = store.game_state(row['id'], 'ww')['generation']
    store.finish_initial(row['id'], 'ww', gen, {'a': {'new_done': True, 'tier': 1, 'end': 10000}})
    with pytest.raises(ValueError):
        store.save_subscription({'id': row['id'], **identity}, row['revision'])
    assert store.subscription(row['id']) == row
    assert store.states(row['id'], 'ww', gen)['a']['new_done'] is True
    store.delete_subscription(row['id'], row['revision'])
    replacement = sub(store, **identity)
    assert store.game_state(replacement['id'], 'ww')['status'] == 'initializing'


def test_complete_empty_snapshot_retains_one_previous_nonempty_diagnostic(store):
    full = {'game_id': 'ww', 'activities': [event()], 'complete': True, 'stale': False, 'checked_at': 100,
            'previous_snapshot': {'activities': ['must not nest']}}
    store.save_snapshot(full)
    empty = {'game_id': 'ww', 'activities': [], 'complete': True, 'stale': False, 'checked_at': 200}
    store.save_snapshot(empty)
    saved = store.snapshot('ww')
    assert saved['activities'] == []
    assert saved['previous_snapshot']['activities'][0]['uid'] == 'a'
    assert 'previous_snapshot' not in saved['previous_snapshot']
    empty['checked_at'] = 300
    store.save_snapshot(empty)
    assert store.snapshot('ww')['previous_snapshot']['checked_at'] == 100
    store.save_snapshot({**full, 'checked_at': 400})
    assert 'previous_snapshot' not in store.snapshot('ww')


def test_incomplete_empty_result_does_not_create_successful_empty_diagnostic(store):
    store.save_snapshot({'game_id': 'ww', 'activities': [event()], 'complete': True, 'checked_at': 100})
    store.save_snapshot({'game_id': 'ww', 'activities': [], 'complete': False, 'stale': True, 'checked_at': 200})
    assert 'previous_snapshot' not in store.snapshot('ww')
