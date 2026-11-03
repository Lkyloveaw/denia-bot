"""Calendar orchestration. All outgoing IO is an injected, bounded sender."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

import game_event_logic as logic
from game_event_message import safe_url


def batches(items):
    batch, length = [], 0
    for item in items:
        event = item.get('event', item)
        links = [event.get(key, '') for key in ('url', 'cover') if safe_url(event.get(key, ''))]
        # Keep a full preferred URL even when other events have short text.
        link_size = min(2200, sum(len(url) + 8 for url in links))
        size = min(600, len(logic.format_event(event, event['end'] - 1, True)) + 180) + link_size
        if batch and (len(batch) >= 8 or length + size > 3100):
            yield batch
            batch, length = [], 0
        batch.append(item)
        length += size
    if batch:
        yield batch


class GameEventService:
    def __init__(self, store, sources, sender, *, clock=time.time, online=None, send_interval=2):
        self.store, self.sources, self.sender = store, sources, sender
        self.clock = clock
        self.online = online or (lambda bot_id: True)
        self.send_interval = send_interval
        self.lock = asyncio.Lock()
        self._last_send = {}

    def policy(self, sub, settings):
        policy = dict(settings)
        for key in ('thresholds', 'cover_mode'):
            if sub.get(key) is not None:
                policy[key] = sub[key]
        policy.update({key: sub.get(key, []) for key in ('include_keywords', 'exclude_keywords')})
        return policy

    def source_enabled(self, game, settings):
        return bool(settings.get('source_' + logic.GAMES[game]['source'], True))

    def usable(self, snapshot):
        return bool(snapshot and snapshot.get('complete', True)
                    and 0 <= self.clock() - snapshot.get('checked_at', 0) <= 6 * 3600)

    def sub_allowed(self, sub, settings):
        return bool(sub and sub.get('enabled', True) and settings.get('enabled', True)
                    and logic.allowed(settings, sub['kind'], sub['target_id'])
                    and self.online(sub['bot_id']))

    def urgent_allowed(self, target, settings):
        if not target or not target.get('enabled', True) or not settings.get('enabled', True) or not settings.get('urgent_enabled', False):
            return False
        if not logic.allowed(settings, 'private', target['user_id']) or not self.online(target['bot_id']):
            return False
        origin = target.get('origin_session', '')
        if origin:
            kind, ident = origin.split(':', 1)
            if not logic.allowed(settings, kind, ident):
                return False
        return True

    async def _fetch(self, game, settings, force=False):
        if not self.source_enabled(game, settings):
            result = dict(self.store.snapshot(game) or {})
            result.update(game_id=game, disabled=True, error='数据源已关闭', stale=True)
            return result
        try:
            result = await self.sources.fetch(game, force=force, include_gacha=settings.get('include_gacha', True))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            result = dict(self.store.snapshot(game) or {})
            result.update(game_id=game, source=logic.GAMES[game]['source'], stale=True,
                          error=f'{type(error).__name__}: {str(error)[:200]}',
                          last_attempt=self.clock())
            result.setdefault('activities', [])
            result.setdefault('checked_at', 0)
            result.setdefault('complete', False)
        self.store.save_snapshot(result)
        return result

    async def query(self, games, sub=None, force=False):
        async with self.lock:
            settings = self.store.settings()
            policy = self.policy(sub or {}, settings)
            results = []
            for game in games:
                snapshot = dict(await self._fetch(game, settings, force))
                age = self.clock() - snapshot.get('checked_at', 0)
                snapshot['activities'] = (logic.relevant(snapshot.get('activities', []), policy, self.clock())
                                          if age <= 7 * logic.DAY else [])
                if age > 7 * logic.DAY:
                    snapshot['error'] = '没有7天内的可用缓存；' + snapshot.get('error', '')
                results.append(snapshot)
            return results

    async def _send(self, *, bot_id, kind, target_id, events, title, settings, valid, game_id='', mode=None):
        def still_valid(cfg):
            return (logic.allowed(cfg, kind, target_id) and valid(cfg)
                    and all(e.get('event', e)['end'] > self.clock() for e in events))
        key = (bot_id, kind, target_id)
        delay = self.send_interval - (time.monotonic() - self._last_send.get(key, 0))
        if delay > 0:
            await asyncio.sleep(delay)
        # Fetch the live policy AFTER waiting, immediately before entering the sender.
        current = self.store.settings()
        if not still_valid(current):
            return False
        settings = {**settings, **{k: current[k] for k in ('cover_max_total_mb', 'cover_max_width', 'use_forward') if k in current}}
        try:
            await asyncio.wait_for(self.sender(bot_id=bot_id, kind=kind, target_id=target_id,
                                               events=events, title=title, mode=mode or settings.get('cover_mode', 'image'),
                                               settings=settings, valid=lambda: still_valid(self.store.settings())), timeout=45)
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            self.store.log_delivery(bot_id, kind, target_id, game_id, 'unknown', type(error).__name__, self.clock())
            return False
        except Exception as error:
            status = 'blocked' if getattr(error, 'blocked', False) else 'failed'
            self.store.log_delivery(bot_id, kind, target_id, game_id, status, type(error).__name__, self.clock())
            return False
        finally:
            self._last_send[key] = time.monotonic()
        self.store.log_delivery(bot_id, kind, target_id, game_id, 'success', at=self.clock())
        return True

    def _sub_valid(self, original, game, generation, settings):
        sub = self.store.subscription(original['id'])
        state = self.store.game_state(original['id'], game)
        return bool(self.sub_allowed(sub, settings) and self.source_enabled(game, settings)
                    and self.usable(self.store.snapshot(game))
                    and sub['revision'] == original['revision'] and state and state['generation'] == generation)

    async def _subscription_game(self, sub, game, snapshot):
        settings = self.store.settings()
        if not self.sub_allowed(sub, settings) or not self.source_enabled(game, settings) or not self.usable(snapshot):
            return 0
        state = self.store.game_state(sub['id'], game)
        if not state:
            return 0
        generation = state['generation']
        policy = self.policy(sub, settings)
        valid = lambda cfg: (cfg['revision'] == settings['revision']
                             and self._sub_valid(sub, game, generation, cfg))
        title = settings.get('aliases', {}).get(game) or logic.GAMES[game]['name']
        if snapshot.get('stale'):
            title += '（缓存核验：' + datetime.fromtimestamp(snapshot['checked_at'], logic.TZ).strftime('%m-%d %H:%M') + '）'
        sent_count = 0
        if state['status'] == 'initializing':
            if state.get('initial') is None:
                initial = logic.relevant(snapshot['activities'], policy, self.clock())
                if not self.store.begin_initial(sub['id'], game, generation, initial):
                    return 0
                state = self.store.game_state(sub['id'], game)
            initial = [e for e in state['initial'] if e['end'] > self.clock()]
            displayed = initial[:30]
            if settings.get('notify_on_subscribe', True):
                remaining = [e for e in displayed if e['uid'] not in state.get('sent', [])]
                groups = list(batches(remaining))
                for index, batch in enumerate(groups):
                    heading = title + ' · 订阅概览'
                    if index == len(groups) - 1 and len(initial) > 30:
                        heading += f'\n另有{len(initial)-30}项已纳入订阅，可在管理面板查看。'
                    if not await self._send(bot_id=sub['bot_id'], kind=sub['kind'], target_id=sub['target_id'],
                                            events=batch, title=heading, settings=policy, valid=valid, game_id=game):
                        return sent_count
                    self.store.initial_sent(sub['id'], game, generation, [e['uid'] for e in batch])
                    sent_count += 1
            baseline = {e['uid']: {'new_done': True, 'tier': logic.current_tier(e, policy, self.clock()), 'end': e['end']} for e in initial}
            if valid(self.store.settings()):
                self.store.finish_initial(sub['id'], game, generation, baseline)
            return sent_count
        states = self.store.states(sub['id'], game, generation)
        plans = logic.plans(snapshot['activities'], states, policy, self.clock())
        for batch in batches(plans):
            if not await self._send(bot_id=sub['bot_id'], kind=sub['kind'], target_id=sub['target_id'], events=batch,
                                    title=title + ' · 活动提醒', settings=policy, valid=valid, game_id=game):
                break
            self.store.record_plans(sub['id'], game, generation, batch, self.clock())
            sent_count += 1
        return sent_count

    async def cycle(self, *, mode='check', games=None, bot_id='', force=False, subscription_id=None):
        async with self.lock:
            settings = self.store.settings()
            subscriptions = [s for s in self.store.subscriptions()
                             if (not bot_id or s['bot_id'] == bot_id)
                             and (not subscription_id or s['id'] == subscription_id)]
            needed = set(games or [])
            if not games:
                for sub in subscriptions:
                    if self.sub_allowed(sub, settings):
                        needed.update(sub['games'])
                if not subscription_id:
                    for target in self.store.urgent_targets():
                        if (not bot_id or target['bot_id'] == bot_id) and self.urgent_allowed(target, settings):
                            needed.update(target['games'])
            snapshots = {game: await self._fetch(game, settings, force) for game in sorted(needed)}
            report = {'sent': 0, 'games': list(snapshots), 'errors': {g: s['error'] for g, s in snapshots.items() if s.get('error')}}
            if mode == 'data':
                return report
            for sub in subscriptions:
                for game in sub['games']:
                    if game in snapshots:
                        report['sent'] += await self._subscription_game(sub, game, snapshots[game])
            if not subscription_id:
                report['sent'] += await self._urgent_locked(bot_id=bot_id, games=games)
            return report

    async def urgent_cycle(self):
        async with self.lock:
            return await self._urgent_locked()

    async def _urgent_locked(self, bot_id='', games=None):
        settings = self.store.settings()
        if not settings.get('enabled', True) or not settings.get('urgent_enabled', False):
            return 0
        now = self.clock()
        local = datetime.fromtimestamp(now, logic.TZ)
        start, end = settings.get('quiet_start', 23), settings.get('quiet_end', 8)
        quiet = start != end and (start <= local.hour < end if start < end else local.hour >= start or local.hour < end)
        if quiet:
            return 0
        count = 0
        for target in self.store.urgent_targets():
            if (bot_id and target['bot_id'] != bot_id) or not self.urgent_allowed(target, settings):
                continue
            day = local.date().isoformat()
            budget = self.store.urgent_budget(target['bot_id'], target['user_id'], day)
            repeat = settings.get('urgent_repeat_minutes', 120) * 60
            if budget['count'] >= settings.get('urgent_daily_limit', 3) or now - budget['last_sent'] < repeat:
                continue
            events = []
            for game in target['games']:
                if games is not None and game not in games:
                    continue
                snapshot = self.store.snapshot(game)
                if not self.source_enabled(game, settings) or not self.usable(snapshot):
                    continue
                events.extend(e for e in snapshot.get('activities', [])
                              if 0 < e['end'] - now <= settings.get('urgent_trigger_days', 1) * logic.DAY
                              and (e.get('start') is None or e['start'] <= now) and logic.matches(e, target))
            self.store.observe_urgent(target['bot_id'], target['user_id'], events)
            states = self.store.urgent_states(target['bot_id'], target['user_id'])
            pending = sorted([e for e in events if not states.get(e['uid'], {}).get('acked')
                              and now - states.get(e['uid'], {}).get('last_sent', 0) >= repeat], key=lambda e: e['end'])[:8]
            if not pending:
                continue

            def valid(cfg):
                live = self.store.urgent_target(target['bot_id'], target['user_id'])
                if (cfg['revision'] != settings['revision'] or not self.urgent_allowed(live, cfg)
                        or live['revision'] != target['revision']):
                    return False
                current_time = self.clock()
                current_local = datetime.fromtimestamp(current_time, logic.TZ)
                quiet_start, quiet_end = cfg['quiet_start'], cfg['quiet_end']
                if quiet_start != quiet_end and (quiet_start <= current_local.hour < quiet_end
                        if quiet_start < quiet_end else current_local.hour >= quiet_start or current_local.hour < quiet_end):
                    return False
                current_budget = self.store.urgent_budget(target['bot_id'], target['user_id'], current_local.date().isoformat())
                if (current_budget['count'] >= cfg['urgent_daily_limit']
                        or current_time - current_budget['last_sent'] < cfg['urgent_repeat_minutes'] * 60):
                    return False
                current_states = self.store.urgent_states(target['bot_id'], target['user_id'])
                return all(not current_states.get(e['uid'], {}).get('acked') and e['end'] > current_time
                           and self.source_enabled(e['game_id'], cfg) and self.usable(self.store.snapshot(e['game_id']))
                           for e in pending)

            heading = '紧急活动提醒\n回复 .我已知晓 或 .活动私人提醒 停止'
            stale_times = [self.store.snapshot(e['game_id'])['checked_at'] for e in pending
                           if self.store.snapshot(e['game_id']).get('stale')]
            if stale_times:
                heading += '\n含缓存数据，最早核验：' + datetime.fromtimestamp(min(stale_times), logic.TZ).strftime('%m-%d %H:%M')
            if await self._send(bot_id=target['bot_id'], kind='private', target_id=target['user_id'],
                                events=pending, title=heading,
                                settings=settings, valid=valid, mode='link'):
                delivered_at = self.clock()
                self.store.record_urgent_sent(target['bot_id'], target['user_id'], pending, delivered_at,
                                              datetime.fromtimestamp(delivered_at, logic.TZ).date().isoformat())
                count += 1
        return count
