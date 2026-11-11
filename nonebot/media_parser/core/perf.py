"""Privacy-preserving stage timings for one parser request."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4
import re

from nonebot import logger

_current = ContextVar('parser_perf', default=None)


def begin(platform):
    safe_platform = re.sub(r'[^a-zA-Z0-9_-]', '', str(platform))[:32] or 'unknown'
    return _current.set({'trace': uuid4().hex[:12], 'platform': safe_platform, 'outcome': 'ok', 'marks': []})


def end(token):
    _current.reset(token)


def mark(status):
    current = _current.get()
    if current is not None:
        current['marks'].append(status)
        if status == 'error' or current['outcome'] == 'ok':
            current['outcome'] = status


def outcome():
    current = _current.get()
    return current['outcome'] if current is not None else 'ok'


def record(stage, started, status='ok'):
    current = _current.get()
    if current is not None:
        try:
            logger.info(f"[parser-perf] stage={stage} status={status} ms={(perf_counter() - started) * 1000:.1f} trace={current['trace']} platform={current['platform']}")
        except Exception:
            pass


@contextmanager
def phase(stage):
    started = perf_counter()
    current = _current.get()
    mark_start = len(current['marks']) if current is not None else 0
    measured = SimpleNamespace(status='ok')
    try:
        yield measured
    except asyncio.CancelledError:
        record(stage, started, 'cancelled')
        raise
    except BaseException:
        record(stage, started, 'error')
        raise
    else:
        status = outcome() if stage == 'send' and measured.status == 'ok' else measured.status
        if stage == 'media_prepare' and status == 'ok' and current is not None:
            changes = current['marks'][mark_start:]
            status = 'error' if 'error' in changes else 'degraded' if 'degraded' in changes else 'ok'
        record(stage, started, status)
