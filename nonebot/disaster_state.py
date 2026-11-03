# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""地震源运行状态：去重集合、首轮静默标记、轮询时间与错误。

只落一个 JSON（`data/disaster_warning_state.json`），不存消息原文；面板配置
另在 `disaster_warning.json`。事件量不大，没必要为第一版上 SQLite。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent / 'data' / 'disaster_warning_state.json'
KEEP_KEYS = 1000
MAX_AGE_SECONDS = 14 * 86400


def new_state() -> dict[str, Any]:
    return {
        'seen': {},
        'initialized': {},
        'baseline_at': {},
        'last_poll': {},
        'errors': {},
        'counts': {'pushed': 0},
    }


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize(raw: Any) -> dict[str, Any]:
    state = new_state()
    if not isinstance(raw, dict):
        return state
    seen = raw.get('seen')
    if isinstance(seen, dict):
        state['seen'] = {
            str(key): _number(value)
            for key, value in seen.items()
            if isinstance(key, str) and _number(value) > 0
        }
    initialized = raw.get('initialized')
    if isinstance(initialized, dict):
        state['initialized'] = {
            str(key): bool(value) for key, value in initialized.items()
        }
    baseline_at = raw.get('baseline_at')
    if isinstance(baseline_at, dict):
        state['baseline_at'] = {
            str(key): str(value) for key, value in baseline_at.items() if value
        }
    last_poll = raw.get('last_poll')
    if isinstance(last_poll, dict):
        state['last_poll'] = {
            str(key): _number(value) for key, value in last_poll.items()
        }
    errors = raw.get('errors')
    if isinstance(errors, dict):
        state['errors'] = {
            str(key): str(value) for key, value in errors.items() if value
        }
    counts = raw.get('counts')
    if isinstance(counts, dict):
        state['counts']['pushed'] = int(_number(counts.get('pushed')))
    return state


def load(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return new_state()
    return normalize(raw)


def save(state: dict[str, Any], path: Path | str | None = None) -> None:
    target = Path(path) if path is not None else DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.tmp')
    temp.write_text(
        json.dumps(normalize(state), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, target)


def mark_seen(state: dict[str, Any], key: str, when: float | None = None) -> None:
    state['seen'][str(key)] = float(when if when is not None else time.time())


def record_poll(state: dict[str, Any], source: str, when: float | None = None,
                error: str = '') -> None:
    now = float(when if when is not None else time.time())
    state['last_poll'][source] = now
    if error:
        state['errors'][source] = str(error)
    else:
        state['errors'].pop(source, None)


def increment_pushed(state: dict[str, Any], count: int = 1) -> None:
    state['counts']['pushed'] = int(state['counts'].get('pushed', 0)) + int(count)


def prune(state: dict[str, Any], keep: int = KEEP_KEYS,
          now: float | None = None, max_age: float = MAX_AGE_SECONDS) -> None:
    """保留最近的 keep 条，并丢掉超过 max_age 的键。"""
    current = float(now if now is not None else time.time())
    cutoff = current - max_age
    seen = state.get('seen') or {}
    alive = {
        key: value for key, value in seen.items()
        if isinstance(value, (int, float)) and value >= cutoff
    }
    if len(alive) > keep:
        newest = sorted(alive.items(), key=lambda pair: pair[1], reverse=True)[:keep]
        alive = dict(newest)
    state['seen'] = alive
