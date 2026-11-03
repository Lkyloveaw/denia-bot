"""超级管理员名单：面板可维护，文件为准，`.env` 只作初始值。

NoneBot 的 `SUPERUSERS` 是启动时从 `.env` 读进内存的，改一次要重启；而「谁能用
管理指令」这种事本来就该在面板里点两下改完。这里把名单落到
`data/superusers.json`，机器人启动时加载、运行中按 mtime 热应用。

三种状态，别混：

- **文件不存在** —— 用 `.env` 的 `SUPERUSERS`，面板显示「初始值」
- **文件存在** —— 只认文件，`.env` 不再参与（否则面板里删不掉来自 `.env` 的人）
- **文件损坏** —— 当作不存在，回退 `.env`，并由机器人日志报警；宁可暂时回退，
  也不要因为一个逗号让所有管理指令失效

本模块不 import nonebot：机器人进程与面板进程共用它，面板进程没有 driver。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

PATH = Path(__file__).resolve().parent / 'data' / 'superusers.json'
ENV_PATH = Path(__file__).resolve().parent / '.env'

MAX_USERS = 20
"""上限。管理名单不是通讯录，超过这个数通常是填错了。"""

_QQ = re.compile(r'[1-9][0-9]{4,19}')
_SPLIT = re.compile(r'[,，;；\s]+')


def normalize(value: Any) -> list[str]:
    """校验并去重，返回干净的字符串列表。

    接受列表，也接受 `"123, 456"` 这种手打字符串——面板输入框和手改 JSON 都会走
    这里，两种写法都该能用。空值表示「一个都不要」，是合法状态。
    """
    if value is None:
        items: list[Any] = []
    elif isinstance(value, str):
        items = [part for part in _SPLIT.split(value) if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        raise ValueError('名单格式无效，应为 QQ 号列表')

    cleaned: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if not _QQ.fullmatch(text):
            raise ValueError(f'不是合法的 QQ 号：{text}')
        if text not in cleaned:
            cleaned.append(text)
    if len(cleaned) > MAX_USERS:
        raise ValueError(f'最多 {MAX_USERS} 个超级管理员')
    return cleaned


def load(path: Path | str | None = None) -> list[str] | None:
    """读名单。文件不存在返回 None；存在但读不懂也返回 None（调用方回退）。

    「读不懂」和「空名单」必须分开：前者要回退 `.env`，后者是用户真的清空了。
    """
    target = Path(path) if path is not None else PATH
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None
    if isinstance(raw, dict):
        raw = raw.get('users')
    if not isinstance(raw, list):
        return None
    try:
        return normalize(raw)
    except ValueError:
        return None


def save(users: Iterable[Any], path: Path | str | None = None) -> list[str]:
    """原子写入，权限 600（名单算身份信息）。返回落盘后的名单。"""
    target = Path(path) if path is not None else PATH
    data = normalize(list(users) if not isinstance(users, str) else users)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.tmp')
    payload = json.dumps({'users': data}, ensure_ascii=False, indent=2)
    fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(payload)
    os.replace(temp, target)
    return data


def reset(path: Path | str | None = None) -> bool:
    """删掉名单文件，回退到 `.env` 的初始值。返回是否真的删了。

    这是逃生口：面板里改乱了、或者想把控制权交回 `.env`，按一下就行。
    """
    target = Path(path) if path is not None else PATH
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    return True


def resolve(env_users: Iterable[Any] | None = None, path: Path | str | None = None) -> tuple[list[str], str]:
    """算出当前该用哪份名单，返回 `(名单, 来源)`，来源是 `file` 或 `env`。"""
    value = load(path)
    if value is not None:
        return value, 'file'
    try:
        fallback = normalize(list(env_users or ()))
    except ValueError:
        fallback = []
    return fallback, 'env'


def read_env_superusers(path: Path | str | None = None) -> list[str]:
    """从 `.env` 里抠出 `SUPERUSERS`，只给面板显示用。

    不引入 dotenv：只认 `SUPERUSERS=...` 这一行，值是 JSON 数组（NoneBot 的写法）。
    解析不了就当没配置，绝不让它影响面板的其它功能。
    """
    target = Path(path) if path is not None else ENV_PATH
    try:
        text = target.read_text(encoding='utf-8')
    except OSError:
        return []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        if not stripped.startswith('SUPERUSERS'):
            continue
        _, _, raw = stripped.partition('=')
        raw = raw.strip()
        if not raw:
            return []
        try:
            return normalize(json.loads(raw))
        except (ValueError, TypeError):
            # 不是 JSON 就按逗号分隔兜底，例如 `SUPERUSERS=123,456`
            return normalize(raw.strip('[]"\' '))
    return []
