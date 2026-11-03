"""Authenticated calendar administration; reads caches and queues bot work only."""
from __future__ import annotations

import json
import re
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request

from game_event_logic import GAMES, allowed, matches, parse_time, relevant
from game_event_message import safe_url
from game_event_store import ConflictError, Store


def _call(operation):
    try:
        return operation()
    except ConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, '游戏活动数据暂时不可用') from exc


async def _body(req):
    raw = await req.body()
    if len(raw) > 128 * 1024:
        raise HTTPException(400, '请求内容过大')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, '请求须为有效 JSON') from exc
    if not isinstance(value, dict):
        raise HTTPException(400, '请求须为对象')
    return value


def _only(value, keys):
    if set(value) - set(keys):
        raise HTTPException(400, '包含未知字段')


def _error(value):
    text = str(value or '')
    text = re.sub(r'(https?://)[^\s/]*@', r'\1[已隐藏]@', text, flags=re.I)
    text = re.sub(r'((?:token|api[_-]?key|secret|password|authorization|cookie)\s*[=:]\s*)[^\s&;,]+', r'\1[已隐藏]', text, flags=re.I)
    return text[:300]


def _valid_event(event):
    return isinstance(event, dict) and bool(event.get('name')) and parse_time(event.get('end')) is not None


def _source(game_id, snapshot, settings, now):
    snapshot = snapshot or {}
    valid = [event for event in snapshot.get('activities', []) if _valid_event(event)]
    expired = sum(parse_time(event['end']) <= now for event in valid)
    source = GAMES[game_id]['source']
    return {
        'game_id': game_id, 'source': source, 'enabled': settings.get('source_' + source, True),
        'cached': bool(snapshot), 'checked_at': snapshot.get('checked_at'),
        'fetched_at': snapshot.get('fetched_at'), 'version': str(snapshot.get('version') or '')[:100],
        'stale': bool(snapshot.get('stale')), 'complete': bool(snapshot.get('complete', True)),
        'valid_count': len(valid) - expired, 'expired_count': expired,
        'invalid_count': len(snapshot.get('activities', [])) - len(valid),
        'backoff_until': snapshot.get('backoff_until'), 'error': _error(snapshot.get('error')),
        'warning': _error(snapshot.get('warning')),
        'has_previous': bool(snapshot.get('previous_snapshot')),
        'previous_fetched_at': (snapshot.get('previous_snapshot') or {}).get('fetched_at'),
    }


def _subscription(store, row, settings, online):
    access = allowed(settings, row['kind'], row['target_id'])
    reasons = []
    if not access:
        reasons.append('已被访问策略暂停')
    if not settings.get('enabled', True):
        reasons.append('全局推送已关闭')
    if not row['enabled']:
        reasons.append('订阅已停用')
    if not row['games']:
        reasons.append('未选择游戏')
    states = {}
    for game_id in row['games']:
        state = store.game_state(row['id'], game_id)
        states[game_id] = None if state is None else {
            'generation': state['generation'], 'status': state['status'],
            'initial_count': len(state.get('initial') or []), 'sent_count': len(state.get('sent') or []),
        }
    return {**row, 'access_allowed': access, 'paused': bool(reasons), 'pause_reasons': reasons,
            'bot_online': row['bot_id'] in online,
            'game_states': states}


def _urgent(row, settings, online):
    access = allowed(settings, 'private', row['user_id'])
    if row.get('origin_session'):
        kind, target_id = row['origin_session'].split(':', 1)
        access = access and allowed(settings, kind, target_id)
    reasons = []
    if not access:
        reasons.append('本人或来源会话已被访问策略暂停')
    if not settings.get('enabled', True):
        reasons.append('全局推送已关闭')
    if not settings.get('urgent_enabled'):
        reasons.append('紧急提醒全局关闭')
    if not row['enabled']:
        reasons.append('目标已停用')
    return {**row, 'access_allowed': access, 'paused': bool(reasons), 'pause_reasons': reasons,
            'bot_online': row['bot_id'] in online}


def overview(store):
    now = time.time()
    settings, runtime = store.settings(), store.runtime()
    online = set(runtime['bots'])
    runtime['possibly_offline'] = not runtime.get('at') or now - runtime['at'] > 600
    subs = [_subscription(store, row, settings, online) for row in store.subscriptions()]
    urgent = [_urgent(row, settings, online) for row in store.urgent_targets()]
    snapshots = {row['game_id']: row for row in store.snapshots()}
    deliveries = [{**row, 'detail': _error(row.get('detail'))} for row in store.deliveries(limit=50)]
    return {
        'now': now, 'settings': settings, 'runtime': runtime,
        'games': [{'id': key, 'name': settings.get('aliases', {}).get(key, game['name']),
                   'canonical_name': game['name'], 'source': game['source']} for key, game in GAMES.items()],
        'bot_ids': sorted(online | {row['bot_id'] for row in subs + urgent}),
        'subscriptions': subs, 'urgent_targets': urgent,
        'sources': [_source(key, snapshots.get(key), settings, now) for key in GAMES],
        'deliveries': deliveries,
        'delivery_counts': {status: sum(row['status'] == status for row in deliveries)
                            for status in ('success', 'failed', 'unknown', 'blocked')},
        'initializing_count': sum(state is not None and state['status'] == 'initializing'
                                  for row in subs for state in row['game_states'].values()),
        'jobs': [{**job, 'error': _error(job.get('error'))} for job in store.jobs(limit=20)],
    }


def preview(store, game_id, subscription_id, diagnostic=False):
    if game_id not in GAMES:
        raise HTTPException(400, '请选择受支持的游戏')
    settings = store.settings()
    subscription = store.subscription(subscription_id) if subscription_id else None
    if subscription_id and subscription is None:
        raise HTTPException(404, '订阅不存在')
    policy = {**settings, **(subscription or {})}
    if policy.get('thresholds') is None:
        policy['thresholds'] = settings['thresholds']
    current = store.snapshot(game_id) or {}
    snapshot = (current.get('previous_snapshot') or {}) if diagnostic else current
    events = []
    for item in snapshot.get('activities', []):
        if _valid_event(item):
            event = dict(item)
            event['start'], event['end'] = parse_time(item.get('start')), parse_time(item['end'])
            events.append(event)
    # Historical diagnostic data may be entirely expired. It is only displayed
    # by explicit request, never merged into the current calendar or job queue.
    events = sorted([event for event in events if matches(event, policy)], key=lambda event: event['end'], reverse=True) if diagnostic else relevant(events, policy, time.time())
    safe_events = []
    for item in events[:30]:
        safe_events.append({
            'uid': str(item.get('uid', ''))[:100], 'name': str(item['name'])[:160],
            'start': item.get('start'), 'end': item['end'], 'description': str(item.get('description', ''))[:200],
            'tags': [str(tag)[:64] for tag in item.get('tags', [])[:20]],
            'extra_times': [{'label': str(extra.get('label', '时间'))[:40], 'at': parse_time(extra.get('at'))}
                            for extra in item.get('extra_times', [])[:8]],
            'cover': item.get('cover', '') if safe_url(item.get('cover')) else '',
            'url': item.get('url', '') if safe_url(item.get('url')) else '',
        })
    return {'game_id': game_id, 'events': safe_events, 'total': len(events), 'omitted': max(0, len(events) - 30),
            'cached': bool(snapshot), 'stale': bool(snapshot.get('stale')), 'fetched_at': snapshot.get('fetched_at'),
            'checked_at': snapshot.get('checked_at'), 'error': _error(snapshot.get('error')),
            'warning': _error(current.get('warning')), 'diagnostic': diagnostic,
            'access_allowed': allowed(settings, subscription['kind'], subscription['target_id']) if subscription else True}


def router(authorized, mutate):
    routes = APIRouter(prefix='/api/game-events')

    @routes.get('')
    def get_overview(req: Request):
        authorized(req)
        return _call(lambda: overview(Store()))

    @routes.post('/settings')
    async def save_settings(req: Request):
        mutate(req)
        body = await _body(req)
        _only(body, {'revision', 'settings'})
        if not isinstance(body.get('settings'), dict):
            raise HTTPException(400, '缺少设置对象')
        return {'settings': _call(lambda: Store().save_settings(body['settings'], body.get('revision')))}

    @routes.post('/subscriptions')
    async def save_subscription(req: Request):
        mutate(req)
        body = await _body(req)
        revision = body.pop('revision', None)
        return {'subscription': _call(lambda: Store().save_subscription(body, revision))}

    @routes.post('/subscriptions/{id}/delete')
    async def delete_subscription(id: str, req: Request):
        mutate(req)
        body = await _body(req)
        _only(body, {'revision'})
        deleted = _call(lambda: Store().delete_subscription(id, body.get('revision')))
        if not deleted:
            raise HTTPException(404, '订阅不存在')
        return {'ok': True}

    @routes.post('/urgent')
    async def save_urgent(req: Request):
        mutate(req)
        body = await _body(req)
        revision = body.pop('revision', None)
        return {'target': _call(lambda: Store().save_urgent(body, revision))}

    @routes.post('/urgent/{bot_id}/{user_id}/delete')
    async def delete_urgent(bot_id: str, user_id: str, req: Request):
        mutate(req)
        body = await _body(req)
        _only(body, {'revision'})
        deleted = _call(lambda: Store().delete_urgent(bot_id, user_id, body.get('revision')))
        if not deleted:
            raise HTTPException(404, '紧急目标不存在')
        return {'ok': True}

    @routes.post('/refresh', status_code=202)
    async def refresh(req: Request):
        mutate(req)
        body = await _body(req)
        _only(body, {'mode', 'games', 'bot_id'})
        if body.get('mode') == 'check' and not body.get('bot_id'):
            raise HTTPException(400, '检查推送须明确指定机器人账号')
        return {'job': _call(lambda: Store().request_refresh(body.get('mode', 'data'), body.get('games'), body.get('bot_id', '')))}

    @routes.get('/preview')
    def get_preview(req: Request, game_id: str = '', subscription_id: str = '', diagnostic: bool = False):
        authorized(req)
        return _call(lambda: preview(Store(), game_id, subscription_id, diagnostic))

    return routes
