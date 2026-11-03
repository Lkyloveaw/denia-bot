# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""地震事件纯逻辑：Wolfx 报文解析、过滤、格式化。

不 import nonebot / httpx / aiohttp，方便单测与复用。网络与接线分别在
`disaster_sources.py`、`plugins/disaster_warning.py`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Any, Mapping

TIMEZONE_LABELS = {
    'cenc_eew': '北京时间',
    'cenc': '北京时间',
    'jma_eew': '日本时间',
    'jma': '日本时间',
}

SOURCE_LABELS = {
    'cenc_eew': '中国地震预警网',
    'cenc': '中国地震台网',
    'jma_eew': '日本气象厅紧急地震速报',
    'jma': '日本气象厅地震情报',
}

SOURCE_SHORT = {
    'cenc_eew': 'CENC EEW',
    'cenc': 'CENC',
    'jma_eew': 'JMA EEW',
    'jma': 'JMA',
}


@dataclass(frozen=True)
class EarthquakeEvent:
    source: str
    event_id: str
    report_num: int
    title: str
    occurred_at: str
    place: str
    latitude: float | None = None
    longitude: float | None = None
    magnitude: float | None = None
    depth: float | None = None
    intensity: str = ''
    is_final: bool = False
    is_cancel: bool = False
    is_training: bool = False
    timezone_label: str = ''

    @property
    def key(self) -> str:
        return f'{self.source}:{self.event_id}:{self.report_num}'


def _text(value: Any) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _int_or(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def parse_float(value: Any) -> float | None:
    """兼容 4.2 / "4.2" / "M4.8" / "30km" / "调查中"。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in ('unknown', 'none', 'null', '---'):
        return None
    match = re.search(r'-?\d+(?:\.\d+)?', text)
    return float(match.group()) if match else None


def _numbered_items(data: Any) -> list[dict[str, Any]]:
    """Wolfx 列表是 {"No1": {...}, "No2": {...}}；兼容单条事件。"""
    if not isinstance(data, dict):
        return []
    items: list[tuple[int, dict[str, Any]]] = []
    for key, value in data.items():
        match = re.fullmatch(r'No(\d+)', str(key))
        if match and isinstance(value, dict):
            items.append((int(match.group(1)), value))
    items.sort(key=lambda pair: pair[0])
    if not items and _text(data.get('EventID')):
        return [data]
    return [value for _, value in items]


def parse_cenc_eew(data: Any) -> EarthquakeEvent | None:
    if not isinstance(data, dict):
        return None
    event_id = _text(data.get('EventID')) or _text(data.get('ID'))
    if not event_id:
        return None
    return EarthquakeEvent(
        source='cenc_eew',
        event_id=event_id,
        report_num=_int_or(data.get('ReportNum'), 1),
        title='中国地震预警',
        occurred_at=_text(data.get('OriginTime')),
        place=_text(data.get('HypoCenter')),
        latitude=parse_float(data.get('Latitude')),
        longitude=parse_float(data.get('Longitude')),
        magnitude=parse_float(data.get('Magnitude')),
        depth=parse_float(data.get('Depth')),
        intensity=_text(data.get('MaxIntensity')),
        timezone_label=TIMEZONE_LABELS['cenc_eew'],
    )


def parse_cenc_list(data: Any) -> list[EarthquakeEvent]:
    events: list[EarthquakeEvent] = []
    for item in _numbered_items(data):
        kind = _text(item.get('type')).lower()
        if kind and kind != 'reviewed':
            continue
        event_id = _text(item.get('EventID'))
        if not event_id:
            continue
        events.append(EarthquakeEvent(
            source='cenc',
            event_id=event_id,
            report_num=1,
            title='中国地震台网正式测定',
            occurred_at=_text(item.get('time')),
            place=_text(item.get('placeName')) or _text(item.get('location')),
            latitude=parse_float(item.get('latitude')),
            longitude=parse_float(item.get('longitude')),
            magnitude=parse_float(item.get('magnitude')),
            depth=parse_float(item.get('depth')),
            intensity=_text(item.get('intensity')),
            is_final=True,
            timezone_label=TIMEZONE_LABELS['cenc'],
        ))
    return events


def parse_jma_eew(data: Any) -> EarthquakeEvent | None:
    if not isinstance(data, dict):
        return None
    event_id = _text(data.get('EventID'))
    if not event_id:
        return None
    if data.get('isCancel') or data.get('isTraining') or data.get('isAssumption'):
        return None
    magnitude = parse_float(data.get('Magunitude'))
    if magnitude is None:
        magnitude = parse_float(data.get('Magnitude'))
    return EarthquakeEvent(
        source='jma_eew',
        event_id=event_id,
        report_num=_int_or(data.get('Serial'), 1),
        title='日本气象厅紧急地震速报',
        occurred_at=_text(data.get('OriginTime')),
        place=_text(data.get('Hypocenter')),
        latitude=parse_float(data.get('Latitude')),
        longitude=parse_float(data.get('Longitude')),
        magnitude=magnitude,
        depth=parse_float(data.get('Depth')),
        intensity=_text(data.get('MaxIntensity')),
        is_final=bool(data.get('isFinal')),
        timezone_label=TIMEZONE_LABELS['jma_eew'],
    )


def parse_jma_list(data: Any) -> list[EarthquakeEvent]:
    events: list[EarthquakeEvent] = []
    for item in _numbered_items(data):
        event_id = _text(item.get('EventID'))
        if not event_id:
            continue
        events.append(EarthquakeEvent(
            source='jma',
            event_id=event_id,
            report_num=1,
            title='日本气象厅地震情报',
            occurred_at=_text(item.get('time_full')) or _text(item.get('time')),
            place=_text(item.get('location')),
            latitude=parse_float(item.get('latitude')),
            longitude=parse_float(item.get('longitude')),
            magnitude=parse_float(item.get('magnitude')),
            depth=parse_float(item.get('depth')),
            intensity=_text(item.get('shindo')),
            is_final=True,
            timezone_label=TIMEZONE_LABELS['jma'],
        ))
    return events


def distance_km(lat1: float | None, lon1: float | None,
                lat2: float | None, lon2: float | None) -> float | None:
    if None in (lat1, lon1, lat2, lon2):
        return None
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(min(1.0, a)))


def local_distance(event: EarthquakeEvent, config: Mapping[str, Any]) -> float | None:
    local = config.get('local') or {}
    if not local.get('enabled'):
        return None
    lat = parse_float(local.get('latitude'))
    lon = parse_float(local.get('longitude'))
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    return distance_km(event.latitude, event.longitude, lat, lon)


def within_filters(event: EarthquakeEvent, config: Mapping[str, Any]) -> bool:
    if event.is_cancel or event.is_training:
        return False
    filters = config.get('filters') or {}
    min_mag = parse_float(filters.get('earthquake_min_magnitude'))
    if event.magnitude is not None and min_mag is not None and event.magnitude < min_mag:
        return False
    dist = local_distance(event, config)
    max_dist = parse_float(filters.get('earthquake_max_distance_km'))
    if dist is not None and max_dist is not None and max_dist > 0 and dist > max_dist:
        return False
    return True


def _headline(event: EarthquakeEvent) -> str:
    head = f'🚨 {event.title}'
    if event.source.endswith('_eew'):
        parts = [f'第 {event.report_num} 报']
        if event.is_final:
            parts.append('最终报')
        head += f'（{"，".join(parts)}）'
    return head


def event_lead(event: EarthquakeEvent, tone: str = 'neutral') -> str:
    """卡片/消息用的一句导语；首报中性，后续达妮娅。"""
    place = event.place or '震中调查中'
    magnitude = f'M{event.magnitude:.1f}' if event.magnitude is not None else '震级调查中'
    if tone != 'character':
        return f'{event.title}：{place} {magnitude}，{event.occurred_at or "时间未知"}。'
    parts = [f'{SOURCE_SHORT.get(event.source, event.source)}测定{place}附近 {magnitude}']
    if event.depth is not None:
        parts.append(f'深度 {event.depth:g} km')
    if event.intensity:
        label = '最大震度' if event.source.startswith('jma') else '最大烈度'
        parts.append(f'{label} {event.intensity}')
    return (
        '达妮娅感觉到了一下？' + '，'.join(parts) +
        '。先就近避险，远离玻璃和吊灯，关注余震和官方通报。'
    )


def format_event(event: EarthquakeEvent, config: Mapping[str, Any] | None = None,
                 tone: str = 'neutral') -> str:
    config = config or {}
    if tone == 'character':
        lines = [f'【地震预警】{event.title}']
        if event.source.endswith('_eew'):
            parts = [f'第 {event.report_num} 报']
            if event.is_final:
                parts.append('最终报')
            lines.append('，'.join(parts))
        lines.append(event_lead(event, 'character'))
        lines.append(f'来源：{SOURCE_LABELS.get(event.source, event.source)} / Wolfx 公开接口')
        lines.append('仅供参考，以官方发布为准。')
        return '\n'.join(lines)
    lines = [_headline(event)]
    tz = event.timezone_label or TIMEZONE_LABELS.get(event.source, '')
    lines.append(f'发震：{event.occurred_at or "未知"}' + (f'（{tz}）' if tz else ''))
    lines.append(f'震中：{event.place or "调查中"}')
    if event.magnitude is not None:
        lines.append(f'震级：M{event.magnitude:.1f}')
    else:
        lines.append('震级：调查中')
    if event.depth is not None:
        lines.append(f'深度：{event.depth:g} km')
    if event.intensity:
        label = '最大震度' if event.source.startswith('jma') else '最大烈度'
        lines.append(f'{label}：{event.intensity}')
    dist = local_distance(event, config)
    if dist is not None:
        lines.append(f'距本地：约 {dist:.0f} km')
    lines.append(f'来源：{SOURCE_LABELS.get(event.source, event.source)} / Wolfx 公开接口')
    lines.append('仅供参考，以官方发布为准。')
    return '\n'.join(lines)


def format_list(events: list[EarthquakeEvent], limit: int = 5) -> str:
    if not events:
        return '还没有收到地震数据，稍后再试。'
    limit = max(1, min(int(limit), 20))
    lines = [f'最近 {min(len(events), limit)} 条地震：']
    for index, event in enumerate(events[:limit], 1):
        magnitude = f'M{event.magnitude:.1f}' if event.magnitude is not None else 'M?'
        lines.append(
            f'{index}. {event.occurred_at or "时间未知"} '
            f'{event.place or "地点未知"} {magnitude}'
            f'（{SOURCE_SHORT.get(event.source, event.source)}）'
        )
    return '\n'.join(lines)


def sample_event() -> EarthquakeEvent:
    return EarthquakeEvent(
        source='cenc_eew',
        event_id='SAMPLE-20261031',
        report_num=1,
        title='中国地震预警',
        occurred_at='2026-10-31 12:00:00',
        place='样例地点',
        latitude=30.0,
        longitude=120.0,
        magnitude=5.2,
        depth=10.0,
        intensity='5.0',
        timezone_label=TIMEZONE_LABELS['cenc_eew'],
    )
