"""Settings shared by the dashboard and owner-aware plugin."""
import json
import os
import re
from pathlib import Path

PATH = Path(__file__).resolve().parent / 'data' / 'master_aware.json'
DEFAULT = {'enabled': True, 'owner_ids': None, 'title': None}

def load():
    if not PATH.exists():
        return dict(DEFAULT)
    value = json.loads(PATH.read_text(encoding='utf-8'))
    return validate(value)

def validate(value):
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ValueError('开关配置无效')
    ids = value.get('owner_ids')
    if ids is not None:
        if not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(x,str) or not re.fullmatch(r'[1-9][0-9]{4,19}',x) for x in ids):
            raise ValueError('主人 QQ 号格式无效')
        ids = list(dict.fromkeys(ids))
    title = value.get('title')
    if title is not None:
        if not isinstance(title,str) or not title.strip() or len(title.strip()) > 40 or any(not c.isprintable() for c in title):
            raise ValueError('称呼须为 1 至 40 个字符')
        title = title.strip()
    return {'enabled':value['enabled'],'owner_ids':ids,'title':title}

def save(value):
    data = validate(value)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + '.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as out:
        json.dump(data,out,ensure_ascii=False)
    os.replace(tmp,PATH)
    return data
