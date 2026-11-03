# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""灾害预警配置：默认值、读写、校验，以及与面板共享的白名单。

机器人进程与面板进程只共享 `data/disaster_warning.json`。配置放 JSON 而不是
SQLite：字段少、人工可查、改错了好修；事件与推送记录以后另放 SQLite。

令牌只落文件，`public_view()` 永远不回显明文，只回 `token_configured`。面板
留空表示保持原值，勾选「清除令牌」才会清空——避免一次误保存把凭证抹掉。
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_PATH = Path(__file__).resolve().parent / 'data' / 'disaster_warning.json'

WEATHER_COLORS = ('白色', '蓝色', '黄色', '橙色', '红色')
TSUNAMI_LEVELS = ('信息', '蓝色', '黄色', '橙色', '红色')

DEFAULT_CONFIG: dict[str, Any] = {
    'enabled': True,
    'sources': {
        # Jian Project：中国气象预警 + 自然资源部海啸（登录密钥换长期 token）
        'jian': {'enabled': True, 'token': '', 'token_expires_at': ''},
        # EQSC：中国气象局实时活跃台风（HTTP 轮询，ARh. refresh token）
        'eqsc': {'enabled': True, 'token': '', 'token_expires_at': '', 'poll_seconds': 120},
        # 地震附加：Wolfx / P2P / PancakesAPI 免鉴权源，是否启用
        'earthquake_free': {'enabled': True},
    },
    'render': {
        'enabled': True,
        'remote_url': '',
        'timeout_seconds': 20,
        # 面板容器状态监视用；实际 compose 名可改
        'container_name': 'qqbot-disaster-playwright',
    },
    'local': {
        # 默认关闭：经纬度没填时不能拿 (0, 0) 去过滤
        'enabled': False,
        'province': '',
        'city': '',
        'latitude': 0.0,
        'longitude': 0.0,
    },
    'push': {
        'enabled': True,
        'group_id': '',
        'owner_id': '',
    },
    'filters': {
        'weather_min_color': '黄色',
        'typhoon_max_distance_km': 1200,
        'tsunami_min_level': '信息',
        'earthquake_min_magnitude': 4.5,
        'earthquake_max_distance_km': 1000,
    },
}


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in ('true', 'false'):
        return value == 'true'
    raise ValueError(f'{name} 必须是布尔值')


def _int(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f'{name} 必须是整数')
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} 必须是整数') from None
    if not low <= number <= high:
        raise ValueError(f'{name} 须在 {low} 到 {high} 之间')
    return number


def _float(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f'{name} 必须是数字')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} 必须是数字') from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f'{name} 须在 {low} 到 {high} 之间')
    return number


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f'{name} 必须是文本')
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f'{name} 不能超过 {limit} 个字符')
    return text


def _region_names(value: Any, name: str) -> str:
    """沿用字符串配置，多个地区规范成顿号分隔并去重。"""
    text = _text(value, name, 600)
    names = [_text(part, f'单个{name}', 40) for part in re.split(r'[,，、;；\r\n]+', text)]
    return '、'.join(dict.fromkeys(name for name in names if name))


def _qq(value: Any, name: str) -> str:
    text = _text(value, name, 12)
    if text and not re.fullmatch(r'\d{5,12}', text):
        raise ValueError(f'{name} 必须是 5 到 12 位 QQ 号或群号')
    return text


def _date(value: Any, name: str) -> str:
    text = _text(value, name, 10)
    if not text:
        return ''
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ValueError(f'{name} 须为 YYYY-MM-DD') from None
    return text


def _url(value: Any, name: str) -> str:
    text = _text(value, name, 300)
    if not text:
        return ''
    try:
        parsed = urlsplit(text)
    except ValueError:
        raise ValueError(f'{name} 不是合法地址') from None
    if parsed.scheme not in ('http', 'https', 'ws', 'wss') or not parsed.netloc:
        raise ValueError(f'{name} 须以 http(s):// 或 ws(s):// 开头')
    return text


def _container(value: Any, name: str) -> str:
    text = _text(value, name, 80)
    if text and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', text):
        raise ValueError(f'{name} 只能包含字母、数字、下划线、点和短横线')
    return text


def _choice(value: Any, name: str, options: tuple[str, ...]) -> str:
    text = _text(value, name, 20)
    if text not in options:
        raise ValueError(f'{name} 只能是：{"、".join(options)}')
    return text


def merge(defaults: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """递归补默认值；必须深拷贝，避免默认值被运行中的修改带脏。"""
    out = copy.deepcopy(defaults)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def normalize(config: Any) -> dict[str, Any]:
    """把读到的任意 JSON 收敛成「结构一定对」的配置。

    手工改坏的 `sources: "oops"` 不该让面板 500；缺字段补默认值，类型不对的
    小节整体回退默认值。真正的字段级校验仍在 apply_patch()。
    """
    out = merge(DEFAULT_CONFIG, config if isinstance(config, dict) else {})
    for key, default in DEFAULT_CONFIG.items():
        if not isinstance(default, dict):
            continue
        if not isinstance(out.get(key), dict):
            out[key] = copy.deepcopy(default)
            continue
        for subkey, subdefault in default.items():
            if isinstance(subdefault, dict) and not isinstance(out[key].get(subkey), dict):
                out[key][subkey] = copy.deepcopy(subdefault)
    return out


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return normalize({})
    return normalize(raw)


def save_config(config: dict[str, Any], path: Path | str | None = None) -> None:
    target = Path(path) if path is not None else DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.tmp')
    temp.write_text(
        json.dumps(normalize(config), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    try:
        os.chmod(temp, 0o600)
    except OSError:
        # Windows 上 chmod 不一定生效；不阻塞保存，部署侧仍按私有文件处理。
        pass
    os.replace(temp, target)


def apply_patch(config: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """把面板提交的改动套进配置；只接受白名单字段。

    未知字段直接忽略，前端版本比后端旧时多提交一个字段不该让整次保存失败。
    非法值抛 ValueError，调用方回 400，绝不半截写入。
    """
    out = normalize(config)
    if not isinstance(patch, dict):
        return out

    if 'enabled' in patch:
        out['enabled'] = _bool(patch['enabled'], '总开关')

    sources = patch.get('sources')
    if isinstance(sources, dict):
        jian = sources.get('jian')
        if isinstance(jian, dict):
            if 'enabled' in jian:
                out['sources']['jian']['enabled'] = _bool(jian['enabled'], 'Jian 开关')
            if 'token' in jian:
                token = _text(jian['token'], 'Jian 令牌', 4096)
                if token:
                    out['sources']['jian']['token'] = token
            if 'token_expires_at' in jian:
                out['sources']['jian']['token_expires_at'] = _date(
                    jian['token_expires_at'], 'Jian 令牌到期日'
                )
        eqsc = sources.get('eqsc')
        if isinstance(eqsc, dict):
            if 'enabled' in eqsc:
                out['sources']['eqsc']['enabled'] = _bool(eqsc['enabled'], 'EQSC 开关')
            if 'token' in eqsc:
                token = _text(eqsc['token'], 'EQSC 令牌', 4096)
                if token:
                    out['sources']['eqsc']['token'] = token
            if 'token_expires_at' in eqsc:
                out['sources']['eqsc']['token_expires_at'] = _date(
                    eqsc['token_expires_at'], 'EQSC 令牌到期日'
                )
            if 'poll_seconds' in eqsc:
                out['sources']['eqsc']['poll_seconds'] = _int(
                    eqsc['poll_seconds'], 'EQSC 轮询间隔', 30, 600
                )
        quake = sources.get('earthquake_free')
        if isinstance(quake, dict) and 'enabled' in quake:
            out['sources']['earthquake_free']['enabled'] = _bool(
                quake['enabled'], '地震附加开关'
            )

    if patch.get('clear_jian_token') is True:
        out['sources']['jian']['token'] = ''
    if patch.get('clear_eqsc_token') is True:
        out['sources']['eqsc']['token'] = ''

    render = patch.get('render')
    if isinstance(render, dict):
        if 'enabled' in render:
            out['render']['enabled'] = _bool(render['enabled'], '远程渲染开关')
        if 'remote_url' in render:
            out['render']['remote_url'] = _url(render['remote_url'], '远程 Playwright 地址')
        if 'timeout_seconds' in render:
            out['render']['timeout_seconds'] = _int(
                render['timeout_seconds'], '渲染超时', 1, 120
            )
        if 'container_name' in render:
            out['render']['container_name'] = _container(
                render['container_name'], '渲染容器名'
            )

    local = patch.get('local')
    if isinstance(local, dict):
        if 'enabled' in local:
            out['local']['enabled'] = _bool(local['enabled'], '本地过滤开关')
        if 'province' in local:
            out['local']['province'] = _region_names(local['province'], '省份')
        if 'city' in local:
            out['local']['city'] = _region_names(local['city'], '城市/区县')
        if 'latitude' in local:
            out['local']['latitude'] = _float(local['latitude'], '纬度', -90.0, 90.0)
        if 'longitude' in local:
            out['local']['longitude'] = _float(local['longitude'], '经度', -180.0, 180.0)

    push = patch.get('push')
    if isinstance(push, dict):
        if 'enabled' in push:
            out['push']['enabled'] = _bool(push['enabled'], '推送开关')
        if 'group_id' in push:
            out['push']['group_id'] = _qq(push['group_id'], '运维群号')
        if 'owner_id' in push:
            out['push']['owner_id'] = _qq(push['owner_id'], '主人 QQ')

    filters = patch.get('filters')
    if isinstance(filters, dict):
        if 'weather_min_color' in filters:
            out['filters']['weather_min_color'] = _choice(
                filters['weather_min_color'], '气象最低颜色', WEATHER_COLORS
            )
        if 'typhoon_max_distance_km' in filters:
            out['filters']['typhoon_max_distance_km'] = _int(
                filters['typhoon_max_distance_km'], '台风最大距离', 0, 5000
            )
        if 'tsunami_min_level' in filters:
            out['filters']['tsunami_min_level'] = _choice(
                filters['tsunami_min_level'], '海啸最低等级', TSUNAMI_LEVELS
            )
        if 'earthquake_min_magnitude' in filters:
            out['filters']['earthquake_min_magnitude'] = _float(
                filters['earthquake_min_magnitude'], '地震最小震级', 0.0, 10.0
            )
        if 'earthquake_max_distance_km' in filters:
            out['filters']['earthquake_max_distance_km'] = _int(
                filters['earthquake_max_distance_km'], '地震最大距离', 0, 5000
            )

    return out


def public_view(config: dict[str, Any]) -> dict[str, Any]:
    """面板要显示的配置子集；令牌只回「是否已配置」。"""
    config = normalize(config)
    sources = config['sources']
    jian = sources['jian']
    eqsc = sources['eqsc']
    render = config['render']
    local = config['local']
    push = config['push']
    filters = config['filters']
    return {
        'enabled': bool(config['enabled']),
        'sources': {
            'jian': {
                'enabled': bool(jian['enabled']),
                'token_configured': bool(str(jian.get('token') or '').strip()),
                'token_expires_at': str(jian.get('token_expires_at') or ''),
            },
            'eqsc': {
                'enabled': bool(eqsc['enabled']),
                'token_configured': bool(str(eqsc.get('token') or '').strip()),
                'token_expires_at': str(eqsc.get('token_expires_at') or ''),
                'poll_seconds': eqsc['poll_seconds'],
            },
            'earthquake_free': {'enabled': bool(sources['earthquake_free']['enabled'])},
        },
        'render': {
            'enabled': bool(render['enabled']),
            'remote_url': str(render.get('remote_url') or ''),
            'timeout_seconds': render['timeout_seconds'],
            'container_name': str(render.get('container_name') or ''),
        },
        'local': {
            'enabled': bool(local['enabled']),
            'province': str(local.get('province') or ''),
            'city': str(local.get('city') or ''),
            'latitude': local['latitude'],
            'longitude': local['longitude'],
        },
        'push': {
            'enabled': bool(push['enabled']),
            'group_id': str(push.get('group_id') or ''),
            'owner_id': str(push.get('owner_id') or ''),
        },
        'filters': {
            'weather_min_color': filters['weather_min_color'],
            'typhoon_max_distance_km': filters['typhoon_max_distance_km'],
            'tsunami_min_level': filters['tsunami_min_level'],
            'earthquake_min_magnitude': filters['earthquake_min_magnitude'],
            'earthquake_max_distance_km': filters['earthquake_max_distance_km'],
        },
        'options': {
            'weather_colors': list(WEATHER_COLORS),
            'tsunami_levels': list(TSUNAMI_LEVELS),
        },
    }
