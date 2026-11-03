# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""状态卡配置：默认值、读写、以及与面板共享的白名单。

为什么单独一个文件：机器人进程和面板进程都要读同一份 `data/statuscard.json`。
面板如果从 `plugins.statuscard` 里借函数，会连带把 nonebot 的 matcher 和钩子
也 import 进来——而面板进程根本没有初始化 nonebot。

配置项刻意保持少。上游有上百个字段，其中大半是多平台、多机器人、远程 TII
的预留；这台机器只有一个号、只走本地渲染，留下能用的就够。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    'display': {
        'width': 1170,
        'compact': False,
        'process_top_n': 8,
        'disk_paths': [],
        'redact': ['hostname', 'process'],
        'website_targets': [{'name': '百度', 'url': 'https://www.baidu.com', 'timeout_seconds': 3}],
        'website_cache_seconds': 60,
        'cooldown_seconds': 10,
        'sample_seconds': 60,
        'avatar_cache_seconds': 86400,
    },
    'alerts': {
        'enabled': False,
        'targets': [],
        'cpu': 85.0,
        'memory': 90.0,
        'swap': 90.0,
        'disk': 85.0,
        'load_per_core': 2.0,
        'interval_seconds': 60,
        'sustain_samples': 3,
        'cooldown_seconds': 1800,
        'notify_recovery': True,
        'offline_notify': True,
        'aggregate_seconds': 120,
        'daily_status': {'enabled': False, 'time': '09:00', 'with_image': True},
        'provider_watch': {'enabled': False, 'threshold': 3, 'cooldown_seconds': 1800},
    },
    'monitors': {
        'enabled': False,
        'targets': [],
        'items': [],
        'interval_seconds': 60,
        'fail_threshold': 2,
        'cooldown_seconds': 1800,
        'silence_enabled': False,
        'silence_until': '',
        'notify_recovery': True,
        'max_response_bytes': 262144,
    },
    'report': {
        'enabled': False,
        'time': '21:00',
        'weekly_enabled': False,
        'weekly_time': '21:30',
        'sections': ['resource', 'event', 'qq', 'token'],
        'weekly_sections': [],
        'with_image': True,
        'history_days': 7,
        'collect': True,
        'resource_sample_seconds': 300,
        'process_top_n': 5,
        'retention_days': 30,
        'event_retention_days': 90,
    },
}

#: 面板允许修改的字段。白名单而不是黑名单：面板是给人用的，手滑改错一个没人
#: 看过的字段，比「面板少一个开关」难查得多。
EDITABLE: dict[str, tuple[str, ...]] = {
    'display': ('width', 'compact', 'process_top_n', 'redact', 'cooldown_seconds'),
    'alerts': ('enabled', 'targets', 'cpu', 'memory', 'swap', 'disk', 'load_per_core',
               'notify_recovery', 'offline_notify'),
    'monitors': ('enabled', 'targets', 'fail_threshold', 'notify_recovery',
                 'silence_enabled', 'silence_until'),
    'report': ('enabled', 'time', 'weekly_enabled', 'weekly_time', 'sections',
               'with_image', 'history_days', 'collect'),
}

REDACT_OPTIONS = ('hostname', 'process', 'network', 'qq_stats')
REPORT_SECTIONS = ('resource', 'event', 'qq', 'token', 'llm', 'website', 'process')


def merge(defaults: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """递归补默认值。旧配置文件缺字段时不该整份失效。

    必须深拷贝：浅拷贝会让返回的嵌套 dict 与 `DEFAULT_CONFIG` 共享同一个对象，
    运行中改一个字段（面板保存、测试里改开关）就把全局默认值也改了，
    后面对同一进程里新建的所有实例都生效——排查起来极其费劲。
    """
    out = copy.deepcopy(defaults)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else Path('data/statuscard.json')
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return merge(DEFAULT_CONFIG, {})
    except (OSError, ValueError):
        return merge(DEFAULT_CONFIG, {})
    return merge(DEFAULT_CONFIG, raw if isinstance(raw, dict) else {})


def save_config(config: dict[str, Any], path: Path | str | None = None) -> None:
    target = Path(path) if path is not None else Path('data/statuscard.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.tmp')
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(target)


def public_view(config: dict[str, Any]) -> dict[str, Any]:
    """面板要显示的配置子集 + 可选项，避免前端硬编码字段名。"""
    display = config.get('display') or {}
    alerts = config.get('alerts') or {}
    monitors = config.get('monitors') or {}
    report = config.get('report') or {}
    return {
        'display': {
            'width': display.get('width'),
            'compact': display.get('compact'),
            'process_top_n': display.get('process_top_n'),
            'redact': list(display.get('redact') or []),
            'cooldown_seconds': display.get('cooldown_seconds'),
        },
        'alerts': {
            'enabled': alerts.get('enabled'),
            'targets': list(alerts.get('targets') or []),
            'cpu': alerts.get('cpu'),
            'memory': alerts.get('memory'),
            'swap': alerts.get('swap'),
            'disk': alerts.get('disk'),
            'load_per_core': alerts.get('load_per_core'),
            'notify_recovery': alerts.get('notify_recovery'),
            'offline_notify': alerts.get('offline_notify'),
            'daily_status': dict(alerts.get('daily_status') or {}),
        },
        'monitors': {
            'enabled': monitors.get('enabled'),
            'targets': list(monitors.get('targets') or []),
            'fail_threshold': monitors.get('fail_threshold'),
            'notify_recovery': monitors.get('notify_recovery'),
            'silence_enabled': monitors.get('silence_enabled'),
            'silence_until': monitors.get('silence_until'),
            'items': list(monitors.get('items') or []),
        },
        'report': {
            'enabled': report.get('enabled'),
            'time': report.get('time'),
            'weekly_enabled': report.get('weekly_enabled'),
            'weekly_time': report.get('weekly_time'),
            'sections': list(report.get('sections') or []),
            'with_image': report.get('with_image'),
            'history_days': report.get('history_days'),
            'collect': report.get('collect'),
        },
        'options': {
            'redact': list(REDACT_OPTIONS),
            'sections': list(REPORT_SECTIONS),
        },
    }


def apply_patch(config: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """把面板提交的改动套进配置，只接受白名单字段。

    越权字段直接忽略而不是报错：前端版本比后端旧时，多提交一个字段不该让
    整次保存失败。
    """
    out = dict(config)
    for section, fields in EDITABLE.items():
        incoming = (patch or {}).get(section)
        if not isinstance(incoming, dict):
            continue
        target = dict(out.get(section) or {})
        for field in fields:
            if field not in incoming:
                continue
            target[field] = incoming[field]
        out[section] = target
    return out
