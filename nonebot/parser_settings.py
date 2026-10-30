"""Validated dashboard access to the parser configuration."""

import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
from media_parser.core.config import PluginConfig, config_lock

ROOT = Path(__file__).resolve().parent
PATH = ROOT / 'data' / 'media_parser' / 'config.json'
SCHEMA_PATH = ROOT / 'media_parser' / '_conf_schema.json'
SECRET_FIELDS = {'cookies', 'snowluma_access_token', 'proxy'}
READ_ONLY = {'arbiter', 'require_at_in_group'}


def schema():
    data = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    data['whitelist']['hint'] = '每行填写 group:群号 或 private:QQ号；留空表示不限制。'
    data['blacklist']['hint'] = '每行填写 group:群号 或 private:QQ号；黑名单会话不解析链接。'
    data['proxy']['hint'] = '输入新的代理地址以替换现有地址；留空保持现有配置，勾选清除可恢复直连。'
    data['media_tools_paths'] = {'type': 'list', 'description': 'FFmpeg 工具目录', 'hint': '每行一个目录，按顺序查找 ffmpeg 和 ffprobe'}
    data['snowluma_container_cache'] = {'type': 'bool', 'description': 'SnowLuma 容器共享缓存', 'hint': 'SnowLuma 容器将本项目 snowluma 目录挂载为 /app/data 时启用，供容器读取解析出的媒体文件。'}
    return data


def load():
    return copy.deepcopy(dict(PluginConfig(PATH).raw_data()))


def version(data):
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def public():
    data = load()
    revision = version(data)
    definitions = schema()
    platforms = {item['__template_key']: item for item in data.pop('parsers_template')}
    secrets = {'proxy': bool(data.get('proxy'))}
    data['proxy'] = ''
    for name, entry in platforms.items():
        for key in SECRET_FIELDS - {'proxy'}:
            if key in entry:
                secrets[f'{name}.{key}'] = bool(entry[key])
                entry[key] = ''
    baseline = defaults()
    return {
        'version': revision,
        'general': data,
        'platforms': platforms,
        'baseline': baseline,
        'summary': summary(platforms, baseline),
        'schema': definitions,
        'secrets': secrets,
    }


def defaults():
    """平台模板里每个字段的默认值，用来判断「有没有改动过」。

    `parsers_template` 是列表，每项带 `__template_key`；只取模板定义，不读用户配置。
    """
    result = {}
    for name, spec in schema()['parsers_template']['templates'].items():
        result[name] = {key: item.get('default') for key, item in spec.get('items', {}).items()}
    return result


def summary(platforms, baseline=None):
    """给前端用的平台开关总览：开了几个、一共几个、哪些开着。

    只看 `enable` 一个字段，不碰其它设置，所以调用方可以只读不写。
    """
    baseline = baseline if baseline is not None else defaults()
    total = len(platforms)
    enabled = [name for name, entry in platforms.items() if entry.get('enable') is True]
    return {'total': total, 'enabled': len(enabled), 'names': sorted(enabled)}


def validate(value, spec, label):
    kind = spec.get('type')
    if kind == 'bool':
        if type(value) is not bool: raise ValueError(f'{label}必须是开关')
    elif kind == 'int':
        if type(value) is not int: raise ValueError(f'{label}必须是整数')
        slider = spec.get('slider', {})
        if value < slider.get('min', 0) or value > slider.get('max', 100000):
            raise ValueError(f'{label}超出允许范围')
    elif kind == 'list':
        if not isinstance(value, list) or len(value) > 500 or any(not isinstance(v, str) or len(v) > 4096 for v in value):
            raise ValueError(f'{label}列表格式无效')
        if spec.get('options') and any(v not in spec['options'] for v in value):
            raise ValueError(f'{label}包含不支持的选项')
    elif kind in ('string', 'text'):
        if not isinstance(value, str) or len(value) > 32768: raise ValueError(f'{label}内容无效')
        if spec.get('options') and value not in spec['options']: raise ValueError(f'{label}选项无效')
    else:
        raise ValueError(f'{label}类型不受支持')
    return value


def save(payload):
    with config_lock(PATH):
        return _save_locked(payload)


def _save_locked(payload):
    if not isinstance(payload, dict): raise ValueError('配置格式无效')
    data = load()
    if payload.get('version') != version(data): raise FileExistsError('配置已被其他操作修改，请重新加载后再保存')
    definitions = schema()
    general = payload.get('general', {})
    platforms = payload.get('platforms', {})
    cleared = payload.get('clear_secrets', [])
    if not isinstance(general, dict) or not isinstance(platforms, dict) or not isinstance(cleared, list): raise ValueError('配置格式无效')
    entries = {v['__template_key']: v for v in data['parsers_template']}
    template_defs = definitions['parsers_template']['templates']
    valid_secrets = {'proxy'} | {f'{name}.{key}' for name, spec in template_defs.items() for key in spec['items'] if key in SECRET_FIELDS}
    if any(not isinstance(k, str) or k not in valid_secrets for k in cleared): raise ValueError('凭证字段无效')
    for key, value in general.items():
        if key not in definitions or key in READ_ONLY or key == 'parsers_template': raise ValueError('未知或不可修改的设置')
        if key in SECRET_FIELDS and not value: continue
        data[key] = validate(value, definitions[key], definitions[key].get('description', key))
        if key in {'whitelist', 'blacklist'} and any(not re.fullmatch(r'(?:group|private):[1-9][0-9]*', v) for v in value):
            raise ValueError('会话格式须为 group:群号 或 private:QQ号')
    for name, changes in platforms.items():
        if name not in entries or name not in template_defs or not isinstance(changes, dict): raise ValueError('解析平台无效')
        specs = template_defs[name]['items']
        for key, value in changes.items():
            if key not in specs: raise ValueError('未知的平台设置')
            if key in SECRET_FIELDS and not value: continue
            entries[name][key] = validate(value, specs[key], specs[key].get('description', key))
    for key in cleared:
        if key == 'proxy': data[key] = ''
        else:
            name, field = key.split('.', 1)
            if name in entries: entries[name][field] = ''
    try:
        if data['clean_cron'].strip(): CronTrigger.from_crontab(data['clean_cron'])
    except (ValueError, TypeError): raise ValueError('缓存清理时间须为有效的五段 Cron 表达式')
    PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=PATH.parent, prefix='.parser-', suffix='.tmp', delete=False) as f:
            temp = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp, PATH)
    finally:
        if temp and os.path.exists(temp): os.unlink(temp)
    return public()
