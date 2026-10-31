"""Endfield 订阅/签到调度器（MIT）。

纯 asyncio：不 import nonebot/muicebot。
- 60s tick 由插件启动；测试直接传 `now`，不 sleep。
- 公告按 `published_at_ts + item_id` 去重；理智/调度券满时私聊；签到 opt-in。
- 推送通过 `PushSink` 注入；测试用 `RecordingSink`。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .client import (
    EndfieldAuthError,
    EndfieldClient,
    EndfieldError,
    EndfieldRateLimitError,
    EndfieldTransientError,
)
from .config import EndfieldConfig
from .store import BindingStore, StoreError, SubscriptionStore


class PushSink(Protocol):
    async def private(self, qq: str, text: str) -> bool: ...

    async def group(self, group_id: str, text: str) -> bool: ...


class RecordingSink:
    """测试/干跑用；默认全部成功。"""

    def __init__(self, *, fail_private: bool = False, fail_group: bool = False):
        self.private_messages: list[tuple[str, str]] = []
        self.group_messages: list[tuple[str, str]] = []
        self.fail_private = fail_private
        self.fail_group = fail_group

    async def private(self, qq: str, text: str) -> bool:
        if self.fail_private:
            return False
        self.private_messages.append((str(qq), text))
        return True

    async def group(self, group_id: str, text: str) -> bool:
        if self.fail_group:
            return False
        self.group_messages.append((str(group_id), text))
        return True


class EndfieldScheduler:
    def __init__(
        self,
        *,
        bindings: BindingStore,
        subscriptions: SubscriptionStore,
        client_factory: Callable[[str], EndfieldClient | None],
        sink: PushSink,
        config_provider: Callable[[], EndfieldConfig],
        heartbeat_path: str | os.PathLike[str] | None = None,
        audit_path: str | os.PathLike[str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.bindings = bindings
        self.subscriptions = subscriptions
        self.client_factory = client_factory
        self.sink = sink
        self.config_provider = config_provider
        self.heartbeat_path = Path(heartbeat_path) if heartbeat_path else None
        self.audit_path = Path(audit_path) if audit_path else None
        self.clock = clock or datetime.now
        self._push_counts: dict[tuple[str, str], int] = {}
        self._current: datetime | None = None
        self._last_announcement_poll = 0
        self._last_sanity_poll = 0
        self._last_ticket_poll = 0
        self._last_signin_poll = 0

    async def tick(self, now: datetime | None = None) -> None:
        current = now or self.clock()
        self._current = current
        config = self.config_provider()
        if not config.enabled:
            return
        if config.enable_subscriptions:
            await self._poll_announcements(current, config)
            if not _in_quiet(current, config):
                await self._poll_sanity(current, config)
                await self._poll_tickets(current, config)
        if config.enable_signin:
            await self._poll_signin(current, config)
        self._write_heartbeat(current)

    async def run_forever(self, interval: float = 60.0) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def _poll_announcements(
        self, now: datetime, config: EndfieldConfig
    ) -> None:
        subs = await self.subscriptions.list_announcements()
        if not subs:
            return
        self._last_announcement_poll = int(now.timestamp())
        client = self.client_factory("")
        if client is None:
            return
        try:
            latest = await client.get_announcement_latest()
        except (EndfieldError, StoreError):
            return
        finally:
            await client.close()
        if not isinstance(latest, Mapping):
            return
        item_id = str(latest.get("item_id") or latest.get("id") or "")
        try:
            ts = int(latest.get("published_at_ts") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts <= 0:
            return
        item_key = item_id or f"ts-{ts}"
        text = _announcement_text(latest)
        for group_id, sub in subs.items():
            if not isinstance(sub, Mapping) or not sub.get("enabled", True):
                continue
            if item_key == str(sub.get("last_item_id") or ""):
                continue
            try:
                since = int(sub.get("since_ts") or 0)
            except (TypeError, ValueError):
                since = 0
            if ts <= since:
                continue
            ok = await self._push_group(str(group_id), text, config)
            if ok:
                await self.subscriptions.update_announcement(
                    group_id,
                    since_ts=ts,
                    last_item_id=item_key,
                    fail_count=0,
                )
                self._audit("announcement_push", group=group_id, ok=True, item=item_key)
            else:
                fail_count = int(sub.get("fail_count") or 0) + 1
                await self.subscriptions.update_announcement(
                    group_id,
                    fail_count=fail_count,
                    enabled=fail_count < config.push_disable_after_failures,
                )
                self._audit("announcement_push", group=group_id, ok=False, item=item_key)

    async def _poll_sanity(self, now: datetime, config: EndfieldConfig) -> None:
        subs = await self.subscriptions.list_personal("sanity")
        if not subs:
            return
        self._last_sanity_poll = int(now.timestamp())
        for qq, sub in subs.items():
            if not isinstance(sub, Mapping) or not sub.get("enabled", True):
                continue
            binding = await self.bindings.get_primary(qq)
            if not binding or binding.get("status") != "valid":
                continue
            client = self.client_factory(str(qq))
            if client is None:
                continue
            try:
                data = await client.get_stamina(
                    str(binding.get("framework_token") or ""),
                    str(binding.get("role_id") or ""),
                    int(binding.get("server_id") or 1),
                )
            except EndfieldAuthError as exc:
                await self.bindings.mark_invalid(
                    qq, binding.get("role_id"), str(exc.code or exc.status or "auth")
                )
                continue
            except (EndfieldRateLimitError, EndfieldTransientError, EndfieldError):
                continue
            finally:
                await client.close()
            current, maximum = _stamina_values(data)
            if maximum <= 0:
                continue
            if current >= maximum:
                if int(sub.get("last_notified") or 0) == 1:
                    continue
                if not self._can_push(now, str(qq), config):
                    continue
                text = (
                    f"【理智已满】{binding.get('role_name') or '干员'}："
                    f"{current}/{maximum}，请及时消耗。"
                )
                if await self._push_private(str(qq), text, config):
                    await self.subscriptions.update_personal(
                        "sanity", qq, last_notified=1, fail_count=0
                    )
                    self._audit("sanity_push", ok=True)
                else:
                    await self._record_push_failure("sanity", qq, sub, config)
            elif int(sub.get("last_notified") or 0) == 1:
                await self.subscriptions.update_personal(
                    "sanity", qq, last_notified=0
                )

    async def _poll_tickets(self, now: datetime, config: EndfieldConfig) -> None:
        subs = await self.subscriptions.list_personal("tickets")
        if not subs:
            return
        self._last_ticket_poll = int(now.timestamp())
        for qq, sub in subs.items():
            if not isinstance(sub, Mapping) or not sub.get("enabled", True):
                continue
            binding = await self.bindings.get_primary(qq)
            if not binding or binding.get("status") != "valid":
                continue
            client = self.client_factory(str(qq))
            if client is None:
                continue
            try:
                data = await client.get_domain(
                    str(binding.get("framework_token") or ""),
                    str(binding.get("role_id") or ""),
                    int(binding.get("server_id") or 1),
                )
            except EndfieldAuthError as exc:
                await self.bindings.mark_invalid(
                    qq, binding.get("role_id"), str(exc.code or exc.status or "auth")
                )
                continue
            except (EndfieldRateLimitError, EndfieldTransientError, EndfieldError):
                continue
            finally:
                await client.close()
            full_zones = _full_zones(data)
            if full_zones:
                if int(sub.get("last_notified") or 0) == 1:
                    continue
                if not self._can_push(now, str(qq), config):
                    continue
                text = (
                    f"【调度券已满】{binding.get('role_name') or '干员'}：\n"
                    + "\n".join(f"• {name}" for name in full_zones[:10])
                )
                if await self._push_private(str(qq), text, config):
                    await self.subscriptions.update_personal(
                        "tickets", qq, last_notified=1, fail_count=0
                    )
                    self._audit("tickets_push", ok=True)
                else:
                    await self._record_push_failure("tickets", qq, sub, config)
            elif int(sub.get("last_notified") or 0) == 1:
                await self.subscriptions.update_personal(
                    "tickets", qq, last_notified=0
                )

    async def _poll_signin(self, now: datetime, config: EndfieldConfig) -> None:
        subs = await self.subscriptions.list_personal("signin")
        if not subs:
            return
        self._last_signin_poll = int(now.timestamp())
        today = now.date().isoformat()
        for qq, sub in subs.items():
            if not isinstance(sub, Mapping) or not sub.get("enabled", True):
                continue
            if str(sub.get("last_run_date") or "") == today:
                continue
            if not _signin_due(now, config, str(qq)):
                continue
            bindings = await self.bindings.list_bindings(qq)
            valid = [item for item in bindings if item.get("status") == "valid"]
            if not valid:
                continue
            client = self.client_factory(str(qq))
            if client is None:
                continue
            success = 0
            failures: list[str] = []
            try:
                for binding in valid:
                    try:
                        await client.post_attendance(
                            str(binding.get("framework_token") or ""),
                            str(binding.get("role_id") or ""),
                            int(binding.get("server_id") or 1),
                        )
                        success += 1
                    except EndfieldAuthError as exc:
                        failures.append("token 失效")
                        await self.bindings.mark_invalid(
                            qq,
                            binding.get("role_id"),
                            str(exc.code or exc.status or "auth"),
                        )
                    except EndfieldError:
                        failures.append("接口失败")
            finally:
                await client.close()
            result = "ok" if not failures else ("partial" if success else "failed")
            await self.subscriptions.update_personal(
                "signin",
                qq,
                last_run_date=today,
                last_result=result,
                last_error="；".join(failures)[:200],
            )
            self._audit(
                "signin", result=result, success=success, failed=len(failures)
            )
            if failures and not _in_quiet(now, config):
                await self._push_private(
                    str(qq),
                    f"自动签到完成：成功 {success}/{len(valid)}；"
                    f"{'；'.join(failures[:3])}",
                    config,
                )

    async def _push_private(
        self, qq: str, text: str, config: EndfieldConfig
    ) -> bool:
        for _ in range(1 + max(0, int(config.push_retry))):
            if await self.sink.private(qq, text):
                current = self._current or datetime.now()
                key = (current.date().isoformat(), qq)
                self._push_counts[key] = self._push_counts.get(key, 0) + 1
                return True
        return False

    async def _push_group(
        self, group_id: str, text: str, config: EndfieldConfig
    ) -> bool:
        for _ in range(1 + max(0, int(config.push_retry))):
            if await self.sink.group(group_id, text):
                return True
        return False

    def _can_push(self, now: datetime, qq: str, config: EndfieldConfig) -> bool:
        limit = int(config.daily_push_limit)
        if limit <= 0:
            return True
        key = (now.date().isoformat(), qq)
        return self._push_counts.get(key, 0) < limit

    async def _record_push_failure(
        self, kind: str, qq: str, sub: Mapping[str, Any], config: EndfieldConfig
    ) -> None:
        fail_count = int(sub.get("fail_count") or 0) + 1
        await self.subscriptions.update_personal(
            kind,
            qq,
            fail_count=fail_count,
            enabled=fail_count < config.push_disable_after_failures,
        )
        self._audit("push_failure", kind=kind, fail_count=fail_count)

    def _audit(self, event: str, **fields: Any) -> None:
        if self.audit_path is None:
            return
        current = self._current or datetime.now()
        row = {"ts": int(current.timestamp()), "event": event, **fields}
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            if (
                self.audit_path.exists()
                and self.audit_path.stat().st_size > 1024 * 1024
            ):
                os.replace(
                    self.audit_path,
                    self.audit_path.with_name(self.audit_path.name + ".1"),
                )
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            try:
                os.chmod(self.audit_path, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    def _write_heartbeat(self, now: datetime) -> None:
        if self.heartbeat_path is None:
            return
        payload = {
            "ts": int(now.timestamp()),
            "iso": now.isoformat(timespec="seconds"),
            "version": "0.1.0",
            "last_announcement_poll": self._last_announcement_poll,
            "last_sanity_poll": self._last_sanity_poll,
            "last_ticket_poll": self._last_ticket_poll,
            "last_signin_poll": self._last_signin_poll,
        }
        _write_json_atomic(self.heartbeat_path, payload)


def _in_quiet(now: datetime, config: EndfieldConfig) -> bool:
    start = _minutes(config.quiet_start, 23 * 60)
    end = _minutes(config.quiet_end, 8 * 60)
    current = now.hour * 60 + now.minute
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _minutes(value: str, default: int) -> int:
    try:
        hour, minute = str(value).split(":")
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return default


def _stamina_values(data: Any) -> tuple[int, int]:
    payload = data if isinstance(data, Mapping) else {}
    stamina = payload.get("stamina") if isinstance(payload.get("stamina"), Mapping) else payload
    try:
        current = int(stamina.get("current") or 0)
        maximum = int(stamina.get("max") or 0)
    except (TypeError, ValueError):
        return 0, 0
    return current, maximum


def _full_zones(data: Any) -> list[str]:
    payload = data if isinstance(data, Mapping) else {}
    zones = payload.get("domain") or []
    names: list[str] = []
    if not isinstance(zones, list):
        return names
    for zone in zones:
        if not isinstance(zone, Mapping):
            continue
        settlements = zone.get("settlements") or []
        if not isinstance(settlements, list):
            continue
        for settlement in settlements:
            if not isinstance(settlement, Mapping):
                continue
            try:
                remain = float(settlement.get("remainMoney") or 0)
                maximum = float(settlement.get("moneyMax") or 0)
            except (TypeError, ValueError):
                continue
            if maximum > 0 and remain >= maximum:
                names.append(str(settlement.get("name") or "未知聚落"))
    return names


def _announcement_text(item: Mapping[str, Any]) -> str:
    title = str(item.get("title") or "终末地新公告")
    summary = str(item.get("summary") or "").strip()
    text = f"【终末地新公告】\n{title}"
    if summary:
        text += f"\n{summary[:800]}"
    return text[:1200]


def _signin_due(now: datetime, config: EndfieldConfig, qq: str) -> bool:
    hour, minute = _minutes_values(config.signin_time)
    jitter = max(0, int(config.signin_jitter_minutes))
    offset = int(qq) % (jitter + 1) if str(qq).isdigit() else 0
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    scheduled += timedelta(minutes=offset)
    return now >= scheduled


def _minutes_values(value: str) -> tuple[int, int]:
    try:
        hour, minute = str(value).split(":")
        return int(hour), int(minute)
    except (TypeError, ValueError):
        return 0, 5


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
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, path)
        tmp_name = None
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
