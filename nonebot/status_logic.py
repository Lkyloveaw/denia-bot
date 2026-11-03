#移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""状态卡与运维报表的纯逻辑层。

这一层只做三件事：**把数字变成人话**、**判断该不该告警**、**把采样聚合成报表**。
不 import `nonebot` / `muicebot` / `psutil`，所以 `tests/test_status_logic.py`
可以在没有机器人、没有硬件的情况下跑完。

分成五个部分：

1. 格式化：字节、速率、时长、百分比
2. 视图装配：采集快照 → 状态卡渲染器吃的 dict（含脱敏）
3. 判定：资源阈值状态机、掉线状态机、告警聚合
4. 报表：采样行 → 日报/周报的通用 section 结构
5. 杂项：推送目标解析、`HH:MM` 时间解析、月流量滚动

约定：所有接受 `now` 参数的函数都允许外部注入时间，测试里不依赖真实时钟。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------------------
# 一、格式化
# ---------------------------------------------------------------------------

_UNITS = ('B', 'KB', 'MB', 'GB', 'TB', 'PB')

#: 磁盘/内存百分比的三档配色边界，渲染层和文字摘要共用，保证同一个数字
#: 在卡片、日报、面板里是同一个颜色。
LEVEL_WARN = 70.0
LEVEL_BAD = 90.0


def format_bytes(value: float | int | None, digits: int = 2) -> str:
    """把字节数变成 `1.23 GB` 这样的短串。

    负数与 None 一律给 `--`：拿不到数据时显示 0 是在撒谎。
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return '--'
    if number < 0 or number != number:  # NaN 也走这里
        return '--'
    index = 0
    while number >= 1024 and index < len(_UNITS) - 1:
        number /= 1024
        index += 1
    if index == 0:
        return f'{int(number)} {_UNITS[index]}'
    return f'{number:.{digits}f} {_UNITS[index]}'


def format_speed(value: float | None, digits: int = 2) -> str:
    """字节/秒。`--` 表示没测到，不是 0。"""
    text = format_bytes(value, digits)
    return text if text == '--' else f'{text}/s'


def format_duration(seconds: float | None) -> str:
    """时长。超过一天用「天」，否则到分钟；不足一分钟给秒。

    运行时长和告警中断时长都走这里，所以两种地方的口径一致。
    """
    try:
        total = int(float(seconds))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return '--'
    if total < 0:
        return '--'
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f'{days} 天 {hours} 小时'
    if hours:
        return f'{hours} 小时 {minutes} 分'
    if minutes:
        return f'{minutes} 分 {secs} 秒'
    return f'{secs} 秒'


def format_pct(value: float | int | None, digits: int = 1) -> str:
    try:
        return f'{float(value):.{digits}f}%'  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return '--'


def percent_level(percent: float | None) -> str:
    """`good` / `warn` / `bad`。渲染层据此选颜色。"""
    try:
        value = float(percent)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 'good'
    if value >= LEVEL_BAD:
        return 'bad'
    if value >= LEVEL_WARN:
        return 'warn'
    return 'good'


def _tz(tz_offset: int = 8) -> timezone:
    return timezone(timedelta(hours=int(tz_offset)))


def format_clock(ts: float | None, *, tz_offset: int = 8) -> str:
    """时间戳 → `YYYY-MM-DD HH:MM:SS`（默认 UTC+8）。

    先当成 UTC 再换算，**不能**用 `datetime.fromtimestamp()` 加偏移：那个函数
    返回的已经是本地时间，再加 8 小时会重复计一次，在 UTC+8 的机器上偏 8 小时。
    """
    if ts is None:
        return '--'
    try:
        moment = datetime.fromtimestamp(float(ts), _tz(0)).astimezone(_tz(tz_offset))
    except (TypeError, ValueError, OSError):
        return '--'
    return moment.strftime('%Y-%m-%d %H:%M:%S')


def format_short_time(ts: float | None, *, tz_offset: int = 8) -> str:
    """`MM-DD HH:MM`，报表事件表用。"""
    if ts is None:
        return '--'
    try:
        moment = datetime.fromtimestamp(float(ts), _tz(0)).astimezone(_tz(tz_offset))
    except (TypeError, ValueError, OSError):
        return '--'
    return moment.strftime('%m-%d %H:%M')


# ---------------------------------------------------------------------------
# 二、视图装配
# ---------------------------------------------------------------------------

#: 四类脱敏开关，与上游同名，方便对照文档
REDACT_HOSTNAME = 'hostname'
REDACT_PROCESS = 'process'
REDACT_NETWORK = 'network'
REDACT_QQ = 'qq_stats'

HIDDEN = '已隐藏'
EMPTY = '--'


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if number != number else number


def _cpu_detail(cpu: dict[str, Any]) -> str:
    brand = str(cpu.get('brand') or 'CPU')
    cores = cpu.get('cores')
    threads = cpu.get('threads')
    freq = cpu.get('freq_mhz')
    head = f'{cores} 核' if cores else ''
    if threads and threads != cores:
        head = f'{head} / {threads} 线程' if head else f'{threads} 线程'
    if freq:
        head = f'{head} · {_as_float(freq) / 1000:.2f} GHz' if head else f'{_as_float(freq) / 1000:.2f} GHz'
    return f'{head} · {brand}' if head else brand


def build_view(snapshot: dict[str, Any], *, redact: Iterable[str] = (), now: float | None = None) -> dict[str, Any]:
    """把采集快照装成渲染器的输入。

    `snapshot` 是接线层从 psutil / OneBot 取到的**原始数字**；这里负责格式化、
    拼环、算颜色档位、执行脱敏。渲染层不再做任何单位换算，避免同一个数字在
    两个地方各算一遍。
    """
    now = time.time() if now is None else float(now)
    cpu = dict(snapshot.get('cpu') or {})
    memory = dict(snapshot.get('memory') or {})
    swap = dict(snapshot.get('swap') or {})
    network = dict(snapshot.get('network') or {})
    process = dict(snapshot.get('process') or {})
    system = dict(snapshot.get('system') or {})
    bot = dict(snapshot.get('bot') or {})

    resources = [
        {
            'key': 'cpu',
            'title': 'CPU',
            'percent': round(_as_float(cpu.get('percent')), 1),
            'detail': _cpu_detail(cpu),
        },
        {
            'key': 'memory',
            'title': '内存',
            'percent': round(_as_float(memory.get('percent')), 1),
            'detail': (
                f'{format_bytes(memory.get("used"))} / {format_bytes(memory.get("total"))}'
                if memory.get('total') else EMPTY
            ),
        },
        {
            'key': 'swap',
            'title': 'SWAP',
            'percent': round(_as_float(swap.get('percent')), 1),
            'detail': (
                f'{format_bytes(swap.get("used"))} / {format_bytes(swap.get("total"))}'
                if swap.get('total') else EMPTY
            ),
        },
    ]

    disks = []
    for item in snapshot.get('disks') or []:
        percent = item.get('percent')
        disks.append({
            'path': str(item.get('path') or '--'),
            'used': format_bytes(item.get('used')),
            'total': format_bytes(item.get('total')),
            'percent': round(_as_float(percent), 1),
            'percent_text': format_pct(percent) if percent is not None else EMPTY,
            'level': percent_level(percent),
        })

    chart = []
    for point in network.get('history') or []:
        # 状态文件里存的是 (ts, up, down) 三元组；SQLite 采样行是 dict。
        # 两种都吃，免得采样来源一换，折线就静默消失。
        if isinstance(point, dict):
            try:
                ts, up, down = point.get('ts'), point.get('up'), point.get('down')
            except AttributeError:
                continue
        else:
            try:
                ts, up, down = point[0], point[1], point[2]
            except (TypeError, IndexError, KeyError):
                continue
        try:
            chart.append({'ts': float(ts), 'up': _as_float(up), 'down': _as_float(down)})
        except (TypeError, ValueError):
            continue

    view = {
        'generated_at': format_clock(now),
        'generated_ts': now,
        'compact': bool(snapshot.get('compact')),
        'bot': {
            'nick': str(bot.get('nick') or 'Bot'),
            'self_id': str(bot.get('self_id') or ''),
            'avatar': bot.get('avatar'),
            'online': bot.get('online'),
            'online_text': (
                '在线' if bot.get('online') is True
                else '离线' if bot.get('online') is False
                else EMPTY
            ),
            'protocol': str(bot.get('protocol') or ''),
            'protocol_version': str(bot.get('protocol_version') or ''),
            'friends': bot.get('friends'),
            'groups': bot.get('groups'),
            'members': bot.get('members'),
            'sent': bot.get('sent'),
            'recv': bot.get('recv'),
            'sent_scope': str(bot.get('sent_scope') or ''),
            'recv_scope': str(bot.get('recv_scope') or ''),
            'run_time': format_duration(snapshot.get('uptime')),
        },
        'resources': resources,
        'disks': disks,
        'disk_io': {
            'read': format_speed(snapshot.get('disk_io', {}).get('read_rate')),
            'write': format_speed(snapshot.get('disk_io', {}).get('write_rate')),
        },
        'network': {
            'up': format_speed(network.get('up_rate')),
            'down': format_speed(network.get('down_rate')),
            'up_total': format_bytes(network.get('sent_total')),
            'down_total': format_bytes(network.get('recv_total')),
            'month': str(network.get('month_label') or ''),
            'month_up': format_bytes(network.get('month_up')),
            'month_down': format_bytes(network.get('month_down')),
            'interface': str(network.get('interface') or EMPTY),
            'chart': chart,
        },
        'websites': [
            {
                'name': str(item.get('name') or '站点'),
                'status': str(item.get('status') or EMPTY),
                'delay': f'{_as_float(item["delay_ms"]):.0f}ms' if item.get('delay_ms') is not None else EMPTY,
                'ok': bool(item.get('ok')),
            }
            for item in snapshot.get('websites') or []
        ],
        'process': {
            'total': process.get('total'),
            'running': process.get('running'),
            'top': [
                {
                    'name': str(item.get('name') or EMPTY),
                    'pid': item.get('pid'),
                    # 用整机口径：psutil 的 per-process cpu_percent 是「单核百分数」，
                    # 20 核机器上会显示 743.8%，读者没法判断到底是多忙。
                    'cpu': format_pct(item.get('cpu_pct_total', item.get('cpu'))),
                    'mem': format_pct(item.get('mem')),
                }
                for item in process.get('top') or []
            ],
        },
        'system': [
            {'label': str(item.get('label') or ''), 'value': str(item.get('value') or '')}
            for item in snapshot.get('system') or []
        ],
        'uptime': format_duration(snapshot.get('uptime')),
        'footer': str(snapshot.get('footer') or '本地渲染 · 数据不出本机'),
    }
    apply_redaction(view, redact)
    return view


def apply_redaction(view: dict[str, Any], rules: Iterable[str]) -> dict[str, Any]:
    """按四类开关就地脱敏，返回同一个 dict。

    与上游口径一致：**只隐藏展示，不删除本地记录**。四个开关互相独立，
    一项不勾就是完全不脱敏。
    """
    active = {str(item).strip().lower() for item in rules or () if str(item).strip()}
    if not active:
        return view

    if REDACT_HOSTNAME in active:
        for row in view.get('system') or []:
            if str(row.get('label')) in {'主机名', 'Host'}:
                row['value'] = HIDDEN

    if REDACT_PROCESS in active:
        for row in (view.get('process') or {}).get('top') or []:
            row['name'] = HIDDEN

    if REDACT_NETWORK in active:
        network = view.get('network') or {}
        network['up'] = EMPTY
        network['down'] = EMPTY
        network['up_total'] = EMPTY
        network['down_total'] = EMPTY
        network['month_up'] = EMPTY
        network['month_down'] = EMPTY
        network['interface'] = HIDDEN
        network['chart'] = []
        view['disk_io'] = {'read': EMPTY, 'write': EMPTY}
        view['websites'] = []

    if REDACT_QQ in active:
        for key in ('friends', 'groups', 'members', 'sent', 'recv'):
            if key in (view.get('bot') or {}):
                view['bot'][key] = None

    return view


def summary_text(view: dict[str, Any]) -> str:
    """状态卡的文字摘要，出图失败或不想出图时用。

    只挑最要紧的几行：资源、磁盘、网络、进程、QQ。渲染失败时这份文本就是
    兜底，所以它必须能独立读懂，不能写成「详见上图」。
    """
    lines = ['服务器状态']
    for item in view.get('resources') or []:
        lines.append(f'{item["title"]}: {format_pct(item.get("percent"))}（{item.get("detail")}）')
    for disk in (view.get('disks') or [])[:2]:
        lines.append(f'磁盘 {disk["path"]}: {disk["percent_text"]}（{disk["used"]} / {disk["total"]}）')
    network = view.get('network') or {}
    lines.append(f'网络: ↑ {network.get("up")} ↓ {network.get("down")}，本月 ↑ {network.get("month_up")} ↓ {network.get("month_down")}')
    top = (view.get('process') or {}).get('top') or []
    if top:
        names = '、'.join(str(item.get('name')) for item in top[:3])
        lines.append(f'Top 进程: {names}')
    bot = view.get('bot') or {}
    if bot.get('friends') is not None:
        lines.append(
            f'好友 {bot.get("friends")} · 群 {bot.get("groups")} · 群员 {bot.get("members")}'
            f' · 收 {bot.get("recv")} / 发 {bot.get("sent")}'
        )
    lines.append(f'运行 {view.get("uptime")} · {view.get("generated_at")}')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 三、判定
# ---------------------------------------------------------------------------


def evaluate_rule(
    state: dict[str, Any] | None,
    value: float | None,
    threshold: float,
    *,
    sustain_samples: int = 3,
    cooldown_seconds: float = 1800.0,
    now: float | None = None,
) -> dict[str, Any]:
    """资源阈值状态机：连续超限才算数，告警后冷却，回落即恢复。

    返回的 `state` 可以直接存进 JSON；`alert` / `recovery` 是这一刻要不要发消息。

    几个刻意的选择：

    - **`value` 为 None 时不重置计数**：读不到 CPU（权限、容器限制）不等于
      「指标正常」，重置会让告警永远攒不够次数。
    - **冷却期内继续累计**：超限持续时冷却一到就发，不会被冷却窗口吞掉整段故障。
    - **告警中不重复发**：同一个规则在恢复前只发一次。
    """
    now = time.time() if now is None else float(now)
    st = dict(state or {})
    count = int(st.get('count') or 0)
    alerting = bool(st.get('alerting'))
    last_sent = _as_float(st.get('last_sent'))
    alert_since = _as_float(st.get('alert_since'))
    threshold = _as_float(threshold)
    sustain = max(1, int(sustain_samples))

    try:
        current = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        current = None

    if current is None or current != current:
        st['count'] = count
        return {'state': st, 'alert': False, 'recovery': False, 'duration': 0.0, 'value': None}

    above = current >= threshold
    alert = False
    recovery = False
    duration = 0.0

    if above:
        count += 1
        if not alerting and count >= sustain:
            # last_sent 为 0 表示「从没发过」，不是「刚发过」——否则新装的
            # 机器在冷却窗口内永远攒不出第一条告警。
            cooling = last_sent > 0 and (now - last_sent) < max(0.0, float(cooldown_seconds))
            if not cooling:
                alert = True
                alerting = True
                alert_since = now
                last_sent = now
    else:
        count = 0
        if alerting:
            recovery = True
            alerting = False
            duration = max(0.0, now - alert_since) if alert_since else 0.0
            alert_since = 0.0

    st.update({
        'count': count,
        'alerting': alerting,
        'last_sent': last_sent,
        'alert_since': alert_since,
        'last_value': current,
    })
    return {
        'state': st,
        'alert': alert,
        'recovery': recovery,
        'duration': duration,
        'value': current,
    }


def evaluate_offline(
    state: dict[str, Any] | None,
    online: bool | None,
    *,
    now: float | None = None,
    failure_warn_at: int = 5,
) -> dict[str, Any]:
    """Bot 掉线状态机。

    **探测失败不等于掉线**。NapCat 重启期间 `get_status` 必然失败，把失败当掉线
    会在每次重启时误报一条。所以只有明确拿到 `online: false` 才记掉线；
    `None` 只是给探测失败计数，供诊断用。

    `online` 语义：
    - `True` 在线
    - `False` 明确离线
    - `None` 探测失败，状态未知
    """
    now = time.time() if now is None else float(now)
    st = dict(state or {})
    confirmed = bool(st.get('offline'))
    since = _as_float(st.get('offline_since'))
    failures = int(st.get('probe_failures') or 0)

    offline_now = False
    recovered = False
    duration = 0.0
    warn = False

    if online is None:
        failures += 1
        threshold = max(1, int(failure_warn_at))
        # 只在刚越过阈值时提示一次，之后每 30 次再提一次。每分钟刷一行
        # 「连续 N 次失败」除了把日志淹掉没有任何作用。
        warn = not confirmed and (failures == threshold or (failures > threshold and failures % 30 == 0))
    elif online is False:
        failures = 0
        if not confirmed:
            confirmed = True
            since = now
            offline_now = True
    else:
        failures = 0
        if confirmed:
            recovered = True
            duration = max(0.0, now - since) if since else 0.0
            confirmed = False
            since = 0.0

    st.update({
        'offline': confirmed,
        'offline_since': since,
        'probe_failures': failures,
        'last_check': now,
    })
    return {
        'state': st,
        'offline': offline_now,
        'recovered': recovered,
        'duration': duration,
        'probe_failures': failures,
        'warn': warn,
    }


class AlertAggregator:
    """告警聚合窗口。

    上游的行为是「第一条立即发，窗口内后续的合成一条汇总」。这条规则很实用：
    一次负载尖峰常常同时点燃 CPU、内存、负载三个规则，群里刷三条不如一条。

    纯内存对象，不持久化：进程重启后窗口自然失效，这是可以接受的。
    """

    def __init__(self, window_seconds: float = 120.0):
        self.window = max(0.0, float(window_seconds))
        # 用 None 而不是 0.0 表示「窗口没开」：0.0 是合法时间戳，
        # 拿它当哨兵会让窗口在 t=0 时假装没开过。
        self._opened_at: float | None = None
        self._pending: list[dict[str, Any]] = []

    def add(self, item: dict[str, Any], *, now: float | None = None) -> bool:
        """塞一条告警。返回 True 表示「现在就该发这一条」。"""
        now = time.time() if now is None else float(now)
        if self.window <= 0:
            return True
        if not self._pending and self._opened_at is None:
            self._opened_at = now
            return True
        self._pending.append(dict(item))
        return False

    def take(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """窗口到期就把缓存的告警一次性交出来。

        注意：窗口开着、但还没有后续告警时**不清窗口**。否则「第一条立即发」
        之后窗口立刻关闭，紧随其后的第二条又会被当成第一条立即发出去，
        聚合等于没做。
        """
        now = time.time() if now is None else float(now)
        if self._opened_at is None:
            return []
        if now - self._opened_at < self.window:
            return []
        items = self._pending
        self._pending = []
        self._opened_at = None
        return items

    def summary(self, items: Sequence[dict[str, Any]]) -> str:
        """把聚合到的告警压成一行，给群消息用。"""
        if not items:
            return ''
        names = '、'.join(str(item.get('name') or item.get('title') or '异常') for item in items)
        return f'另有 {len(items)} 项异常：{names}'


# ---------------------------------------------------------------------------
# 四、报表
# ---------------------------------------------------------------------------


def aggregate_resources(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """资源采样 → 日报资源段需要的统计量。

    平均值回答「这一天大概怎么样」，峰值回答「有没有炸过」。两个都要，
    因为只看平均会把一次 30 秒的满载完全抹平。
    """
    def series(key: str) -> list[float]:
        out = []
        for row in rows:
            try:
                out.append(float(row.get(key)))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        return out

    def stat(values: list[float]) -> tuple[float | None, float | None]:
        if not values:
            return None, None
        return sum(values) / len(values), max(values)

    cpu_avg, cpu_max = stat(series('cpu'))
    mem_avg, mem_max = stat(series('mem'))
    _, swap_max = stat(series('swap'))
    _, disk_max = stat(series('disk'))
    net_up_avg, net_up_max = stat(series('net_up'))
    net_down_avg, net_down_max = stat(series('net_down'))

    points = []
    for row in rows:
        try:
            points.append((float(row.get('ts')), float(row.get('cpu'))))
        except (TypeError, ValueError):
            continue

    return {
        'count': len(rows),
        'cpu_avg': cpu_avg,
        'cpu_max': cpu_max,
        'mem_avg': mem_avg,
        'mem_max': mem_max,
        'swap_max': swap_max,
        'disk_max': disk_max,
        'net_up_avg': net_up_avg,
        'net_up_max': net_up_max,
        'net_down_avg': net_down_avg,
        'net_down_max': net_down_max,
        'points': points,
    }


def aggregate_websites(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """网站延迟 → 每个目标的可用率、平均延迟、峰值。

    可用率按成功请求算，不按「有没有返回」算：4xx/5xx 是「服务在应答但接口
    坏了」，用户关心的是能不能用。
    """
    bucket: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get('name') or '站点')
        slot = bucket.setdefault(name, {'name': name, 'total': 0, 'ok': 0, 'delays': []})
        slot['total'] += 1
        if row.get('ok'):
            slot['ok'] += 1
            try:
                slot['delays'].append(float(row.get('delay_ms')))
            except (TypeError, ValueError):
                pass

    result = []
    for slot in bucket.values():
        ok = slot['ok']
        delays = slot['delays']
        result.append({
            'name': slot['name'],
            'total': slot['total'],
            'ok': ok,
            'uptime': round(ok / slot['total'] * 100, 1) if slot['total'] else None,
            'avg_ms': round(sum(delays) / len(delays), 0) if delays else None,
            'max_ms': max(delays) if delays else None,
        })
    result.sort(key=lambda item: (item['uptime'] if item['uptime'] is not None else 999))
    return result


def aggregate_processes(rows: Sequence[dict[str, Any]], top_n: int = 5) -> dict[str, list[dict[str, Any]]]:
    """进程采样 → 按名字聚合的峰值 Top N。

    同一个程序的多进程要合并：`chrome` 开八个进程时，逐个列出既占地方又看不出
    到底是谁在吃 CPU。
    """
    def collect(kind: str) -> list[dict[str, Any]]:
        peaks: dict[str, float] = {}
        for row in rows:
            if str(row.get('kind') or '') != kind:
                continue
            name = str(row.get('name') or '--')
            try:
                value = float(row.get('value'))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            peaks[name] = max(peaks.get(name, 0.0), value)
        ranked = sorted(peaks.items(), key=lambda item: -item[1])[: max(1, int(top_n))]
        return [{'name': name, 'value': value} for name, value in ranked]

    return {'cpu': collect('cpu'), 'mem': collect('mem')}


def summarize_events(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """异常事件 → 日报异常段。

    中断时长优先用写入时落库的 `resolved_ts`：插件在故障期间重启过也能算准。
    老数据没有该字段时，退化成把「告警」和「恢复」两条事件配对。
    """
    items: list[dict[str, Any]] = []
    downtime = 0.0
    pending: dict[str, dict[str, Any]] = {}

    for row in sorted(rows, key=lambda item: _as_float(item.get('ts'))):
        kind = str(row.get('kind') or '')
        title = str(row.get('title') or '异常')
        ts = _as_float(row.get('ts'))
        if kind == 'recovery':
            opened = pending.pop(title, None)
            if opened is not None:
                duration = max(0.0, ts - _as_float(opened.get('ts')))
                opened['duration'] = duration
                downtime += duration
            continue
        resolved = row.get('resolved_ts')
        try:
            resolved_ts = float(resolved)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            resolved_ts = 0.0
        record = {
            'ts': ts,
            'title': title,
            'detail': str(row.get('detail') or ''),
            'kind': kind,
            'unresolved': not bool(resolved_ts),
            'duration': max(0.0, resolved_ts - ts) if resolved_ts else 0.0,
        }
        if resolved_ts:
            downtime += record['duration']
        else:
            pending[title] = record
        items.append(record)

    return {
        'total': len(items),
        'unresolved': sum(1 for item in items if item['unresolved']),
        'downtime': downtime,
        'items': items[-12:],
    }


def qq_trend(snapshots: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """好友/群/群员快照 + 收发累计 → QQ 趋势段。

    收发用**次日快照差值**，不用绝对累计：累计值在换号或重置后会出现一个
    负跳变，直接画上去像是「昨天少了三千条」。
    """
    ordered = sorted(snapshots, key=lambda item: str(item.get('day') or ''))
    if not ordered:
        return {'friends': None, 'groups': None, 'members': None, 'recv': None, 'sent': None, 'points': []}

    latest = ordered[-1]
    recv_delta = None
    sent_delta = None
    if len(ordered) >= 2:
        previous = ordered[-2]
        try:
            recv_delta = int(latest.get('recv_total') or 0) - int(previous.get('recv_total') or 0)
            sent_delta = int(latest.get('sent_total') or 0) - int(previous.get('sent_total') or 0)
        except (TypeError, ValueError):
            recv_delta = sent_delta = None
        if recv_delta is not None and recv_delta < 0:
            recv_delta = None
        if sent_delta is not None and sent_delta < 0:
            sent_delta = None

    points = []
    for row in ordered:
        try:
            points.append((str(row.get('day')), int(row.get('recv_total') or 0)))
        except (TypeError, ValueError):
            continue

    return {
        'friends': latest.get('friends'),
        'groups': latest.get('groups'),
        'members': latest.get('members'),
        'recv': recv_delta,
        'sent': sent_delta,
        'points': points,
    }


def build_report(
    kind: str,
    *,
    day: str,
    days: int,
    host: str,
    sections: Iterable[str],
    resources: Sequence[dict[str, Any]] = (),
    websites: Sequence[dict[str, Any]] = (),
    processes: Sequence[dict[str, Any]] = (),
    events: Sequence[dict[str, Any]] = (),
    snapshots: Sequence[dict[str, Any]] = (),
    tokens: dict[str, Any] | None = None,
    llm: dict[str, Any] | None = None,
    footer: str = '',
) -> dict[str, Any]:
    """拼出日报/周报的通用 section 结构。

    渲染层只认 `sections`，不关心这是日报还是周报、也不关心数据从哪来。
    这样日报加一个模块只需要在这里多产出一个 section，出图那边不用动。
    """
    enabled = {str(item) for item in sections or ()}
    out_sections: list[dict[str, Any]] = []

    if 'resource' in enabled:
        stat = aggregate_resources(resources)
        rows = [
            ('CPU 均值 / 峰值', f'{format_pct(stat["cpu_avg"])} / {format_pct(stat["cpu_max"])}'),
            ('内存 均值 / 峰值', f'{format_pct(stat["mem_avg"])} / {format_pct(stat["mem_max"])}'),
            ('SWAP 峰值', format_pct(stat['swap_max'])),
            ('磁盘 峰值', format_pct(stat['disk_max'])),
        ]
        if stat['net_up_avg'] is not None:
            rows.append((
                '网络 上行均值 / 峰值',
                f'{format_speed(stat["net_up_avg"])} / {format_speed(stat["net_up_max"])}',
            ))
            rows.append((
                '网络 下行均值 / 峰值',
                f'{format_speed(stat["net_down_avg"])} / {format_speed(stat["net_down_max"])}',
            ))
        out_sections.append({
            'key': 'resource',
            'title': '资源趋势',
            'rows': rows,
            'chart': {'points': stat['points'], 'unit': '%', 'label': 'CPU'},
            'note': '' if stat['count'] else '暂无采样数据，开启采样后次日开始累积',
        })

    if 'event' in enabled:
        summary = summarize_events(events)
        rows = [
            ('异常条数', str(summary['total'])),
            ('未恢复', str(summary['unresolved'])),
            ('累计中断', format_duration(summary['downtime']) if summary['downtime'] else '无'),
        ]
        out_sections.append({
            'key': 'event',
            'title': '异常汇总',
            'rows': rows,
            'table': {
                'headers': ('时间', '事件', '状态'),
                'rows': [
                    (
                        format_short_time(item['ts']),
                        item['title'],
                        '未恢复' if item['unresolved'] else format_duration(item['duration']),
                    )
                    for item in summary['items'][-6:]
                ],
            },
            'note': '' if summary['total'] else '这一天没有异常',
        })

    if 'qq' in enabled:
        trend = qq_trend(snapshots)
        out_sections.append({
            'key': 'qq',
            'title': 'QQ 统计',
            'rows': [
                ('好友', str(trend['friends']) if trend['friends'] is not None else EMPTY),
                ('群聊', str(trend['groups']) if trend['groups'] is not None else EMPTY),
                ('群员', str(trend['members']) if trend['members'] is not None else EMPTY),
                ('昨日接收', str(trend['recv']) if trend['recv'] is not None else '数据积累中'),
                ('昨日发送', str(trend['sent']) if trend['sent'] is not None else '数据积累中'),
            ],
        })

    if 'token' in enabled:
        data = tokens or {}
        rows = [
            ('昨日消耗', f'{int(data.get("yesterday") or 0):,}'),
            ('近 7 天', f'{int(data.get("week") or 0):,}'),
            ('累计', f'{int(data.get("all") or 0):,}'),
        ]
        top = data.get('top_model')
        if top:
            rows.append(('最忙模型', str(top)))
        out_sections.append({
            'key': 'token',
            'title': 'Token 消耗',
            'rows': rows,
            'note': '' if data else '未接入 token 账本',
        })

    if 'llm' in enabled:
        data = llm or {}
        total = int(data.get('total') or 0)
        ok = int(data.get('ok') or 0)
        rows = [
            ('调用次数', str(total) if total else '暂无记录'),
            ('成功率', f'{ok / total * 100:.1f}%' if total else EMPTY),
            ('平均延迟', f'{data.get("avg_ms", 0):.0f}ms' if total else EMPTY),
            ('首字延迟', f'{data.get("avg_ttft_ms", 0):.0f}ms' if data.get('avg_ttft_ms') else EMPTY),
        ]
        out_sections.append({
            'key': 'llm',
            'title': 'LLM 服务',
            'rows': rows,
            'note': '' if total else '未记录到调用，包一层 provider 后开始统计',
        })

    if 'website' in enabled:
        sites = aggregate_websites(websites)
        out_sections.append({
            'key': 'website',
            'title': '网站延迟',
            'table': {
                'headers': ('站点', '可用率', '均值', '峰值'),
                'rows': [
                    (
                        site['name'],
                        f'{site["uptime"]:.1f}%' if site['uptime'] is not None else EMPTY,
                        f'{site["avg_ms"]:.0f}ms' if site['avg_ms'] is not None else EMPTY,
                        f'{site["max_ms"]:.0f}ms' if site['max_ms'] is not None else EMPTY,
                    )
                    for site in sites
                ],
            },
            'note': '' if sites else '未配置网站测试目标',
        })

    if 'process' in enabled:
        proc = aggregate_processes(processes)
        # CPU 榜和内存榜常常不是同一批进程（编译时 gcc 吃 CPU、数据库吃内存），
        # 所以按名字合并两个榜，而不是只按 CPU 排完留一列空着。
        cpu_peak = {row['name']: row['value'] for row in proc['cpu']}
        mem_peak = {row['name']: row['value'] for row in proc['mem']}
        order: list[str] = [row['name'] for row in proc['cpu']]
        order += [name for name in mem_peak if name not in order]
        merged = [
            (
                name,
                format_pct(cpu_peak.get(name)),
                format_pct(mem_peak.get(name)),
            )
            for name in order
        ][:5]
        out_sections.append({
            'key': 'process',
            'title': 'Top 进程',
            'table': {'headers': ('进程', 'CPU 峰值', '内存峰值'), 'rows': merged},
            'note': '' if merged else '暂无进程采样',
        })

    title = '服务器周报' if kind == 'weekly' else '服务器日报'
    subtitle = f'{day} · {days} 天窗口' if kind == 'weekly' else f'{day} · 昨日'
    return {
        'kind': kind,
        'title': title,
        'subtitle': subtitle,
        'host': host,
        'sections': out_sections,
        'footer': footer or '内容口径与状态卡一致 · 本地 Pillow 渲染',
    }


# ---------------------------------------------------------------------------
# 五、杂项
# ---------------------------------------------------------------------------


def parse_hhmm(text: str) -> tuple[int, int] | None:
    """`21:00` / `21：00` / `9:5` → (21, 0)。非法返回 None。"""
    raw = str(text or '').strip().replace('：', ':')
    if not raw:
        return None
    parts = raw.split(':')
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def seconds_until(now: datetime, hour: int, minute: int) -> float:
    """距离下一个 `hour:minute` 还有多少秒；今天已过就算明天。"""
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def daily_marker_due(last_run: str, today: str, *, first_day: str | None = None) -> bool:
    """今天该不该补发定时任务。

    补发是为了「机器人晚上重启，第二天早上发现没发」。但首次安装当天不该补：
    那时 `last_run` 是空的，补发会在装好插件的下一秒往群里推一张卡。
    """
    if not last_run:
        return False
    if last_run >= today:
        return False
    if first_day and first_day >= today:
        return False
    return True


def parse_target(raw: Any) -> dict[str, Any] | None:
    """把推送目标统一成 `{"kind": "group"|"private", "id": int}`。

    接受三种写法，因为上游用户手里三种都有：

    - `123456789` —— 裸群号（最常见）
    - `GroupMessage:123456789` —— AstrBot 的 UMO 写法，迁移过来的配置直接用
    - `group:123` / `private:123` —— 本项目自己的写法
    """
    text = str(raw or '').strip()
    if not text:
        return None
    kind = 'group'
    value = text
    if ':' in text:
        prefix, _, rest = text.partition(':')
        lowered = prefix.strip().lower()
        if lowered in {'privatemessage', 'private', 'friend'}:
            kind = 'private'
        elif lowered in {'groupmessage', 'group'}:
            kind = 'group'
        else:
            return None
        value = rest.strip()
    if not value.isdigit():
        return None
    return {'kind': kind, 'id': int(value)}


def parse_targets(raw: Any) -> list[dict[str, Any]]:
    """批量解析目标，去重且保持顺序。"""
    if isinstance(raw, (str, int)):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    seen: list[str] = []
    out: list[dict[str, Any]] = []
    for item in raw:
        parsed = parse_target(item)
        if not parsed:
            continue
        key = f'{parsed["kind"]}:{parsed["id"]}'
        if key in seen:
            continue
        seen.append(key)
        out.append(parsed)
    return out


def update_month_traffic(
    state: dict[str, Any] | None,
    sent_total: int | None,
    recv_total: int | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """按自然月累计流量，每月 1 日归零。

    两个细节：

    - **计数器回退当重启**：`psutil.net_io_counters` 在重启后清零，差值会变成
      负数。这时只更新基线，不把负数加进本月流量。
    - **本月流量跨重启累加**：所以重启后「本月」可能大于「本次开机累计」，
      这不是算错，是窗口不同。
    """
    now = now or datetime.now()
    month = now.strftime('%Y-%m')
    st = dict(state or {})
    if str(st.get('month') or '') != month:
        st = {'month': month, 'month_up': 0, 'month_down': 0, 'sent_base': sent_total, 'recv_base': recv_total}
        return st

    if sent_total is not None:
        base = st.get('sent_base')
        if isinstance(base, int) and sent_total >= base:
            st['month_up'] = int(st.get('month_up') or 0) + (sent_total - base)
        st['sent_base'] = int(sent_total)
    if recv_total is not None:
        base = st.get('recv_base')
        if isinstance(base, int) and recv_total >= base:
            st['month_down'] = int(st.get('month_down') or 0) + (recv_total - base)
        st['recv_base'] = int(recv_total)
    return st


def network_rates(
    previous: dict[str, float] | None,
    current: dict[str, float] | None,
    elapsed: float,
) -> tuple[float, float]:
    """两次网卡计数 → (上行速率, 下行速率)，字节/秒。

    时钟回拨（NTP 校时）会算出负速率，这时返回 0 而不是一个巨大的负数：
    卡片上出现 `-2.3 GB/s` 比显示 0 更让人怀疑整张卡。
    """
    if not previous or not current:
        return 0.0, 0.0
    span = max(0.0, float(elapsed))
    if span <= 0:
        return 0.0, 0.0
    up = float(current.get('sent', 0)) - float(previous.get('sent', 0))
    down = float(current.get('recv', 0)) - float(previous.get('recv', 0))
    if up < 0 or down < 0:
        return 0.0, 0.0
    return up / span, down / span


def month_label(month: str) -> str:
    """`2026-10` → `10月`。"""
    try:
        return f'{int(str(month).split("-")[1])}月'
    except (IndexError, ValueError):
        return str(month or '')
