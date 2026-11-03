"""Transactional game calendar state shared by the bot and control panel.

Connections and transactions live for one method call. Delivery happens outside
this module; callers commit only the events whose platform receipt succeeded.
"""
from __future__ import annotations

import copy
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from game_event_logic import GAMES

PATH = Path(__file__).resolve().parent / 'data' / 'game_events.sqlite3'
SCHEMA_VERSION = 1
DEFAULTS = {
    'revision': 0, 'enabled': True, 'group_enabled': True, 'private_enabled': True,
    'whitelist': [], 'blacklist': [], 'poll_minutes': 30, 'thresholds': [7, 3, 1],
    'notify_new': True, 'notify_on_subscribe': True, 'lookahead_days': 45,
    'max_new_per_cycle': 8, 'remind_only_active': True, 'cover_mode': 'image',
    'include_description': False, 'use_forward': False, 'cover_max_total_mb': 5,
    'cover_max_width': 720, 'source_sra': True, 'source_akedata': True,
    'source_prts': True, 'include_gacha': True, 'urgent_enabled': False,
    'urgent_trigger_days': 1, 'urgent_repeat_minutes': 120,
    'urgent_daily_limit': 3, 'quiet_start': 23, 'quiet_end': 8, 'aliases': {},
}
RANGES = {
    'poll_minutes': (15, 180), 'lookahead_days': (0, 365),
    'max_new_per_cycle': (1, 100), 'cover_max_total_mb': (1, 5),
    'cover_max_width': (64, 720), 'urgent_trigger_days': (1, 365),
    'urgent_repeat_minutes': (5, 1440), 'urgent_daily_limit': (1, 20),
    'quiet_start': (0, 23), 'quiet_end': (0, 23),
}


class ConflictError(ValueError):
    """A caller edited or completed work using a stale revision."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value):
        raise ValueError('账号必须为最多20位的正十进制字符串')
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'整数必须在{low}–{high}之间')
    return value


def _revision(value, expected):
    if type(value) is not int or value != expected:
        raise ConflictError('配置已变化，请重新读取后保存')


def _strings(value, limit, length):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f'列表最多{limit}项')
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > length:
            raise ValueError('列表包含空值或过长字符串')
        item = item.strip()
        if item not in result:
            result.append(item)
    return result


def _games(value, empty=True):
    result = _strings(value, len(GAMES), 40)
    if any(x not in GAMES for x in result) or (not empty and not result):
        raise ValueError('请选择有效游戏')
    return result


def _thresholds(value, inherit=True):
    if value is None and inherit:
        return None
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError('阈值最多10档')
    return sorted({_integer(x, 1, 365) for x in value}, reverse=True)


def _session(value):
    if not isinstance(value, str) or not re.fullmatch(r'(group|private):[1-9][0-9]{0,19}', value):
        raise ValueError('会话须为group:号码或private:号码')
    return value


def _patch(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError('包含未知字段')
    return copy.deepcopy(value)


def _subscription_values(data, new=False):
    for key in ('bot_id', 'target_id', 'user_id'):
        if key in data:
            _identifier(data[key])
    if 'kind' in data and data['kind'] not in ('group', 'private'):
        raise ValueError('无效会话类型')
    if 'enabled' in data and type(data['enabled']) is not bool:
        raise ValueError('enabled必须为布尔值')
    if 'games' in data:
        data['games'] = _games(data['games'], empty=not new)
    for key in ('include_keywords', 'exclude_keywords'):
        if key in data:
            data[key] = _strings(data[key], 50, 64)
    if 'thresholds' in data:
        data['thresholds'] = _thresholds(data['thresholds'])
    if 'cover_mode' in data and data['cover_mode'] not in (None, 'image', 'link', 'off'):
        raise ValueError('无效封面模式')
    if 'origin_session' in data and data['origin_session'] != '':
        _session(data['origin_session'])
    return data


_DDL = (
    'CREATE TABLE meta (key TEXT PRIMARY KEY, data TEXT NOT NULL)',
    'CREATE TABLE subscriptions (id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, kind TEXT NOT NULL, target_id TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(bot_id,kind,target_id))',
    'CREATE TABLE game_states (sub_id TEXT NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE, game_id TEXT NOT NULL, generation TEXT NOT NULL, status TEXT NOT NULL, initial TEXT, sent TEXT NOT NULL, PRIMARY KEY(sub_id,game_id))',
    'CREATE TABLE notifications (sub_id TEXT NOT NULL, game_id TEXT NOT NULL, generation TEXT NOT NULL, uid TEXT NOT NULL, new_done INTEGER NOT NULL, tier INTEGER, end REAL NOT NULL, at REAL NOT NULL, PRIMARY KEY(sub_id,game_id,generation,uid), FOREIGN KEY(sub_id,game_id) REFERENCES game_states(sub_id,game_id) ON DELETE CASCADE)',
    'CREATE TABLE snapshots (game_id TEXT PRIMARY KEY, data TEXT NOT NULL)',
    'CREATE TABLE deliveries (id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, kind TEXT NOT NULL, target_id TEXT NOT NULL, game_id TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL)',
    'CREATE TABLE urgent_targets (bot_id TEXT NOT NULL, user_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(bot_id,user_id))',
    'CREATE TABLE urgent_states (bot_id TEXT NOT NULL, user_id TEXT NOT NULL, uid TEXT NOT NULL, event TEXT NOT NULL, acked INTEGER NOT NULL, last_sent REAL NOT NULL, end REAL NOT NULL, PRIMARY KEY(bot_id,user_id,uid), FOREIGN KEY(bot_id,user_id) REFERENCES urgent_targets(bot_id,user_id) ON DELETE CASCADE)',
    'CREATE TABLE urgent_budgets (bot_id TEXT NOT NULL, user_id TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL, last_sent REAL NOT NULL, PRIMARY KEY(bot_id,user_id,day), FOREIGN KEY(bot_id,user_id) REFERENCES urgent_targets(bot_id,user_id) ON DELETE CASCADE)',
    'CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL, mode TEXT NOT NULL, games TEXT, bot_id TEXT NOT NULL, created_at REAL NOT NULL, claimed_at REAL, finished_at REAL, result TEXT, error TEXT NOT NULL)',
)


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else PATH

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError(f'不支持的schema_version: {version}')
            if version == 0:
                for statement in _DDL:
                    db.execute(statement)
                db.execute('PRAGMA user_version=1')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _meta(db, key, default):
        row = db.execute('SELECT data FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else copy.deepcopy(default)

    @staticmethod
    def _put_meta(db, key, value):
        db.execute('INSERT INTO meta(key,data) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data', (key, _json(value)))

    def settings(self):
        with self._db() as db:
            return self._meta(db, 'settings', DEFAULTS)

    def save_settings(self, patch, revision):
        patch = _patch(patch, set(DEFAULTS) - {'revision'})
        for key, value in patch.items():
            if type(DEFAULTS[key]) is bool and type(value) is not bool:
                raise ValueError(f'{key}必须为布尔值')
            if key in RANGES:
                _integer(value, *RANGES[key])
            elif key == 'thresholds':
                patch[key] = _thresholds(value, inherit=False)
            elif key in ('whitelist', 'blacklist'):
                patch[key] = [_session(x) for x in _strings(value, 500, 28)]
            elif key == 'cover_mode' and value not in ('image', 'link', 'off'):
                raise ValueError('无效封面模式')
            elif key == 'aliases':
                if not isinstance(value, dict) or any(k not in GAMES or not isinstance(v, str) or not v.strip() or len(v) > 40 for k, v in value.items()):
                    raise ValueError('无效游戏别名')
        with self._db() as db:
            data = self._meta(db, 'settings', DEFAULTS)
            _revision(revision, data['revision'])
            changed = any(data[k] != v for k, v in patch.items())
            data.update(patch)
            data['revision'] += 1
            self._put_meta(db, 'settings', data)
            if changed:
                self._reset_initial(db)
            return data

    @staticmethod
    def _reset_initial(db, sub_id=None):
        rows = db.execute('SELECT sub_id,game_id FROM game_states WHERE status=?' + (' AND sub_id=?' if sub_id else ''), ('initializing', sub_id) if sub_id else ('initializing',)).fetchall()
        for row in rows:
            db.execute('UPDATE game_states SET generation=?,initial=NULL,sent=? WHERE sub_id=? AND game_id=?', (str(uuid.uuid4()), '[]', row['sub_id'], row['game_id']))
            db.execute('DELETE FROM notifications WHERE sub_id=? AND game_id=?', (row['sub_id'], row['game_id']))

    def subscriptions(self):
        with self._db() as db:
            return [json.loads(x[0]) for x in db.execute('SELECT data FROM subscriptions ORDER BY rowid')]

    def subscription(self, id):
        with self._db() as db:
            row = db.execute('SELECT data FROM subscriptions WHERE id=?', (id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_subscription(self, payload, revision=None):
        allowed = {'id', 'bot_id', 'kind', 'target_id', 'enabled', 'games', 'thresholds', 'include_keywords', 'exclude_keywords', 'cover_mode'}
        patch = _subscription_values(_patch(payload, allowed))
        if 'id' in patch and (not isinstance(patch['id'], str) or not patch['id']):
            raise ValueError('无效订阅id')
        with self._db() as db:
            if patch.get('id'):
                row = db.execute('SELECT data FROM subscriptions WHERE id=?', (patch['id'],)).fetchone()
                if row is None:
                    raise ConflictError('订阅已删除')
            else:
                if not {'bot_id', 'kind', 'target_id'} <= patch.keys():
                    raise ValueError('缺少会话标识')
                row = db.execute('SELECT data FROM subscriptions WHERE bot_id=? AND kind=? AND target_id=?', (patch['bot_id'], patch['kind'], patch['target_id'])).fetchone()
            old = json.loads(row[0]) if row else None
            if old:
                _revision(revision, old['revision'])
                if any(key in patch and patch[key] != old[key] for key in ('bot_id', 'kind', 'target_id')):
                    raise ValueError('订阅目标不能修改，请删除后新建')
                data = {**old, **patch, 'revision': old['revision'] + 1}
            else:
                if revision is not None:
                    _revision(revision, 0)
                if db.execute('SELECT count(*) FROM subscriptions').fetchone()[0] >= 200:
                    raise ValueError('最多200个会话')
                _games(patch.get('games'), empty=False)
                data = {'id': str(uuid.uuid4()), 'revision': 0, 'enabled': True, 'games': [], 'thresholds': None, 'include_keywords': [], 'exclude_keywords': [], 'cover_mode': None, **patch}
            duplicate = db.execute('SELECT id FROM subscriptions WHERE bot_id=? AND kind=? AND target_id=? AND id<>?', (data['bot_id'], data['kind'], data['target_id'], data['id'])).fetchone()
            if duplicate:
                raise ConflictError('会话已存在')
            db.execute('INSERT INTO subscriptions(id,bot_id,kind,target_id,data) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET bot_id=excluded.bot_id,kind=excluded.kind,target_id=excluded.target_id,data=excluded.data', (data['id'], data['bot_id'], data['kind'], data['target_id'], _json(data)))
            old_games = set(old['games']) if old else set()
            for game in old_games - set(data['games']):
                db.execute('DELETE FROM game_states WHERE sub_id=? AND game_id=?', (data['id'], game))
            if old and any(old[k] != v for k, v in patch.items() if k != 'id'):
                self._reset_initial(db, data['id'])
            for game in set(data['games']) - old_games:
                db.execute('INSERT INTO game_states VALUES (?,?,?,?,NULL,?)', (data['id'], game, str(uuid.uuid4()), 'initializing', '[]'))
            return data

    def delete_subscription(self, id, revision):
        with self._db() as db:
            row = db.execute('SELECT data FROM subscriptions WHERE id=?', (id,)).fetchone()
            if not row:
                return False
            _revision(revision, json.loads(row[0])['revision'])
            db.execute('DELETE FROM subscriptions WHERE id=?', (id,))
            return True

    def game_state(self, sub_id, game_id):
        with self._db() as db:
            row = db.execute('SELECT generation,status,initial,sent FROM game_states WHERE sub_id=? AND game_id=?', (sub_id, game_id)).fetchone()
            if row:
                return {'generation': row['generation'], 'status': row['status'], 'initial': json.loads(row['initial']) if row['initial'] is not None else None, 'sent': json.loads(row['sent'])}
            return None

    @staticmethod
    def _current(db, sub_id, game_id, generation, initializing=False):
        row = db.execute('SELECT * FROM game_states WHERE sub_id=? AND game_id=? AND generation=?', (sub_id, game_id, generation)).fetchone()
        return row if row and (not initializing or row['status'] == 'initializing') else None

    def begin_initial(self, sub_id, game_id, generation, events):
        with self._db() as db:
            row = self._current(db, sub_id, game_id, generation, True)
            if not row or row['initial'] is not None:
                return False
            db.execute('UPDATE game_states SET initial=? WHERE sub_id=? AND game_id=?', (_json(events), sub_id, game_id))
            return True

    def initial_sent(self, sub_id, game_id, generation, uids):
        with self._db() as db:
            row = self._current(db, sub_id, game_id, generation, True)
            if not row or row['initial'] is None:
                return False
            sent = list(dict.fromkeys([*json.loads(row['sent']), *uids]))
            db.execute('UPDATE game_states SET sent=? WHERE sub_id=? AND game_id=?', (_json(sent), sub_id, game_id))
            return True

    @staticmethod
    def _record(db, sub_id, game_id, generation, uid, state, at):
        db.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(sub_id,game_id,generation,uid) DO UPDATE SET new_done=max(notifications.new_done,excluded.new_done),tier=CASE WHEN notifications.tier IS NULL THEN excluded.tier WHEN excluded.tier IS NULL THEN notifications.tier ELSE min(notifications.tier,excluded.tier) END,end=excluded.end,at=excluded.at', (sub_id, game_id, generation, uid, int(state['new_done']), state['tier'], state['end'], at))

    def finish_initial(self, sub_id, game_id, generation, baseline):
        with self._db() as db:
            if not self._current(db, sub_id, game_id, generation, True):
                return False
            for uid, state in baseline.items():
                self._record(db, sub_id, game_id, generation, uid, state, time.time())
            db.execute('UPDATE game_states SET status=?,initial=NULL,sent=? WHERE sub_id=? AND game_id=?', ('ready', '[]', sub_id, game_id))
            return True

    def states(self, sub_id, game_id, generation):
        with self._db() as db:
            if not self._current(db, sub_id, game_id, generation):
                return {}
            return {row['uid']: {'new_done': bool(row['new_done']), 'tier': row['tier'], 'end': row['end']} for row in db.execute('SELECT * FROM notifications WHERE sub_id=? AND game_id=? AND generation=? ORDER BY rowid', (sub_id, game_id, generation))}

    def record_plans(self, sub_id, game_id, generation, plans, at):
        with self._db() as db:
            if not self._current(db, sub_id, game_id, generation):
                return False
            for plan in plans:
                event = plan['event']
                self._record(db, sub_id, game_id, generation, event['uid'], {'new_done': 'new' in plan['reasons'], 'tier': plan['tier'], 'end': event['end']}, at)
            return True

    def snapshot(self, game_id):
        with self._db() as db:
            row = db.execute('SELECT data FROM snapshots WHERE game_id=?', (game_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_snapshot(self, snapshot):
        _games([snapshot['game_id']])
        snapshot = copy.deepcopy(snapshot)
        snapshot.pop('previous_snapshot', None)
        with self._db() as db:
            if snapshot.get('activities') == []:
                row = db.execute('SELECT data FROM snapshots WHERE game_id=?', (snapshot['game_id'],)).fetchone()
                previous = json.loads(row[0]) if row else {}
                diagnostic = previous.get('previous_snapshot')
                if snapshot.get('complete') is True and not snapshot.get('stale', False) and previous.get('activities'):
                    diagnostic = previous
                if diagnostic:
                    diagnostic = copy.deepcopy(diagnostic)
                    diagnostic.pop('previous_snapshot', None)
                    snapshot['previous_snapshot'] = diagnostic
            db.execute('INSERT INTO snapshots VALUES (?,?) ON CONFLICT(game_id) DO UPDATE SET data=excluded.data', (snapshot['game_id'], _json(snapshot)))

    def snapshots(self):
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM snapshots ORDER BY game_id')]

    def log_delivery(self, bot_id, kind, target_id, game_id, status, detail='', at=None):
        _identifier(bot_id)
        _identifier(target_id)
        _session(f'{kind}:{target_id}')
        if game_id != '':
            _games([game_id])
        if status not in ('success', 'failed', 'unknown', 'blocked'):
            raise ValueError('无效投递状态')
        with self._db() as db:
            db.execute('INSERT INTO deliveries(bot_id,kind,target_id,game_id,status,detail,at) VALUES (?,?,?,?,?,?,?)', (bot_id, kind, target_id, game_id, status, str(detail)[:300], time.time() if at is None else at))

    def deliveries(self, limit=30):
        _integer(limit, 1, 1000)
        with self._db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM deliveries ORDER BY at DESC,id DESC LIMIT ?', (limit,))]

    def urgent_targets(self):
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM urgent_targets ORDER BY rowid')]

    def urgent_target(self, bot_id, user_id):
        with self._db() as db:
            row = db.execute('SELECT data FROM urgent_targets WHERE bot_id=? AND user_id=?', (bot_id, user_id)).fetchone()
            return json.loads(row[0]) if row else None

    def save_urgent(self, payload, revision=None):
        patch = _patch(payload, {'bot_id', 'user_id', 'revision', 'enabled', 'games', 'include_keywords', 'exclude_keywords', 'origin_session'})
        if 'revision' in patch:
            supplied = patch.pop('revision')
            if revision is not None and supplied != revision:
                raise ConflictError('版本不一致')
            revision = supplied
        patch = _subscription_values(patch)
        if not {'bot_id', 'user_id'} <= patch.keys():
            raise ValueError('缺少紧急目标标识')
        with self._db() as db:
            row = db.execute('SELECT data FROM urgent_targets WHERE bot_id=? AND user_id=?', (patch['bot_id'], patch['user_id'])).fetchone()
            if row:
                old = json.loads(row[0])
                _revision(revision, old['revision'])
                data = {**old, **patch, 'revision': old['revision'] + 1}
            else:
                if revision is not None:
                    _revision(revision, 0)
                if db.execute('SELECT count(*) FROM urgent_targets').fetchone()[0] >= 200:
                    raise ValueError('最多200个紧急目标')
                data = {'enabled': True, 'include_keywords': [], 'exclude_keywords': [], 'origin_session': '', **patch, 'revision': 0}
            _games(data.get('games'), empty=False)
            db.execute('INSERT INTO urgent_targets VALUES (?,?,?) ON CONFLICT(bot_id,user_id) DO UPDATE SET data=excluded.data', (data['bot_id'], data['user_id'], _json(data)))
            return data

    def delete_urgent(self, bot_id, user_id, revision):
        with self._db() as db:
            row = db.execute('SELECT data FROM urgent_targets WHERE bot_id=? AND user_id=?', (bot_id, user_id)).fetchone()
            if not row:
                return False
            _revision(revision, json.loads(row[0])['revision'])
            db.execute('DELETE FROM urgent_targets WHERE bot_id=? AND user_id=?', (bot_id, user_id))
            return True

    def urgent_states(self, bot_id, user_id):
        with self._db() as db:
            return {row['uid']: {'event': json.loads(row['event']), 'acked': bool(row['acked']), 'last_sent': row['last_sent']} for row in db.execute('SELECT * FROM urgent_states WHERE bot_id=? AND user_id=? ORDER BY rowid', (bot_id, user_id))}

    def observe_urgent(self, bot_id, user_id, events):
        with self._db() as db:
            if not db.execute('SELECT 1 FROM urgent_targets WHERE bot_id=? AND user_id=?', (bot_id, user_id)).fetchone():
                return
            for event in events:
                db.execute('INSERT INTO urgent_states VALUES (?,?,?,?,?,?,?) ON CONFLICT(bot_id,user_id,uid) DO UPDATE SET event=excluded.event,end=excluded.end', (bot_id, user_id, event['uid'], _json(event), 0, 0, event['end']))

    def ack_urgent(self, bot_id, user_id, scope, now):
        scope = str(scope).casefold().strip()
        with self._db() as db:
            result = []
            for row in db.execute('SELECT uid,event FROM urgent_states WHERE bot_id=? AND user_id=? AND acked=0 AND end>? ORDER BY rowid', (bot_id, user_id, now)).fetchall():
                event = json.loads(row['event'])
                if not scope or scope in event['name'].casefold() or scope in row['uid'].casefold():
                    db.execute('UPDATE urgent_states SET acked=1 WHERE bot_id=? AND user_id=? AND uid=?', (bot_id, user_id, row['uid']))
                    result.append(row['uid'])
            return result

    def urgent_budget(self, bot_id, user_id, day):
        with self._db() as db:
            row = db.execute('SELECT count FROM urgent_budgets WHERE bot_id=? AND user_id=? AND day=?', (bot_id, user_id, day)).fetchone()
            last = db.execute('SELECT max(last_sent) FROM urgent_budgets WHERE bot_id=? AND user_id=?', (bot_id, user_id)).fetchone()[0]
            return {'count': row[0] if row else 0, 'last_sent': last or 0}

    def record_urgent_sent(self, bot_id, user_id, events, now, day):
        with self._db() as db:
            if not events or not db.execute('SELECT 1 FROM urgent_targets WHERE bot_id=? AND user_id=?', (bot_id, user_id)).fetchone():
                return
            for event in events:
                db.execute('UPDATE urgent_states SET last_sent=max(last_sent,?) WHERE bot_id=? AND user_id=? AND uid=?', (now, bot_id, user_id, event['uid']))
            db.execute('INSERT INTO urgent_budgets VALUES (?,?,?,?,?) ON CONFLICT(bot_id,user_id,day) DO UPDATE SET count=urgent_budgets.count+1,last_sent=max(urgent_budgets.last_sent,excluded.last_sent)', (bot_id, user_id, day, 1, now))

    @staticmethod
    def _job(row):
        data = dict(row)
        data['games'] = json.loads(data['games']) if data['games'] is not None else None
        data['result'] = json.loads(data['result']) if data['result'] is not None else None
        return data

    def request_refresh(self, mode='data', games=None, bot_id=''):
        if mode not in ('data', 'check'):
            raise ValueError('刷新模式须为data/check')
        if games is not None:
            games = _games(games)
        if bot_id != '':
            _identifier(bot_id)
        with self._db() as db:
            row = db.execute('SELECT * FROM jobs WHERE status IN (?,?) ORDER BY created_at LIMIT 1', ('pending', 'running')).fetchone()
            if row:
                existing = self._job(row)
                same_games = ((existing['games'] is None and games is None) or
                              (existing['games'] is not None and games is not None and set(existing['games']) == set(games)))
                if existing['mode'] != mode or existing['bot_id'] != bot_id or not same_games:
                    raise ConflictError('已有不同模式或范围的任务待处理，请完成后重新提交')
                return existing
            now = time.time()
            last = db.execute('SELECT max(created_at) FROM jobs').fetchone()[0]
            if last is not None and now - last < 60:
                raise ConflictError('刷新间隔至少60秒')
            id = str(uuid.uuid4())
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,NULL,NULL,NULL,?)', (id, 'pending', mode, _json(games) if games is not None else None, bot_id, now, ''))
            return self._job(db.execute('SELECT * FROM jobs WHERE id=?', (id,)).fetchone())

    def claim_job(self, now=None):
        now = time.time() if now is None else now
        with self._db() as db:
            row = db.execute('SELECT * FROM jobs WHERE status=? OR (status=? AND claimed_at<?) ORDER BY created_at LIMIT 1', ('pending', 'running', now - 600)).fetchone()
            if not row:
                return None
            db.execute('UPDATE jobs SET status=?,claimed_at=? WHERE id=?', ('running', now, row['id']))
            return self._job(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())

    def jobs(self, limit=20):
        _integer(limit, 1, 1000)
        with self._db() as db:
            return [self._job(row) for row in db.execute('SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT ?', (limit,))]

    def finish_job(self, id, result=None, error=''):
        with self._db() as db:
            db.execute('UPDATE jobs SET status=?,finished_at=?,result=?,error=? WHERE id=?', ('failed' if error else 'succeeded', time.time(), _json(result) if result is not None else None, str(error)[:300], id))

    def set_runtime(self, bots, at):
        bots = [_identifier(x) for x in _strings(bots, 200, 20)]
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
            raise ValueError('无效时间')
        with self._db() as db:
            self._put_meta(db, 'runtime', {'bots': bots, 'at': at})

    def runtime(self):
        with self._db() as db:
            return self._meta(db, 'runtime', {'bots': [], 'at': 0})

    def prune(self, now):
        cutoff = now - 30 * 86400
        with self._db() as db:
            db.execute('DELETE FROM notifications WHERE end<?', (cutoff,))
            db.execute('DELETE FROM urgent_states WHERE end<?', (cutoff,))
            db.execute('DELETE FROM urgent_budgets WHERE last_sent<?', (cutoff,))
            db.execute('DELETE FROM deliveries WHERE at<?', (cutoff,))
            db.execute('DELETE FROM jobs WHERE created_at<?', (cutoff,))
