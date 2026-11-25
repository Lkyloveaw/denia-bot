"""Host and runtime facts for the dashboard panel, cached so polling stays cheap."""

from __future__ import annotations

import os
import platform
import re
import sys
import threading
import time

try:
    from importlib import metadata
except ImportError:  # pragma: no cover - Python without importlib.metadata
    metadata = None  # type: ignore[assignment]

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is optional here
    psutil = None  # type: ignore[assignment]

__all__ = ['clear_cache', 'snapshot']

DEPENDENCIES = (
    'fastapi',
    'uvicorn',
    'pydantic',
    'SQLAlchemy',
    'nonebot2',
    'openai',
    'httpx',
    'jieba',
    'psutil',
)

_LOCK = threading.Lock()
_cache: dict | None = None
_cached_at = 0.0


def clear_cache() -> None:
    """Drop the cached snapshot so the next call collects fresh facts."""
    global _cache, _cached_at
    with _LOCK:
        _cache = None
        _cached_at = 0.0


def _call(func, *args, **kwargs):
    """Run an optional probe; any failure degrades to None instead of raising."""
    try:
        return func(*args, **kwargs)
    except Exception:
        return None


def _os_name() -> str:
    system = platform.system()
    release = platform.release()
    if system == 'Windows':
        try:
            build = int(str(platform.version()).split('.')[2])
        except (IndexError, ValueError):
            build = 0
        if build >= 22000:
            return 'Windows 11'
    return f'{system} {release}'.strip()


def _cpu_name() -> str:
    if platform.system() == 'Windows':
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r'HARDWARE\DESCRIPTION\System\CentralProcessor\0',
            ) as key:
                value, _ = winreg.QueryValueEx(key, 'ProcessorNameString')
            if str(value).strip():
                return str(value).strip()
        except (ImportError, OSError, ValueError):
            pass
    name = _call(platform.processor) or ''
    if not name:
        name = _call(lambda: platform.uname().processor) or ''
    return str(name) or 'Unknown'


def _package_version(name: str) -> str:
    if metadata is None:
        return ''
    normalized = re.sub(r'[-_.]+', '-', name).lower()
    try:
        value = metadata.version(normalized)
    except Exception:
        return ''
    return str(value) if value else ''


def _dependencies() -> list[dict]:
    return [{'name': name, 'version': _package_version(name)} for name in DEPENDENCIES]


def _copy(data: dict) -> dict:
    """Keep the cached object private from callers that may mutate the result."""
    return {**data, 'dependencies': [dict(item) for item in data['dependencies']]}


def snapshot(*, cache_seconds: float = 60.0) -> dict:
    """Return JSON-safe host facts, reusing the previous result while it is fresh.

    ``cache_seconds`` at or below zero bypasses the cache. Missing psutil or
    package metadata degrades to ``None`` or ``''`` rather than raising.
    """
    global _cache, _cached_at
    with _LOCK:
        now = time.monotonic()
        if _cache is not None and cache_seconds > 0 and now - _cached_at < cache_seconds:
            return _copy(_cache)

        booted = _call(psutil.boot_time) if psutil is not None else None
        booted = float(booted) if booted is not None else None
        cores = _call(psutil.cpu_count, logical=False) if psutil is not None else None
        threads = _call(psutil.cpu_count, logical=True) if psutil is not None else None

        _cache = {
            'hostname': platform.node(),
            'os': _os_name(),
            'arch': platform.machine(),
            'python_implementation': platform.python_implementation(),
            'python_version': platform.python_version(),
            'compiler': platform.python_compiler(),
            'cpu': _cpu_name(),
            'cpu_cores': int(cores) if cores is not None else None,
            'cpu_threads': int(threads) if threads is not None else None,
            'executable': sys.executable,
            'pid': os.getpid(),
            'boot_time': booted,
            'uptime_seconds': max(0, int(time.time() - booted)) if booted is not None else None,
            'dependencies': _dependencies(),
        }
        _cached_at = time.monotonic()
        return _copy(_cache)
