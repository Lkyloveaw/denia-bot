"""Game calendars: normalized records and decisions, without framework or IO."""
from __future__ import annotations

import hashlib
import html
import math
import re
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
DAY = 86400
GAMES = {
    'sr': {'name': '崩坏：星穹铁道', 'source': 'sra', 'aliases': ['崩铁', '星铁', '星穹铁道', 'starrail', 'hsr']},
    'ys': {'name': '原神', 'source': 'sra', 'aliases': ['genshin', 'genshinimpact']},
    'zzz': {'name': '绝区零', 'source': 'sra', 'aliases': ['zenless', 'zenlesszonezero']},
    'ww': {'name': '鸣潮', 'source': 'sra', 'aliases': ['wuwa', 'wutheringwaves']},
    'nte': {'name': '异环', 'source': 'sra', 'aliases': ['neverness', 'nevernesstoeverness']},
    'ba-cn': {'name': '蔚蓝档案（国服）', 'source': 'sra', 'aliases': ['蔚蓝档案', '碧蓝档案', 'ba', '蔚蓝国服', 'ba国服']},
    'ba-global': {'name': '蔚蓝档案（国际服）', 'source': 'sra', 'aliases': ['蔚蓝档案', '碧蓝档案', '蔚蓝国际服', 'ba国际服']},
    'ba-jp': {'name': '蔚蓝档案（日服）', 'source': 'sra', 'aliases': ['蔚蓝档案', '碧蓝档案', '蔚蓝日服', 'ba日服']},
    'endfield': {'name': '明日方舟：终末地', 'source': 'akedata', 'aliases': ['终末地', 'zmd', '明日方舟终末地']},
    'arknights': {'name': '明日方舟', 'source': 'prts', 'aliases': ['舟', '方舟', 'ak']},
}

COMMANDS = {
    '活动订阅': ('subscribe', 'game_sub', '活动关注'),
    '活动退订': ('unsubscribe', 'game_unsub', '活动取消订阅'),
    '活动查询': ('query', 'game_query', '活动'),
    '活动游戏': ('games', 'game_games', '活动支持'),
    '活动列表': ('list', 'game_list', '活动订阅列表'),
    '活动过滤': ('filter', 'game_filter', '活动关键词'),
    '活动封面': ('cover', 'game_cover', '活动图片'),
    '活动开关': ('toggle', 'game_toggle', '活动推送'),
    '活动阈值': ('threshold', 'game_threshold', '活动提醒时间'),
    '活动状态': ('status', 'game_status'),
    '活动刷新': ('refresh', 'game_refresh'),
    '活动私人提醒': ('dm', 'game_dm', '活动私聊提醒'),
    '我已知晓': ('ack', 'game_ack', '已知晓', '活动已知晓'),
}


def clean_text(value):
    return html.unescape(re.sub(r'<[^>]*>', '', str(value or ''))).strip()


def parse_time(value):
    if value is None or isinstance(value, bool) or value == '':
        return None
    try:
        if isinstance(value, (int, float)) or re.fullmatch(r'[-+]?\d+(?:\.\d+)?', str(value)):
            number = float(value)
            if abs(number) >= 100_000_000_000:
                number /= 1000
        else:
            moment = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
            number = (moment if moment.tzinfo else moment.replace(tzinfo=TZ)).timestamp()
        if not math.isfinite(number):
            return None
        datetime.fromtimestamp(number, TZ)
        return number
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def event_record(game_id, name, start, end, *, identity='', description='', cover='', url='', tags=(), extra_times=()):
    start, end, name = parse_time(start), parse_time(end), clean_text(name)
    if not name or end is None or (start is not None and start >= end):
        return None
    # A mutable deadline must never be part of notification identity.
    raw_id = f'{game_id}\0{identity or name}\0{start if start is not None else ""}'
    return {'uid': hashlib.sha256(raw_id.encode()).hexdigest()[:24], 'game_id': game_id,
            'name': name, 'start': start, 'end': end, 'description': clean_text(description),
            'cover': str(cover or '').strip(), 'url': str(url or '').strip(),
            'tags': [clean_text(x) for x in tags if clean_text(x)], 'extra_times': list(extra_times)}


def resolve_games(tokens):
    found, unknown = [], []
    for token in tokens:
        key = str(token).casefold().strip()
        matches = [code for code, game in GAMES.items()
                   if key in [x.casefold() for x in [code, game['name'], *game['aliases']]]]
        if not matches:
            matches = [code for code, game in GAMES.items() if key and key in game['name'].casefold()]
            if len(matches) > 1 and not all(x.startswith('ba-') for x in matches):
                raise ValueError('游戏名不明确：' + '、'.join(GAMES[x]['name'] for x in matches))
        if not matches:
            unknown.append(str(token))
        for code in matches:
            if code not in found:
                found.append(code)
    if unknown:
        raise ValueError('未识别游戏：' + '、'.join(unknown))
    return found


def allowed(settings, kind, target_id):
    if kind not in ('group', 'private') or not settings.get(kind + '_enabled', True):
        return False
    session = f'{kind}:{target_id}'
    whitelist = settings.get('whitelist') or []
    return session not in (settings.get('blacklist') or []) and (not whitelist or session in whitelist)


def parse_command(text):
    text = str(text or '').strip()
    prefixed = text[:1] in ('.', '/', '／')
    if prefixed:
        text = text[1:].lstrip()
    head, _, rest = text.partition(' ')
    # split() also accepts tabs/newlines, but leaves the argument text intact.
    parts = text.split(maxsplit=1)
    if not parts:
        return None
    head, rest = parts[0].casefold(), parts[1] if len(parts) > 1 else ''
    for primary, (action, *aliases) in COMMANDS.items():
        if head in (primary, *aliases):
            if not prefixed and (head.isascii() or head in ('活动', '已知晓')):
                return None
            return action, rest.strip()
    return None


def thresholds(value):
    if value is None or value == '默认':
        return None
    if isinstance(value, str):
        if value.strip().casefold() in ('', '关闭', 'off', 'none'):
            return []
        value = re.split(r'[,，\s]+', value.strip())
    if not isinstance(value, (list, tuple)) or len(value) > 10:
        raise ValueError('阈值须为最多10个1–365天的整数')
    result = set()
    for item in value:
        if isinstance(item, bool) or not re.fullmatch(r'\d+', str(item)) or not 1 <= int(item) <= 365:
            raise ValueError('阈值须为1–365天的整数')
        result.add(int(item))
    return sorted(result, reverse=True)


def matches(event, policy):
    text = ' '.join([event['name'], event.get('description', ''), *event.get('tags', [])]).casefold()
    include, exclude = policy.get('include_keywords') or [], policy.get('exclude_keywords') or []
    return (not include or any(x.casefold() in text for x in include)) and not any(x.casefold() in text for x in exclude)


def relevant(events, policy, now):
    window = policy.get('lookahead_days', 45)
    return sorted([e for e in events if e['end'] > now and matches(e, policy)
                   and (not window or e.get('start') is None or e['start'] <= now + window * DAY)],
                  key=lambda e: (bool(e.get('start') and e['start'] > now), e['end'], e['name']))


def current_tier(event, policy, now):
    if event['end'] <= now or (policy.get('remind_only_active', True) and event.get('start') is not None and event['start'] > now):
        return None
    levels = policy.get('thresholds')
    if levels is None:
        levels = [7, 3, 1]
    hit = [d for d in levels if event['end'] - now <= d * DAY]
    return min(hit) if hit else None


def plans(events, states, policy, now):
    result, new_count = [], 0
    for event in relevant(events, policy, now):
        state = states.get(event['uid'], {})
        tier = current_tier(event, policy, now)
        old_tier = state.get('tier')
        if tier is not None and old_tier is not None and tier >= old_tier:
            tier = None
        reasons = []
        if policy.get('notify_new', True) and not state.get('new_done'):
            if tier is not None or new_count < policy.get('max_new_per_cycle', 8):
                reasons.append('new')
                if tier is None:
                    new_count += 1
        if tier is not None:
            reasons.append(f'd{tier}')
        if reasons:
            result.append({'event': event, 'reasons': reasons, 'tier': tier})
    return result


def format_event(event, now, description=False):
    remaining = max(0, event['end'] - now)
    marker = '🟥' if remaining <= DAY else '🟧' if remaining <= 3 * DAY else '🟩' if remaining <= 7 * DAY else '🟦'
    hours = max(1, math.ceil(remaining / 3600))
    span = f'{hours // 24}天{hours % 24}小时' if hours >= 24 else f'{hours}小时'
    when = lambda stamp: datetime.fromtimestamp(stamp, TZ).strftime('%Y-%m-%d %H:%M')
    lines = [f"{marker} {event['name'][:160]}（剩余约{span}）"]
    if event.get('start') is not None:
        lines.append(('未开始 · ' if event['start'] > now else '') + '开始：' + when(event['start']))
    lines.append('结束：' + when(event['end']) + '（北京时间）')
    if event.get('tags'):
        lines.append('分类：' + ' / '.join(event['tags']))
    for extra in event.get('extra_times', []):
        stamp = parse_time(extra.get('at'))
        if stamp is not None:
            lines.append(str(extra.get('label', '时间')) + '：' + when(stamp))
    if description and event.get('description'):
        lines.append(event['description'][:200] + ('…' if len(event['description']) > 200 else ''))
    return '\n'.join(lines)
