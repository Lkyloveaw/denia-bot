# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""中国三类灾害的纯逻辑：Jian 气象/海啸、EQSC 台风。

只做解析、过滤、文案与去重键；网络在 `disaster_china_sources`，接线在
`plugins/disaster_warning.py`。字段以 2026-10 上游 v1.6.5 的
`weather_parser.py` / `tsunami_parser.py` / `typhoon_event_adapter.py` 与
`docs/EQSC API.md` 为准，并兼容 `Data` 外层包装。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import product
from typing import Any, Iterable, Mapping

from disaster_logic import distance_km, parse_float

CST = timezone(timedelta(hours=8))
WEATHER_COLORS = ('白色', '蓝色', '黄色', '橙色', '红色')
WEATHER_RANK = {name: index for index, name in enumerate(WEATHER_COLORS)}
WEATHER_RANK['未知'] = WEATHER_RANK['黄色']
TSUNAMI_LEVELS = ('信息', '蓝色', '黄色', '橙色', '红色')
TSUNAMI_RANK = {name: index for index, name in enumerate(TSUNAMI_LEVELS)}
TSUNAMI_RANK['解除'] = 0
TSUNAMI_RANK['未知'] = 0

_COLOR_RE = re.compile(r'红色|橙色|黄色|蓝色|白色')
_ENGLISH_COLORS = {
    'red': '红色', 'orange': '橙色', 'yellow': '黄色',
    'blue': '蓝色', 'white': '白色',
}
_NULLISH = {'', 'null', 'none', 'nameless', 'n/a', '-'}
_REGION_SUFFIXES = (
    '特别行政区', '维吾尔自治区', '壮族自治区', '回族自治区',
    '自治区', '自治州', '地区', '省', '市', '盟', '区', '县',
)


def _text(value: Any, limit: int | None = None) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    if text.lower() in _NULLISH:
        return ''
    return text[:limit] if limit else text


def _short(value: Any, limit: int = 90) -> str:
    text = _text(value)
    return text if len(text) <= limit else text[:limit] + '…'


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ('true', '1', 'yes', 'y', 'active'):
            return True
        if text in ('false', '0', 'no', 'n', 'inactive'):
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _hash(*parts: Any) -> str:
    payload = '|'.join(_text(part) for part in parts)
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]


def _inner(data: Any) -> Any:
    if isinstance(data, Mapping):
        for key in ('Data', 'data', 'payload'):
            value = data.get(key)
            if isinstance(value, (Mapping, list)):
                return value
    return data


def _first(raw: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ''):
            return value
    return None


def _region_key(value: Any) -> str:
    text = _text(value)
    for suffix in _REGION_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[:-len(suffix)]
    return text


def _region_match(alert_value: Any, config_value: Any) -> bool:
    """同层级名称任一匹配；有配置时，缺失字段不能证明匹配。"""
    config_keys = [_region_key(part) for part in _region_values(config_value)]
    alert_key = _region_key(alert_value)
    return not config_keys or bool(alert_key and alert_key in config_keys)


def _region_values(value: Any) -> list[str]:
    return [part.strip() for part in re.split(r'[,，、;；\r\n]+', _text(value)) if part.strip()]


def _weather_region_match(alert: WeatherAlert, config_value: Any) -> bool:
    """匹配完整地区路径，或由行政编码确认的上级预警；不猜测缺失地区。"""
    targets = _region_values(config_value)
    if not targets:
        return True

    parts = [part for part in (alert.province, alert.city, alert.district) if part]
    paths: set[str] = set()
    # 区县订阅必须命中区县；城市订阅可命中其下所有区县。仅各级后缀可省略，
    # 不再以“杭州”反向包含“杭州市滨江区”为由放过任意杭州区县。
    for start in range(len(parts)):
        for end in range(start + 1, len(parts) + 1):
            variants = [(part, _region_key(part)) for part in parts[start:end]]
            paths.update(''.join(names) for names in product(*variants))
    if any(target in paths for target in targets):
        return True

    code = alert.adcode
    if not re.fullmatch(r'[1-9][0-9]{5}', code):
        # 与地图解析一致：只接受已知 Jian 行政编码 + 时间戳格式。
        match = re.fullmatch(r'([1-9][0-9]{5})(?:[0-9]{8})?_[0-9]{12,14}', alert.alert_id)
        code = match.group(1) if match else ''
    if not code or alert.district:
        return False
    if code.endswith('0000') and alert.province and not alert.city:
        parents = [alert.province]
    elif code.endswith('00') and not code.endswith('0000') and alert.city:
        parents = [alert.city, alert.province + alert.city]
    else:
        return False
    # 必须是带行政后缀的完整上级名称，防止短名称前缀误命中另一地区。
    return any(target.startswith(parent) for target in targets for parent in parents
               if parent and _region_key(parent) != parent)


def _time_text(value: Any) -> str:
    text = _text(value)
    if not text:
        return '未知'
    text = text.replace('/', '-')
    if '（北京时间）' not in text and '北京时间' not in text:
        text += '（北京时间）'
    return text


def _parse_time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    for fmt in ('%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(CST).replace(tzinfo=None)
    return parsed


def _lat_text(value: float | None) -> str:
    if value is None:
        return '未知'
    return f'北纬 {value:.1f}°' if value >= 0 else f'南纬 {abs(value):.1f}°'


def _lon_text(value: float | None) -> str:
    if value is None:
        return '未知'
    return f'东经 {value:.1f}°' if value >= 0 else f'西经 {abs(value):.1f}°'


# ---------------------------------------------------------------- 气象预警


@dataclass
class WeatherAlert:
    alert_id: str
    title: str
    headline: str
    description: str
    code: str
    color: str
    effective_at: str
    issue_at: str
    province: str
    city: str
    district: str
    adcode: str
    latitude: float | None
    longitude: float | None

    @property
    def key(self) -> str:
        return f'jian:weather:{self.alert_id}:{_hash(self.effective_at, self.title, self.description)}'


def _issue_from_id(alert_id: str) -> str:
    if '_' not in alert_id:
        return ''
    suffix = alert_id.rsplit('_', 1)[-1]
    if len(suffix) < 12 or not suffix[:12].isdigit():
        return ''
    try:
        moment = datetime.strptime(suffix[:14] if len(suffix) >= 14 else suffix[:12],
                                    '%Y%m%d%H%M%S' if len(suffix) >= 14 else '%Y%m%d%H%M')
    except ValueError:
        return ''
    return moment.strftime('%Y-%m-%d %H:%M:%S')


def weather_color(raw: Mapping[str, Any], *texts: str) -> str:
    explicit = _text(_first(raw, 'color', 'level', 'severity', 'warningLevel'))
    mapped = _ENGLISH_COLORS.get(explicit.lower(), explicit)
    if mapped in WEATHER_RANK:
        return mapped
    for text in texts:
        found = _COLOR_RE.findall(_text(text))
        if found:
            return max(found, key=lambda item: WEATHER_RANK[item])
    return '未知'


def parse_jian_weather(data: Any) -> WeatherAlert | None:
    raw = _inner(data)
    if not isinstance(raw, Mapping):
        return None
    title = _text(raw.get('title')) or _text(raw.get('headline'))
    headline = _text(raw.get('headline'))
    description = _text(raw.get('description') or raw.get('text'))
    if not (title or headline or description):
        return None
    alert_id = _text(raw.get('id')) or f'w_{_hash(title, raw.get("effective"), description)}'
    effective = _text(raw.get('effective') or raw.get('effectiveAt') or raw.get('pubTime'))
    issue = _issue_from_id(alert_id) or _text(raw.get('issueTime') or raw.get('issue_time')) or effective
    code = _text(_first(
        raw, 'weather_type', 'weatherType', 'alertCode', 'alert_code',
        'code', 'warningType', 'warning_type',
    ))
    return WeatherAlert(
        alert_id=alert_id,
        title=title,
        headline=headline,
        description=description,
        code=code,
        color=weather_color(raw, title, headline, description),
        effective_at=effective,
        issue_at=issue,
        province=_text(raw.get('province')),
        city=_text(raw.get('city')),
        district=_text(raw.get('district') or raw.get('county')),
        adcode=_text(raw.get('adcode')),
        latitude=parse_float(raw.get('latitude')),
        longitude=parse_float(raw.get('longitude')),
    )


def parse_jian_weather_list(data: Any) -> list[WeatherAlert]:
    inner = _inner(data)
    if isinstance(inner, list):
        items = inner
    elif isinstance(inner, Mapping):
        candidates = inner.get('alerts') or inner.get('list') or inner.get('items')
        items = candidates if isinstance(candidates, list) else [inner]
    else:
        return []
    alerts: list[WeatherAlert] = []
    for item in items:
        alert = parse_jian_weather(item)
        if alert is not None:
            alerts.append(alert)
    return alerts


def within_weather_filters(alert: WeatherAlert, config: Mapping[str, Any]) -> bool:
    filters = config.get('filters') or {}
    min_color = _text(filters.get('weather_min_color')) or '黄色'
    if WEATHER_RANK.get(alert.color, WEATHER_RANK['未知']) < WEATHER_RANK.get(min_color, WEATHER_RANK['黄色']):
        return False
    local = config.get('local') or {}
    if local.get('enabled'):
        if not _region_match(alert.province, local.get('province')):
            return False
        if not _weather_region_match(alert, local.get('city')):
            return False
    return True


WEATHER_TYPE_WORDS = (
    '强对流', '暴雨', '雷电', '大风', '高温', '寒潮', '大雾', '冰雹',
    '暴雪', '道路结冰', '森林火险', '干旱', '台风', '沙尘暴', '霾',
    '低温', '霜冻', '海上大风',
)


def weather_type_name(alert: WeatherAlert) -> str:
    text = f'{alert.title} {alert.headline}'
    for word in WEATHER_TYPE_WORDS:
        if word in text:
            return word
    return alert.code or '气象预警'


def weather_lead(alert: WeatherAlert, tone: str = 'neutral') -> str:
    region = ' · '.join(part for part in (alert.province, alert.city, alert.district) if part)
    if tone != 'character':
        return alert.description or alert.headline or alert.title
    level = f'{alert.color}预警' if alert.color != '未知' else '气象预警'
    where = region or '你所在的地区'
    detail = _short(alert.description, 96)
    return (
        f'达妮娅提醒：{where}挂起了{level}。{detail} '
        '出门记得带伞，远离临时搭建物和广告牌。'
    )


def format_weather(alert: WeatherAlert, config: Mapping[str, Any] | None = None,
                   tone: str = 'neutral') -> str:
    region = ''.join(part for part in (alert.province, alert.city, alert.district) if part)
    if tone == 'character':
        lines = [f'【气象预警】达妮娅提醒', f'{alert.title or alert.headline}']
        meta = ' · '.join(part for part in (
            f'{alert.color}预警' if alert.color != '未知' else '',
            region,
            _time_text(alert.effective_at) if alert.effective_at else '',
        ) if part)
        if meta:
            lines.append(meta)
        lines.append(weather_lead(alert, 'character'))
        if alert.description:
            lines.append(f'预警原文：{alert.description.strip()}')
        lines.append('来源：中国气象局 / Jian Project')
        lines.append('仅供参考，以官方发布为准。')
        return '\n'.join(lines)
    lines = [f'【气象预警】{alert.title or alert.headline}']
    if alert.headline and alert.headline != alert.title:
        lines.append(alert.headline)
    if alert.color != '未知':
        lines.append(f'等级：{alert.color}预警')
    if region:
        lines.append(f'地区：{region}')
    if alert.effective_at:
        lines.append(f'生效：{_time_text(alert.effective_at)}')
    if alert.description:
        lines.append(f'正文：{alert.description.strip()}')
    lines.append('来源：中国气象局 / Jian Project')
    lines.append('仅供参考，以官方发布为准。')
    return '\n'.join(lines)


# ---------------------------------------------------------------- 海啸


@dataclass
class TsunamiAlert:
    alert_id: str
    title: str
    level: str
    headline: str
    origin_time: str
    number: int
    latitude: float | None
    longitude: float | None
    depth: float | None
    magnitude: float | None
    place: str
    org_unit: str
    description: str
    html_url: str

    @property
    def key(self) -> str:
        return (
            f'jian:tsunami:{self.alert_id}:{self.number}:'
            f'{_hash(self.title, self.level, self.origin_time)}'
        )


def parse_jian_tsunami(data: Any) -> TsunamiAlert | None:
    raw = _inner(data)
    if not isinstance(raw, Mapping):
        return None
    warning = raw.get('warningInfo') if isinstance(raw.get('warningInfo'), Mapping) else {}
    time_info = raw.get('timeInfo') if isinstance(raw.get('timeInfo'), Mapping) else {}
    shock = raw.get('shockInfo') if isinstance(raw.get('shockInfo'), Mapping) else {}
    details = raw.get('details') if isinstance(raw.get('details'), Mapping) else {}

    title = _text(raw.get('title') or raw.get('headline') or warning.get('title'))
    headline = _text(raw.get('headline') or warning.get('headline'))
    level = _text(raw.get('level') or warning.get('level'))
    if not title and level:
        title = f'海啸{level}警报'
    if not title:
        return None

    origin = _text(
        raw.get('originTime')
        or time_info.get('alarmDate')
        or time_info.get('issueTime')
        or time_info.get('publishTime')
        or time_info.get('updateDate')
    )
    number_raw = _first(raw, 'number', 'batch') or details.get('batch') or 1
    try:
        number = int(number_raw)
    except (TypeError, ValueError):
        number = 1

    alert_id = _text(raw.get('id') or raw.get('code'))
    if not alert_id:
        alert_id = 'tsunami_jian_' + _hash(number, title, raw.get('place'), origin)

    return TsunamiAlert(
        alert_id=alert_id,
        title=title,
        level=level,
        headline=headline,
        origin_time=origin,
        number=number,
        latitude=parse_float(raw.get('latitude') or shock.get('latitude')),
        longitude=parse_float(raw.get('longitude') or shock.get('longitude')),
        depth=parse_float(raw.get('depth') or shock.get('depth')),
        magnitude=parse_float(raw.get('magnitude') or shock.get('magnitude')),
        place=_text(raw.get('place') or shock.get('placeName') or raw.get('placeName')),
        org_unit=_text(raw.get('orgUnit') or warning.get('orgUnit')
                       or (raw.get('publishInfo') or {}).get('unitName')
                       or '自然资源部海啸预警中心'),
        description=_text(raw.get('description') or warning.get('description')),
        html_url=_text(raw.get('htmlUrl') or details.get('htmlUrl')),
    )


def parse_jian_tsunami_list(data: Any) -> list[TsunamiAlert]:
    inner = _inner(data)
    if isinstance(inner, list):
        items = inner
    elif isinstance(inner, Mapping):
        candidates = inner.get('alerts') or inner.get('list') or inner.get('items')
        items = candidates if isinstance(candidates, list) else [inner]
    else:
        return []
    alerts: list[TsunamiAlert] = []
    for item in items:
        alert = parse_jian_tsunami(item)
        if alert is not None:
            alerts.append(alert)
    return alerts


def tsunami_rank(level: Any) -> int:
    text = _text(level).replace('级', '')
    if '解除' in text:
        return -1
    return TSUNAMI_RANK.get(text, TSUNAMI_RANK['未知'])


def within_tsunami_filters(alert: TsunamiAlert, config: Mapping[str, Any]) -> bool:
    filters = config.get('filters') or {}
    min_level = _text(filters.get('tsunami_min_level')) or '信息'
    rank = tsunami_rank(alert.level)
    if rank < 0:  # 解除/取消类通告始终放行，避免只收到警报收不到解除。
        return True
    return rank >= TSUNAMI_RANK.get(min_level, 0)


def tsunami_lead(alert: TsunamiAlert, tone: str = 'neutral') -> str:
    if tone != 'character':
        return alert.description or f'{alert.title}（{alert.level}）'
    place = alert.place or '相关海域'
    magnitude = f'M{alert.magnitude:.1f}' if alert.magnitude is not None else '地震'
    level = f'{alert.level}海啸预警' if alert.level else '海啸预警'
    return (
        f'达妮娅提醒沿海的朋友：{place}附近发生 {magnitude}，{level}。'
        '先别去海边和港口，留意应急部门的疏散通知。'
    )


def format_tsunami(alert: TsunamiAlert, config: Mapping[str, Any] | None = None,
                   tone: str = 'neutral') -> str:
    if tone == 'character':
        lines = [f'【海啸预警】达妮娅提醒', f'{alert.title}（{alert.level}）']
        meta = ' · '.join(part for part in (
            f'第 {alert.number} 报',
            alert.place,
            _time_text(alert.origin_time) if alert.origin_time else '',
        ) if part)
        if meta:
            lines.append(meta)
        lines.append(tsunami_lead(alert, 'character'))
        lines.append('来源：自然资源部海啸预警中心 / Jian Project')
        lines.append('仅供参考，以官方发布为准。')
        return '\n'.join(lines)
    lines = [f'【海啸预警】{alert.title}']
    if alert.level:
        lines.append(f'等级：{alert.level}')
    if alert.place:
        lines.append(f'震中：{alert.place}')
    if alert.origin_time:
        lines.append(f'发震：{_time_text(alert.origin_time)}')
    metrics: list[str] = []
    if alert.magnitude is not None:
        metrics.append(f'M{alert.magnitude:.1f}')
    if alert.depth is not None:
        metrics.append(f'深度 {alert.depth:g} km')
    if alert.latitude is not None and alert.longitude is not None:
        metrics.append(f'{_lat_text(alert.latitude)} / {_lon_text(alert.longitude)}')
    if metrics:
        lines.append('参数：' + '，'.join(metrics))
    if alert.description:
        description = alert.description.strip()
        if len(description) > 420:
            description = description[:420] + '…'
        lines.append(f'正文：{description}')
    if alert.org_unit:
        lines.append(f'发布：{alert.org_unit}')
    lines.append('来源：自然资源部海啸预警中心 / Jian Project')
    lines.append('仅供参考，以官方发布为准。')
    return '\n'.join(lines)


# ---------------------------------------------------------------- 台风


@dataclass
class Typhoon:
    typhoon_id: str
    name_cn: str
    name_en: str
    level: str
    latitude: float | None
    longitude: float | None
    pressure: float | None
    wind_speed: float | None
    move_direction: str
    move_speed: Any
    updated_at: str
    is_active: bool
    future_time: str
    future_latitude: float | None
    future_longitude: float | None
    future_level: str

    @property
    def key(self) -> str:
        return (
            f'eqsc:typhoon:{self.typhoon_id}:{self.updated_at}:'
            f'{self.latitude or 0:.1f}:{self.longitude or 0:.1f}:'
            f'{self.wind_speed if self.wind_speed is not None else ""}'
        )

    @property
    def stop_key(self) -> str:
        return f'eqsc:typhoon:{self.typhoon_id}:stopped:{self.updated_at}'

    @property
    def display_name(self) -> str:
        if self.name_cn and self.name_en:
            return f'{self.name_cn}（{self.name_en}）'
        return self.name_cn or self.name_en or f'{self.typhoon_id} 号台风'


def _valid_track_nodes(nodes: Any) -> list[tuple[datetime, Mapping[str, Any]]]:
    if not isinstance(nodes, list):
        return []
    result: list[tuple[datetime, Mapping[str, Any]]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        moment = _parse_time(node.get('time'))
        if moment is None:
            continue
        latitude = parse_float(node.get('latitude'))
        longitude = parse_float(node.get('longitude'))
        wind = parse_float(node.get('windSpeed') or node.get('wind_speed'))
        if latitude is None or longitude is None:
            if wind is None:
                continue
        result.append((moment, node))
    result.sort(key=lambda item: item[0])
    return result


def _positive(value: Any) -> float | None:
    number = parse_float(value)
    return number if number is not None and number > 0 else None


def parse_eqsc_typhoon_item(data: Any) -> Typhoon | None:
    if not isinstance(data, Mapping):
        return None
    typhoon_id = _text(data.get('id'))
    if not typhoon_id:
        return None
    history = data.get('historyTrack') or data.get('history_track') or []
    nodes = _valid_track_nodes(history)
    if not nodes:
        return None
    _, latest = nodes[-1]
    level = _text(latest.get('typeNameCN') or latest.get('type'))
    latitude = parse_float(latest.get('latitude'))
    longitude = parse_float(latest.get('longitude'))
    pressure = _positive(latest.get('pressure'))
    wind_speed = _positive(latest.get('windSpeed') or latest.get('wind_speed'))
    direction = _text(latest.get('directionCN') or latest.get('direction'))
    speed = _first(latest, 'speed', 'moveSpeed')

    future_nodes = _valid_track_nodes(data.get('futureTrack') or data.get('future_track') or [])
    future_time = ''
    future_latitude = future_longitude = None
    future_level = ''
    if future_nodes:
        _, future = future_nodes[0]
        future_time = _text(future.get('time'))
        future_latitude = parse_float(future.get('latitude'))
        future_longitude = parse_float(future.get('longitude'))
        future_level = _text(future.get('typeNameCN') or future.get('type'))

    name_cn = _text(data.get('nameCN') or data.get('name'))
    name_en = _text(data.get('nameEN') or data.get('name_en'))
    return Typhoon(
        typhoon_id=typhoon_id,
        name_cn=name_cn,
        name_en=name_en,
        level=level,
        latitude=latitude,
        longitude=longitude,
        pressure=pressure,
        wind_speed=wind_speed,
        move_direction=direction,
        move_speed=speed,
        updated_at=_text(latest.get('time')),
        is_active=_as_bool(data.get('isActive'), True),
        future_time=future_time,
        future_latitude=future_latitude,
        future_longitude=future_longitude,
        future_level=future_level,
    )


def parse_eqsc_typhoon_list(data: Any) -> list[Typhoon]:
    items: Any = data
    if isinstance(data, Mapping):
        items = data.get('typhoon') if 'typhoon' in data else [data]
    if not isinstance(items, list):
        return []
    typhoons: list[Typhoon] = []
    for item in items:
        typhoon = parse_eqsc_typhoon_item(item)
        if typhoon is not None:
            typhoons.append(typhoon)
    return typhoons


def typhoon_distance(typhoon: Typhoon, config: Mapping[str, Any]) -> float | None:
    local = config.get('local') or {}
    if not local.get('enabled'):
        return None
    latitude = parse_float(local.get('latitude'))
    longitude = parse_float(local.get('longitude'))
    if latitude is None or longitude is None or latitude == 0 or longitude == 0:
        return None
    return distance_km(latitude, longitude, typhoon.latitude, typhoon.longitude)


def within_typhoon_filters(typhoon: Typhoon, config: Mapping[str, Any]) -> bool:
    local = config.get('local') or {}
    if not local.get('enabled'):
        return True
    distance = typhoon_distance(typhoon, config)
    if distance is None:
        return True
    filters = config.get('filters') or {}
    max_distance = parse_float(filters.get('typhoon_max_distance_km'))
    if max_distance is not None and max_distance > 0 and distance > max_distance:
        return False
    return True


def typhoon_lead(typhoon: Typhoon, tone: str = 'neutral') -> str:
    name = typhoon.display_name
    level = typhoon.level or '台风'
    bits = [f'{name} 目前{level}']
    if typhoon.pressure is not None:
        bits.append(f'中心气压 {typhoon.pressure:g} hPa')
    if typhoon.wind_speed is not None:
        bits.append(f'最大风速 {typhoon.wind_speed:g} m/s')
    move = _text(typhoon.move_direction)
    speed = _text(typhoon.move_speed)
    if speed.lower() == 'stnr':
        speed = '几乎停滞'
    elif speed:
        speed = f'{speed} km/h'
    if move or speed:
        bits.append('向' + '、'.join(part for part in (move, speed) if part) + '移动')
    if tone != 'character':
        return '，'.join(bits) + '。'
    return (
        '达妮娅看了下台风路径：' + '，'.join(bits) +
        '。路径还可能调整，沿海朋友先别急着安排海上活动。'
    )


def format_typhoon(typhoon: Typhoon, config: Mapping[str, Any] | None = None,
                   tone: str = 'neutral') -> str:
    config = config or {}
    if tone == 'character':
        lines = [f'【台风】达妮娅提醒', f'{typhoon.typhoon_id} {typhoon.display_name}']
        lines.append(typhoon_lead(typhoon, 'character'))
        if typhoon.updated_at:
            lines.append(f'更新：{_time_text(typhoon.updated_at)}')
        distance = typhoon_distance(typhoon, config)
        if distance is not None:
            lines.append(f'距本地：约 {distance:.0f} km')
        lines.append('来源：中国气象局 / EQSC')
        lines.append('仅供参考，以官方发布为准。')
        return '\n'.join(lines)
    lines = [f'【台风】{typhoon.typhoon_id} {typhoon.display_name}']
    if typhoon.level:
        lines.append(f'等级：{typhoon.level}')
    if typhoon.latitude is not None and typhoon.longitude is not None:
        lines.append(f'中心：{_lat_text(typhoon.latitude)} / {_lon_text(typhoon.longitude)}')
    metrics: list[str] = []
    if typhoon.pressure is not None:
        metrics.append(f'中心气压 {typhoon.pressure:g} hPa')
    if typhoon.wind_speed is not None:
        metrics.append(f'最大风速 {typhoon.wind_speed:g} m/s')
    if metrics:
        lines.append('，'.join(metrics))
    move = typhoon.move_direction
    speed = _text(typhoon.move_speed)
    if speed.lower() == 'stnr':
        speed = '几乎停滞'
    elif speed:
        speed = f'{speed} km/h'
    if move or speed:
        lines.append('移动：' + '，'.join(part for part in (move, speed) if part))
    if typhoon.updated_at:
        lines.append(f'更新：{_time_text(typhoon.updated_at)}')
    if typhoon.future_time:
        future = f'预计 {_time_text(typhoon.future_time)}'
        if typhoon.future_level:
            future += f'，强度 {typhoon.future_level}'
        if typhoon.future_latitude is not None and typhoon.future_longitude is not None:
            future += f'，位置 {_lat_text(typhoon.future_latitude)} / {_lon_text(typhoon.future_longitude)}'
        lines.append(future)
    distance = typhoon_distance(typhoon, config)
    if distance is not None:
        lines.append(f'距本地：约 {distance:.0f} km')
    lines.append('状态：' + ('活跃' if typhoon.is_active else '已停编'))
    lines.append('来源：中国气象局 / EQSC')
    lines.append('仅供参考，以官方发布为准。')
    return '\n'.join(lines)


def format_typhoon_stopped(typhoon: Typhoon, config: Mapping[str, Any] | None = None,
                           tone: str = 'neutral') -> str:
    if tone == 'character':
        lines = ['【台风停编】达妮娅提醒', f'{typhoon.typhoon_id} {typhoon.display_name} 已停编。']
        if typhoon.updated_at:
            lines.append(f'最后更新：{_time_text(typhoon.updated_at)}')
        lines.append('来源：中国气象局 / EQSC')
        lines.append('仅供参考，以官方发布为准。')
        return '\n'.join(lines)
    lines = ['【台风停编】', f'{typhoon.typhoon_id} {typhoon.display_name} 已停编。']
    if typhoon.updated_at:
        lines.append(f'最后更新：{_time_text(typhoon.updated_at)}')
    lines.append('来源：中国气象局 / EQSC')
    lines.append('仅供参考，以官方发布为准。')
    return '\n'.join(lines)


# ---------------------------------------------------------------- 首帧 all 基线


def iter_hinted_payloads(value: Any, hint: str = '') -> Iterable[tuple[str, Any]]:
    """递归产出 (最近类型提示, 子载荷)，用于 Jian `all` 首帧基线。"""
    if isinstance(value, Mapping):
        child_hint = hint
        for key, child in value.items():
            key_low = str(key).lower()
            if any(token in key_low for token in ('weather', 'tsunami', 'nmefc')):
                child_hint = key_low
            if isinstance(child, (Mapping, list)):
                yield from iter_hinted_payloads(child, child_hint)
        yield hint, value
    elif isinstance(value, list):
        for item in value:
            yield from iter_hinted_payloads(item, hint)
