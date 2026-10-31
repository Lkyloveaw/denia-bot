"""Endfield 非敏感配置（MIT）。

纯代码：不 import nonebot/muicebot，也不做网络 I/O。
配置文件默认 `nonebot/data/endfield.json`，由 dashboard 写、bot 按 mtime 重载。
API Key 不在这里；见 #38 的 `endfield_secrets.json`。
"""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

DEFAULT_RENDER_URL = "http://127.0.0.1:3100"
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "enable_queries": True,
    "enable_subscriptions": True,
    "enable_signin": True,
    "announcement_poll_minutes": 10,
    "sanity_poll_minutes": 20,
    "ticket_poll_minutes": 20,
    "signin_time": "00:05",
    "signin_jitter_minutes": 60,
    "quiet_start": "23:00",
    "quiet_end": "08:00",
    "daily_push_limit": 10,
    "push_retry": 1,
    "push_disable_after_failures": 3,
    "render_url": DEFAULT_RENDER_URL,
    "render_timeout_seconds": 20,
    "log_level": "INFO",
    "audit_max_entries": 200,
}


class ConfigError(ValueError):
    """配置文件损坏、不可读或不是 JSON 对象。"""


@dataclass(frozen=True)
class ConfigStamp:
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class EndfieldConfig:
    enabled: bool = True
    enable_queries: bool = True
    enable_subscriptions: bool = True
    enable_signin: bool = True
    announcement_poll_minutes: int = 10
    sanity_poll_minutes: int = 20
    ticket_poll_minutes: int = 20
    signin_time: str = "00:05"
    signin_jitter_minutes: int = 60
    quiet_start: str = "23:00"
    quiet_end: str = "08:00"
    daily_push_limit: int = 10
    push_retry: int = 1
    push_disable_after_failures: int = 3
    render_url: str = DEFAULT_RENDER_URL
    render_timeout_seconds: int = 20
    log_level: str = "INFO"
    audit_max_entries: int = 200

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "EndfieldConfig":
        """宽松解析：类型/范围非法的字段回退默认值，不抛异常。"""
        raw = data if isinstance(data, Mapping) else {}
        return cls(
            enabled=_as_bool(raw.get("enabled"), DEFAULTS["enabled"]),
            enable_queries=_as_bool(raw.get("enable_queries"), DEFAULTS["enable_queries"]),
            enable_subscriptions=_as_bool(
                raw.get("enable_subscriptions"), DEFAULTS["enable_subscriptions"]
            ),
            enable_signin=_as_bool(raw.get("enable_signin"), DEFAULTS["enable_signin"]),
            announcement_poll_minutes=_as_int(
                raw.get("announcement_poll_minutes"),
                DEFAULTS["announcement_poll_minutes"],
                5,
                60,
            ),
            sanity_poll_minutes=_as_int(
                raw.get("sanity_poll_minutes"), DEFAULTS["sanity_poll_minutes"], 10, 120
            ),
            ticket_poll_minutes=_as_int(
                raw.get("ticket_poll_minutes"), DEFAULTS["ticket_poll_minutes"], 10, 120
            ),
            signin_time=_as_time(raw.get("signin_time"), DEFAULTS["signin_time"]),
            signin_jitter_minutes=_as_int(
                raw.get("signin_jitter_minutes"),
                DEFAULTS["signin_jitter_minutes"],
                0,
                120,
            ),
            quiet_start=_as_time(raw.get("quiet_start"), DEFAULTS["quiet_start"]),
            quiet_end=_as_time(raw.get("quiet_end"), DEFAULTS["quiet_end"]),
            daily_push_limit=_as_int(
                raw.get("daily_push_limit"), DEFAULTS["daily_push_limit"], 0, 100
            ),
            push_retry=_as_int(raw.get("push_retry"), DEFAULTS["push_retry"], 0, 3),
            push_disable_after_failures=_as_int(
                raw.get("push_disable_after_failures"),
                DEFAULTS["push_disable_after_failures"],
                1,
                10,
            ),
            render_url=_as_render_url(raw.get("render_url"), DEFAULT_RENDER_URL),
            render_timeout_seconds=_as_int(
                raw.get("render_timeout_seconds"),
                DEFAULTS["render_timeout_seconds"],
                1,
                60,
            ),
            log_level=_as_log_level(raw.get("log_level"), DEFAULTS["log_level"]),
            audit_max_entries=_as_int(
                raw.get("audit_max_entries"), DEFAULTS["audit_max_entries"], 50, 1000
            ),
        )


def load_config(path: str | os.PathLike[str]) -> EndfieldConfig:
    """读取配置；文件不存在返回默认值；损坏的 JSON/非对象抛 ConfigError。"""
    file_path = Path(path)
    if not file_path.exists():
        return EndfieldConfig()
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid config JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    return EndfieldConfig.from_mapping(data)


def save_config(config: EndfieldConfig, path: str | os.PathLike[str]) -> None:
    """原子写 0600；不写 key/token（配置本身不含秘密）。"""
    _write_json_atomic(Path(path), config.to_dict())


def public_view(config: EndfieldConfig) -> dict[str, Any]:
    """面板展示用；配置没有秘密，原样返回字典。"""
    return config.to_dict()


def _stamp(path: Path) -> ConfigStamp:
    try:
        stat = path.stat()
    except OSError:
        return ConfigStamp(0, 0)
    return ConfigStamp(int(stat.st_mtime_ns), int(stat.st_size))


def load_with_stamp(
    path: str | os.PathLike[str],
) -> tuple[EndfieldConfig, ConfigStamp]:
    file_path = Path(path)
    return load_config(file_path), _stamp(file_path)


def reload_if_changed(
    path: str | os.PathLike[str],
    cached: EndfieldConfig,
    stamp: ConfigStamp,
) -> tuple[EndfieldConfig, ConfigStamp, bool]:
    """mtime/size 未变则用缓存；变了则重读。返回 (config, stamp, changed)。"""
    file_path = Path(path)
    current = _stamp(file_path)
    if current == stamp:
        return cached, stamp, False
    return load_config(file_path), current, True


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _chmod_600(tmp_name)
        os.replace(tmp_name, path)
        tmp_name = None
        _chmod_600(path)
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _chmod_600(path: str | os.PathLike[str]) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _as_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if minimum <= number <= maximum else default


def _as_time(value: Any, default: str) -> str:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        return default
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


def _as_log_level(value: Any, default: str) -> str:
    text = str(value or "").strip().upper()
    return text if text in LOG_LEVELS else default


def _as_render_url(value: Any, default: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return default
    try:
        parsed = urlparse(text)
    except ValueError:
        return default
    if parsed.scheme not in {"http", "https"}:
        return default
    if not parsed.hostname or parsed.username or parsed.password:
        return default
    try:
        port = parsed.port
    except ValueError:
        return default
    if port is not None and not (1 <= port <= 65535):
        return default
    if not _is_private_host(parsed.hostname):
        return default
    return text


def _is_private_host(host: str) -> bool:
    lowered = host.strip().lower()
    if lowered in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local)
