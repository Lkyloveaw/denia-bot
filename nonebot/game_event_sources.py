"""Public game calendars with complete, persistent source snapshots.

Only fixed public origins are requested. Raw HTTP entities and normalized
snapshots are committed together after the whole source has been validated.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx

from game_event_logic import GAMES, event_record, parse_time

JSON_LIMIT = 5 * 1024 * 1024
TEXT_LIMIT = 32 * 1024 * 1024
QUERY_TTL = 300
FAILURE_TTL = 60
AKE_BASE = 'https://data.akedata.wiki/'
PRTS_API = 'https://prts.wiki/api.php'
logger = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """The source did not provide a complete, valid calendar."""


def _relative_url(value, base=AKE_BASE):
    value = str(value or '').strip()
    decoded = unquote(unquote(value))
    parts = urlsplit(decoded)
    if (not value or parts.scheme or parts.netloc or parts.query or parts.fragment
            or decoded.startswith('/') or '\\' in decoded
            or any(p in ('.', '..') for p in decoded.split('/'))
            or any(ord(c) < 32 for c in decoded)):
        raise SourceError('unsafe AKEData relative path')
    return base + value


def _table(value, name):
    if not isinstance(value, dict):
        raise SourceError(f'{name}: expected object table')
    return value


def _first(printouts, *names):
    for name in names:
        value = printouts.get(name)
        if isinstance(value, list) and value:
            return value[0]
    return None


def _prts_time(value):
    if isinstance(value, dict):
        value = value.get('timestamp')
    return parse_time(value)


def _text(value, texts):
    if isinstance(value, dict):
        value = value.get('id', value.get('textId', value.get('text', '')))
    result = texts.get(str(value), value)
    if isinstance(result, dict):
        result = result.get('text', result.get('value', result.get('content', '')))
    return str(result or '')


def _ake_time(value):
    if isinstance(value, str) and '/' in value:
        try:
            return parse_time(datetime.strptime(value, '%Y/%m/%d %H:%M:%S').isoformat())
        except ValueError:
            return None
    return parse_time(value)


def _ake_cover(value):
    value = str(value or '').strip()
    if not value:
        return ''
    if '/' in value or '.' in value:
        return _relative_url(value)
    return _relative_url('public/images/assets/beyond/dynamicassets/gameplay/ui/sprites/'
                         'activity/' + quote(value, safe='') + '.png')


class SourceClient:
    def __init__(self, cache_dir, client=None, clock=time.time):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.cache_dir / 'source-cache.json'
        self._clock = clock
        self._owned = client is None
        self._client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        self._locks = {source: asyncio.Lock() for source in ('sra', 'akedata', 'prts')}
        self._state = {'entities': {}, 'snapshots': {}, 'ake_versions': {}, 'failures': {}}
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
            if (isinstance(data, dict) and isinstance(data.get('entities'), dict)
                    and isinstance(data.get('snapshots'), dict) and isinstance(data.get('ake_versions'), dict)):
                self._state = data
        except (OSError, ValueError):
            pass
        if not isinstance(self._state.get('failures'), dict):
            self._state['failures'] = {}
        self._prts_last = self._state.get('prts_last')

    async def close(self):
        if self._owned:
            await self._client.aclose()

    def _persist(self):
        temporary = self._path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(self._state, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            os.replace(temporary, self._path)
        except (OSError, ValueError) as exc:
            raise SourceError(f'cannot persist source cache: {exc}') from exc

    async def _json(self, url, pending, *, key=None, limit=JSON_LIMIT, params=None,
                    conditional=True, headers=None, prts=False):
        key = key or url
        old = self._state['entities'].get(key)
        validators = dict(headers or {})
        if conditional and isinstance(old, dict):
            if old.get('etag'):
                validators['If-None-Match'] = old['etag']
            if old.get('modified'):
                validators['If-Modified-Since'] = old['modified']
        for attempt in range(2):
            if prts:
                await self._wait_prts()
            async with self._client.stream('GET', url, params=params, headers=validators,
                                           timeout=20, follow_redirects=False) as response:
                if response.status_code == 304:
                    if isinstance(old, dict) and 'data' in old:
                        pending[key] = old
                        return old['data'], False
                    if attempt == 0:
                        validators.pop('If-None-Match', None)
                        validators.pop('If-Modified-Since', None)
                        continue
                    raise SourceError('304 response without cached entity')
                response.raise_for_status()
                length = response.headers.get('Content-Length')
                if length and int(length) > limit:
                    raise SourceError('JSON size limit exceeded')
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    body.extend(chunk)
                    if len(body) > limit:
                        raise SourceError('JSON size limit exceeded')
                try:
                    value = json.loads(body)
                except (ValueError, UnicodeError) as exc:
                    raise SourceError('invalid JSON response') from exc
                pending[key] = {'data': value, 'etag': response.headers.get('ETag'),
                                'modified': response.headers.get('Last-Modified')}
                return value, True
        raise SourceError('missing HTTP entity')

    async def fetch(self, game_id, force=False, include_gacha=True):
        if game_id not in GAMES:
            raise SourceError('unknown game')
        source = GAMES[game_id]['source']
        snapshot_key = game_id
        if source == 'akedata':
            snapshot_key += ':gacha' if include_gacha else ':no-gacha'
        async with self._locks[source]:
            pending = {}
            previous = self._state['snapshots'].get(snapshot_key)
            failure = self._state['failures'].get(snapshot_key)
            now = self._clock()
            if not force:
                if failure and 0 <= now - failure['at'] < FAILURE_TTL:
                    if previous:
                        result = copy.deepcopy(previous)
                        result.update(stale=True, error=failure['error'])
                        return result
                    raise SourceError(failure['error'])
                if (not failure and previous and previous.get('complete')
                        and not previous.get('stale') and not previous.get('error')
                        and 0 <= now - previous['checked_at'] < QUERY_TTL):
                    return copy.deepcopy(previous)
            try:
                if source == 'sra':
                    activities, version, changed = await self._sra(game_id, pending)
                    ake_version = None
                elif source == 'akedata':
                    activities, version, changed, ake_version = await self._ake(pending, include_gacha, force)
                else:
                    activities, version, changed = await self._prts(pending)
                    ake_version = None
                now = self._clock()
                snapshot = {'game_id': game_id, 'source': source, 'activities': activities,
                            'version': version, 'checked_at': now,
                            'fetched_at': now if changed or not previous else previous['fetched_at'],
                            'stale': False, 'error': '', 'complete': True,
                            'warning': '来源快照中的活动均已结束' if activities and all(e['end'] <= now for e in activities) else ''}
                old_state = self._state
                self._state = copy.copy(old_state)
                self._state['entities'] = {**old_state['entities'], **pending}
                if source == 'prts':
                    self._state['entities'] = {key: value for key, value in self._state['entities'].items()
                                              if not key.startswith('prts:') or key in pending}
                self._state['snapshots'] = {**old_state['snapshots'], snapshot_key: snapshot}
                self._state['ake_versions'] = dict(old_state['ake_versions'])
                self._state['failures'] = dict(old_state['failures'])
                self._state['failures'].pop(snapshot_key, None)
                if ake_version is not None:
                    self._state['ake_versions'][str(bool(include_gacha))] = ake_version
                    # Retain entities needed by either latest parsing mode; prune only after success.
                    keep = {key for v in self._state['ake_versions'].values() for key in v['keys']}
                    self._state['entities'] = {key: value for key, value in self._state['entities'].items()
                                               if not key.startswith('ake:') or key in keep}
                try:
                    self._persist()
                except SourceError:
                    self._state = old_state
                    raise
                return copy.deepcopy(snapshot)
            except (SourceError, httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError) as exc:
                message = f'{source}: {exc}'
                self._state['failures'][snapshot_key] = {'at': self._clock(), 'error': message}
                try:
                    self._persist()
                except SourceError:
                    logger.warning('could not persist source failure cooldown')
                if previous and isinstance(previous.get('activities'), list):
                    result = copy.deepcopy(previous)
                    result.update(stale=True, error=message)
                    return result
                raise SourceError(message) from exc

    async def _sra(self, game_id, pending):
        body, changed = await self._json(f'https://starrailassistant.top/api/v1/activity/{game_id}.json', pending)
        if isinstance(body, dict) and (body.get('error') or body.get('errors')):
            raise SourceError('source returned error response')
        if not isinstance(body, dict) or not isinstance(body.get('activities'), list):
            raise SourceError('missing activities array')
        events = []
        for row in body['activities']:
            if not isinstance(row, dict):
                continue
            kind = row.get('kind')
            tags = kind if isinstance(kind, list) else [kind] if kind else []
            event = event_record(game_id, row.get('name'), row.get('startTime'), row.get('endTime'),
                                 identity=str(row['id']) if row.get('id') is not None else '',
                                 description=row.get('description', ''), cover=row.get('cover', ''),
                                 url=row.get('url', ''), tags=tags)
            if event:
                events.append(event)
        if body['activities'] and not events:
            raise SourceError('all activity candidates are invalid')
        invalid = len(body['activities']) - len(events)
        if invalid:
            logger.warning('sra %s discarded %d invalid activity rows', game_id, invalid)
        return events, str(body.get('version', '')), changed

    async def _ake(self, pending, include_gacha, force):
        manifest, manifest_changed = await self._json(AKE_BASE + 'manifest.json', pending, key='ake:manifest')
        if not isinstance(manifest, dict) or not isinstance(manifest.get('versions'), list):
            raise SourceError('invalid AKEData manifest')
        latest = manifest.get('latest')
        if isinstance(latest, dict):
            latest = latest.get('id')
        selected = next((v for v in manifest['versions'] if isinstance(v, dict)
                         and str(v.get('id')) == str(latest) and v.get('tableCfgPath')), None)
        if not selected:
            raise SourceError('manifest latest version has no tables')
        table_base = _relative_url(str(selected['tableCfgPath']).rstrip('/') + '/')
        revision = [selected, manifest.get('updatedAt'), manifest.get('sharedRevision')]
        signature = hashlib.sha256(json.dumps(revision, sort_keys=True).encode()).hexdigest()
        version = f"{selected.get('id')}:{signature[:12]}:{int(include_gacha)}"
        old_version = self._state['ake_versions'].get(str(bool(include_gacha)))
        names = ['ActivityTable', 'TimeRangeTable', 'ActivityTagTable', 'I18nTextTable_CN']
        if include_gacha:
            names.append('GachaCharPoolTable')
        tables, keys, changed = {}, ['ake:manifest'], manifest_changed
        for name in names:
            scope = selected.get('gameVersion', selected['id']) if name == 'I18nTextTable_CN' else selected['id']
            key = f'ake:{scope}:{name}'
            keys.append(key)
            cached = self._state['entities'].get(key)
            if not force and old_version and old_version['signature'] == signature and cached:
                data = cached['data']
            else:
                data, updated = await self._json(table_base + name + '.json', pending, key=key,
                                                limit=TEXT_LIMIT if name == 'I18nTextTable_CN' else JSON_LIMIT)
                changed = changed or updated
            tables[name] = _table(data, name)
        texts, times = tables['I18nTextTable_CN'], tables['TimeRangeTable']
        tags, events, seen, candidates = tables['ActivityTagTable'], [], set(), 0
        def append_rows(rows, gacha=False):
            nonlocal candidates
            for entry_id, row in rows.items():
                if not isinstance(row, dict):
                    raise SourceError('invalid AKEData table row')
                time_field = 'clientTopTimeId' if gacha else 'timeId'
                time_id = row.get(time_field) or 'time_' + entry_id
                range_row = times.get(str(time_id))
                if not isinstance(range_row, dict):
                    if gacha and not row.get(time_field):
                        continue  # Permanent and unscheduled pools have no timing row.
                    raise SourceError(f'missing time range for {entry_id}')
                ranges = range_row.get('timeRangeList')
                if not isinstance(ranges, list):
                    raise SourceError('invalid AKEData time ranges')
                row_tags = ['寻访'] if gacha else []
                if gacha:
                    kind = {0: '限定寻访', 1: '启程寻访', 2: '常驻寻访',
                            3: '联合寻访', 4: '复刻寻访'}.get(row.get('type'))
                    if kind:
                        row_tags.append(kind)
                raw_tags = row.get('tagIds', row.get('tagId', []))
                if not isinstance(raw_tags, list):
                    raw_tags = [raw_tags]
                for tag_id in raw_tags:
                    tag = tags.get(str(tag_id), {})
                    if isinstance(tag, dict):
                        row_tags.append(_text(tag.get('name', ''), texts))
                cover = _ake_cover(row.get('tabImg'))
                description = _text(row.get('desc', ''), texts)
                if gacha and isinstance(row.get('upCharIds'), list) and row['upCharIds']:
                    description += '\nUP角色：' + '、'.join(str(x) for x in row['upCharIds'])
                for interval in ranges:
                    if not isinstance(interval, dict):
                        raise SourceError('invalid AKEData time interval')
                    if interval.get('closeTime') in (None, '', 0, '0'):
                        continue  # Permanent content has no reminder deadline.
                    candidates += 1
                    event = event_record('endfield', _text(row.get('name', row.get('poolName', '')), texts),
                                         _ake_time(interval.get('openTime')), _ake_time(interval.get('closeTime')),
                                         identity=('gacha:' if gacha else 'activity:') + entry_id,
                                         description=description,
                                         cover=cover, url='https://www.akedata.wiki/', tags=row_tags)
                    if event and event['uid'] not in seen:
                        seen.add(event['uid'])
                        events.append(event)
        append_rows(tables['ActivityTable'])
        if include_gacha:
            append_rows(tables['GachaCharPoolTable'], True)
        if candidates and not events:
            raise SourceError('all AKEData activity candidates are invalid')
        return events, version, changed, {'signature': signature, 'keys': keys}

    async def _wait_prts(self):
        if isinstance(self._prts_last, (int, float)) and math.isfinite(self._prts_last):
            delay = 20 - (self._clock() - self._prts_last)
            if delay > 0:
                await asyncio.sleep(delay)
        self._prts_last = self._clock()
        self._state['prts_last'] = self._prts_last
        self._persist()

    async def _prts(self, pending):
        cutoff = (datetime.fromtimestamp(self._clock(), timezone(timedelta(hours=8))) - timedelta(days=7)).strftime('%Y-%m-%d')
        query = ('[[分类:有活动信息的页面]][[活动结束时间::>=' + cutoff + ']]'
                 '|?活动开始时间|?活动结束时间|?兑换结束时间|?名称|?名称缩短'
                 '|?分类|?标题图文件名|?官网链接|limit=100')
        offset, visited, events, seen, candidates = 0, set(), [], set(), 0
        for page in range(5):
            if offset in visited:
                raise SourceError('partial PRTS repeated continuation')
            visited.add(offset)
            body, _ = await self._json(PRTS_API, pending, params={'action': 'ask', 'format': 'json',
                                      'query': query + f'|offset={offset}'},
                                      key=f'prts:{cutoff}:{offset}', conditional=False,
                                      headers={'Referer': 'https://prts.wiki/'}, prts=True)
            if isinstance(body, dict) and (body.get('error') or body.get('errors')):
                raise SourceError('source returned error response')
            if not isinstance(body, dict) or not isinstance(body.get('query'), dict):
                raise SourceError('invalid PRTS query response')
            rows = body['query'].get('results')
            if not isinstance(rows, dict):
                raise SourceError('missing PRTS results')
            for title, row in rows.items():
                if not isinstance(row, dict) or not isinstance(row.get('printouts'), dict):
                    raise SourceError('invalid PRTS activity row')
                fields = row['printouts']
                categories = fields.get('分类', [])
                if not isinstance(categories, list):
                    raise SourceError('invalid PRTS categories')
                category_names = [str(c.get('fulltext', '') if isinstance(c, dict) else c).removeprefix('分类:') for c in categories]
                if any(any(word in c for word in ('需要', '待清理', '待删除', '维护', '缺少', '错误')) for c in category_names):
                    continue
                candidates += 1
                exchange = _prts_time(_first(fields, '兑换结束日期', '兑换结束时间', '兑换结束'))
                cover = _first(fields, '标题图文件名')
                if isinstance(cover, dict):
                    cover = cover.get('fulltext', '')
                cover = 'https://prts.wiki/w/Special:Redirect/file/' + quote(str(cover), safe='') if cover else ''
                event = event_record('arknights', _first(fields, '活动名称', '名称') or title,
                    _prts_time(_first(fields, '开始日期', '活动开始时间', '开始时间')),
                    _prts_time(_first(fields, '结束日期', '活动结束时间', '结束时间')),
                    identity=title, url=row.get('fullurl') or 'https://prts.wiki/w/' + quote(title),
                    cover=cover, tags=category_names,
                    extra_times=[{'label': '兑换结束', 'at': exchange}] if exchange is not None else [])
                if event and event['uid'] not in seen:
                    events.append(event)
                    seen.add(event['uid'])
            continuation = body.get('query-continue-offset')
            if continuation is None:
                if candidates and not events:
                    raise SourceError('all PRTS activity candidates are invalid')
                return events, cutoff, True
            if isinstance(continuation, bool) or not str(continuation).isdigit() or int(continuation) <= offset:
                raise SourceError('partial PRTS invalid continuation')
            offset = int(continuation)
        raise SourceError('partial PRTS pagination exceeds five pages')

