# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。
"""Epic 喜加一的配置与跨进程「立即推送」触发。

机器人进程与面板进程共用这一份 `data/epic_free.json`；面板只写文件，机器人每轮
重读，改完不用重启。触发文件也是同一个思路：面板写、机器人轮询到后消费。

本模块不 import nonebot / httpx，面板进程可以直接用。
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parent / 'data' / 'epic_free.json'
TRIGGER_PATH = Path(__file__).resolve().parent / 'data' / 'epic_free_trigger.json'
MAX_TARGETS = 50
_COUNTRY = re.compile(r'[A-Z]{2}')
_LOCALE = re.compile(r'[a-z]{2}(?:-[A-Z]{2})?')
_QQ = re.compile(r'[1-9][0-9]{4,19}')

DEFAULT = {
    'version': 1,
    'country': 'CN',
    'locale': 'zh-CN',
    'targets': [],
    'push_enabled': True,
    'poll_minutes': 30,
    'include_upcoming': True,
    'card_enabled': True,
}


def default():
    return copy.deepcopy(DEFAULT)


def _bool(value, fallback):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ('true', '1', 'yes', 'on'):
            return True
        if text in ('false', '0', 'no', 'off'):
            return False
    return fallback


def _int(value, fallback, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(high, max(low, number))


def _targets(value, strict=False):
    if value is None:
        return []
    if not isinstance(value, list):
        if strict:
            raise ValueError('推送目标应为列表')
        return []
    cleaned, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            if strict:
                raise ValueError('推送目标格式无效')
            continue
        kind = str(item.get('kind', '')).strip().lower()
        ident = str(item.get('id', '')).strip()
        if kind not in ('group', 'private') or not _QQ.fullmatch(ident):
            if strict:
                raise ValueError('推送目标需为 group/private + 5-20 位 QQ/群号')
            continue
        key = (kind, ident)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({'kind': kind, 'id': ident})
    if len(cleaned) > MAX_TARGETS:
        if strict:
            raise ValueError(f'最多 {MAX_TARGETS} 个推送目标')
        cleaned = cleaned[:MAX_TARGETS]
    return cleaned


def _clean(raw, strict=False):
    if not isinstance(raw, dict):
        if strict:
            raise ValueError('配置格式无效')
        raw = {}
    out = default()
    country = str(raw.get('country', 'CN')).strip().upper()
    if _COUNTRY.fullmatch(country):
        out['country'] = country
    elif strict:
        raise ValueError('地区代码应为两位字母')
    locale = str(raw.get('locale', 'zh-CN')).strip()
    if _LOCALE.fullmatch(locale):
        out['locale'] = locale
    elif strict:
        raise ValueError('语言代码格式无效')
    out['targets'] = _targets(raw.get('targets'), strict=strict)
    out['push_enabled'] = _bool(raw.get('push_enabled'), True)
    out['poll_minutes'] = _int(raw.get('poll_minutes'), 30, 5, 180)
    out['include_upcoming'] = _bool(raw.get('include_upcoming'), True)
    out['card_enabled'] = _bool(raw.get('card_enabled'), True)
    return out


def read(path=None):
    target = Path(path) if path is not None else PATH
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default()
    return _clean(raw)


def write(config, path=None):
    target = Path(path) if path is not None else PATH
    clean = _clean(config, strict=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp')
    temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, target)
    return clean


def update(patch, path=None):
    current = read(path)
    for key in ('country', 'locale', 'poll_minutes', 'push_enabled',
                'include_upcoming', 'card_enabled'):
        if isinstance(patch, dict) and key in patch:
            current[key] = patch[key]
    return write(current, path)


def add_target(kind, ident, path=None):
    current = read(path)
    target = _targets([{'kind': kind, 'id': ident}], strict=True)[0]
    if target not in current['targets']:
        if len(current['targets']) >= MAX_TARGETS:
            raise ValueError(f'最多 {MAX_TARGETS} 个推送目标')
        current['targets'].append(target)
    return write(current, path)


def remove_target(kind, ident, path=None):
    current = read(path)
    kind = str(kind).strip().lower()
    ident = str(ident).strip()
    current['targets'] = [
        item for item in current['targets']
        if not (item['kind'] == kind and item['id'] == ident)
    ]
    return write(current, path)


def request_test(path=None):
    """面板点「立即推送」时写一个触发文件，机器人进程消费。"""
    target = Path(path) if path is not None else TRIGGER_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp')
    payload = {
        'nonce': secrets.token_hex(8),
        'at': datetime.now(timezone.utc).isoformat(),
    }
    temporary.write_text(json.dumps(payload), encoding='utf-8')
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, target)
    return payload


def has_test_request(path=None):
    return (Path(path) if path is not None else TRIGGER_PATH).exists()


def take_test_request(path=None):
    target = Path(path) if path is not None else TRIGGER_PATH
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raw = None
    try:
        target.unlink()
    except OSError:
        pass
    return raw if isinstance(raw, dict) else None
