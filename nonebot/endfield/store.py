# 移植自 Entropy-Increase-Team/astrbot_plugin_endfield v3.0.3（commit 41482b7）
# 的 core/user.py；上游作者：Entropy-Increase-Team。
# 本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写绑定/订阅数据模型、原子写与多账号约束；
# 详见 docs/third-party/endfield-NOTICE.md

"""Endfield 绑定与订阅存储（AGPL-3.0-only）。

纯存储：不 import nonebot/muicebot，不发 HTTP。
所有写操作：内存锁 + `.tmp` + `os.replace` + 0600。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

MAX_BINDINGS_PER_USER = 3
BINDING_STATUSES = {"valid", "invalid"}
PERSONAL_KINDS = {"sanity", "tickets", "signin"}


class StoreError(ValueError):
    """数据文件损坏、字段非法或操作不允许。"""


class ConsentRequired(StoreError):
    """用户尚未确认第三方数据告知。"""


class BindingLimitExceeded(StoreError):
    """超过每 QQ 绑定上限。"""


class BindingNotFound(StoreError):
    """按序号/role_id 找不到绑定。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mask_role_id(role_id: Any) -> str:
    text = str(role_id or "")
    if len(text) <= 4:
        return "****"
    return f"{text[:2]}****{text[-2:]}"


def _normalize_binding(raw: Mapping[str, Any]) -> dict[str, Any]:
    role_id = str(raw.get("role_id") or "").strip()
    token = str(raw.get("framework_token") or "").strip()
    if not role_id:
        raise StoreError("role_id is required")
    if not token:
        raise StoreError("framework_token is required")
    try:
        server_id = int(raw.get("server_id") or 1)
    except (TypeError, ValueError) as exc:
        raise StoreError("server_id must be an integer") from exc
    if server_id < 0:
        raise StoreError("server_id must be >= 0")
    status = str(raw.get("status") or "valid")
    if status not in BINDING_STATUSES:
        raise StoreError(f"invalid status: {status}")
    now = _now()
    return {
        "binding_id": str(raw.get("binding_id") or ""),
        "role_id": role_id,
        "server_id": server_id,
        "role_name": str(raw.get("role_name") or role_id),
        "alias": str(raw.get("alias") or ""),
        "provider": str(raw.get("provider") or "skland"),
        "framework_token": token,
        "is_primary": bool(raw.get("is_primary")),
        "status": status,
        "invalid_reason": raw.get("invalid_reason"),
        "invalid_at": raw.get("invalid_at"),
        "created_at": str(raw.get("created_at") or now),
        "updated_at": now,
        "last_used_at": raw.get("last_used_at"),
    }


def public_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    """面板/列表用：去掉 token，role_id 脱敏。"""
    return {
        "binding_id": binding.get("binding_id", ""),
        "role_id": mask_role_id(binding.get("role_id")),
        "server_id": binding.get("server_id", 1),
        "role_name": binding.get("role_name", ""),
        "alias": binding.get("alias", ""),
        "provider": binding.get("provider", "skland"),
        "token_configured": bool(binding.get("framework_token")),
        "is_primary": bool(binding.get("is_primary")),
        "status": binding.get("status", "valid"),
        "invalid_reason": binding.get("invalid_reason"),
        "invalid_at": binding.get("invalid_at"),
        "created_at": binding.get("created_at"),
        "updated_at": binding.get("updated_at"),
        "last_used_at": binding.get("last_used_at"),
    }


class BindingStore:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] | None = None

    @staticmethod
    def _default() -> dict[str, Any]:
        return {"version": 1, "users": {}}

    def _load_sync(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.path.exists():
            self._data = self._default()
            return self._data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"cannot read bindings: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise StoreError("bindings must be version 1")
        users = raw.get("users")
        if not isinstance(users, dict):
            raise StoreError("bindings.users must be an object")
        self._data = raw
        return raw

    async def _save(self) -> None:
        assert self._data is not None
        _write_json_atomic(self.path, self._data)

    async def load(self) -> dict[str, Any]:
        async with self._lock:
            return copy.deepcopy(self._load_sync())

    async def get_user(self, qq: str | int) -> dict[str, Any] | None:
        async with self._lock:
            user = self._load_sync()["users"].get(str(qq))
            return copy.deepcopy(user) if user else None

    async def set_consent(
        self,
        qq: str | int,
        *,
        version: int = 1,
        at: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            users = self._load_sync()["users"]
            user = users.setdefault(
                str(qq), {"consent_version": 0, "consent_at": None, "bindings": []}
            )
            user["consent_version"] = int(version)
            user["consent_at"] = at or _now()
            await self._save()
            return copy.deepcopy(user)

    async def add_binding(
        self,
        qq: str | int,
        raw: Mapping[str, Any],
        *,
        consent_version: int = 1,
    ) -> dict[str, Any]:
        async with self._lock:
            users = self._load_sync()["users"]
            user = users.get(str(qq))
            if not user or int(user.get("consent_version") or 0) < consent_version:
                raise ConsentRequired("consent required before binding")
            normalized = _normalize_binding(raw)
            bindings = user.setdefault("bindings", [])
            existing = _find_binding(
                bindings, normalized["role_id"], normalized["server_id"]
            )
            if existing is not None:
                existing.update(
                    {
                        "binding_id": normalized["binding_id"] or existing.get("binding_id", ""),
                        "framework_token": normalized["framework_token"],
                        "role_name": normalized["role_name"],
                        "alias": normalized["alias"] or existing.get("alias", ""),
                        "provider": normalized["provider"],
                        "status": "valid",
                        "invalid_reason": None,
                        "invalid_at": None,
                        "updated_at": normalized["updated_at"],
                    }
                )
                await self._save()
                return copy.deepcopy(existing)
            if len(bindings) >= MAX_BINDINGS_PER_USER:
                raise BindingLimitExceeded(
                    f"at most {MAX_BINDINGS_PER_USER} bindings per user"
                )
            normalized["is_primary"] = not bindings
            bindings.append(normalized)
            await self._save()
            return copy.deepcopy(normalized)

    async def list_bindings(self, qq: str | int) -> list[dict[str, Any]]:
        async with self._lock:
            user = self._load_sync()["users"].get(str(qq)) or {}
            return copy.deepcopy(user.get("bindings", []))

    async def public_bindings(self, qq: str | int) -> list[dict[str, Any]]:
        return [public_binding(item) for item in await self.list_bindings(qq)]

    async def get_primary(self, qq: str | int) -> dict[str, Any] | None:
        bindings = await self.list_bindings(qq)
        for item in bindings:
            if item.get("is_primary"):
                return item
        return bindings[0] if bindings else None

    async def set_primary(self, qq: str | int, ref: str | int) -> dict[str, Any]:
        async with self._lock:
            user = self._require_user(qq)
            bindings = user.get("bindings", [])
            index = _resolve_index(bindings, ref)
            for item in bindings:
                item["is_primary"] = False
            bindings[index]["is_primary"] = True
            bindings[index]["updated_at"] = _now()
            await self._save()
            return copy.deepcopy(bindings[index])

    async def remove_binding(self, qq: str | int, ref: str | int) -> bool:
        async with self._lock:
            user = self._load_sync()["users"].get(str(qq))
            if not user:
                return False
            bindings = user.get("bindings", [])
            if not bindings:
                return False
            index = _resolve_index(bindings, ref)
            removed = bindings.pop(index)
            if removed.get("is_primary") and bindings:
                bindings[0]["is_primary"] = True
            await self._save()
            return True

    async def mark_invalid(
        self,
        qq: str | int,
        ref: str | int,
        reason: str | None = None,
        *,
        when: str | None = None,
        server_id: int | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            user = self._require_user(qq)
            bindings = user.get("bindings", [])
            if server_id is None:
                binding = bindings[_resolve_index(bindings, ref)]
            else:
                binding = _find_binding(bindings, str(ref), int(server_id))
                if binding is None:
                    raise BindingNotFound("binding no longer exists")
            binding["status"] = "invalid"
            binding["invalid_reason"] = reason or "auth_invalid"
            binding["invalid_at"] = when or _now()
            binding["updated_at"] = _now()
            await self._save()
            return copy.deepcopy(binding)

    async def all_users(self) -> dict[str, Any]:
        async with self._lock:
            return copy.deepcopy(self._load_sync()["users"])

    def _require_user(self, qq: str | int) -> dict[str, Any]:
        user = self._load_sync()["users"].get(str(qq))
        if not user:
            raise BindingNotFound(f"no bindings for {qq}")
        return user


class SubscriptionStore:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] | None = None

    @staticmethod
    def _default() -> dict[str, Any]:
        return {
            "version": 1,
            "announcements": {},
            "sanity": {},
            "tickets": {},
            "signin": {},
            "meta": {},
        }

    def _load_sync(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.path.exists():
            self._data = self._default()
            return self._data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"cannot read subscriptions: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise StoreError("subscriptions must be version 1")
        self._data = raw
        return raw

    async def _save(self) -> None:
        assert self._data is not None
        _write_json_atomic(self.path, self._data)

    async def load(self) -> dict[str, Any]:
        async with self._lock:
            return copy.deepcopy(self._load_sync())

    async def add_announcement(
        self,
        group_id: str | int,
        created_by: str | int,
        *,
        since_ts: int = 0,
        last_item_id: str = "",
    ) -> dict[str, Any]:
        async with self._lock:
            now = _now()
            item = self._load_sync().setdefault("announcements", {}).setdefault(
                str(group_id),
                {
                    "target": f"group:{group_id}",
                    "since_ts": 0,
                    "last_item_id": "",
                    "enabled": True,
                    "fail_count": 0,
                    "created_by": str(created_by),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            item.update(
                {
                    "since_ts": int(since_ts or 0),
                    "last_item_id": str(last_item_id or ""),
                    "enabled": True,
                    "updated_at": now,
                    "fail_count": 0,
                    "last_error": "",
                }
            )
            await self._save()
            return copy.deepcopy(item)

    async def remove_announcement(self, group_id: str | int) -> bool:
        async with self._lock:
            items = self._load_sync().setdefault("announcements", {})
            if str(group_id) not in items:
                return False
            del items[str(group_id)]
            await self._save()
            return True

    async def list_announcements(self) -> dict[str, Any]:
        async with self._lock:
            return copy.deepcopy(self._load_sync().get("announcements", {}))

    async def update_announcement(
        self,
        group_id: str | int,
        *,
        since_ts: int | None = None,
        last_item_id: str | None = None,
        enabled: bool | None = None,
        fail_count: int | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            item = self._require_announcement(group_id)
            if since_ts is not None:
                item["since_ts"] = int(since_ts)
            if last_item_id is not None:
                item["last_item_id"] = str(last_item_id)
            if enabled is not None:
                item["enabled"] = bool(enabled)
            if fail_count is not None:
                item["fail_count"] = int(fail_count)
            item["updated_at"] = _now()
            await self._save()
            return copy.deepcopy(item)

    async def set_personal(
        self,
        kind: str,
        qq: str | int,
        *,
        enabled: bool = True,
        target: str | None = None,
    ) -> dict[str, Any]:
        if kind not in PERSONAL_KINDS:
            raise StoreError(f"unknown subscription kind: {kind}")
        async with self._lock:
            section = self._load_sync().setdefault(kind, {})
            item = section.setdefault(
                str(qq),
                {
                    "target": target or f"private:{qq}",
                    "enabled": True,
                    "last_notified": 0,
                    "last_checked": 0,
                    "fail_count": 0,
                },
            )
            item["enabled"] = bool(enabled)
            if enabled:
                item["fail_count"] = 0
                item["last_error"] = ""
            if target:
                item["target"] = str(target)
            if kind == "signin":
                item.setdefault("last_run_date", "")
                item.setdefault("last_result", "")
                item.setdefault("last_error", "")
            await self._save()
            return copy.deepcopy(item)

    async def remove_personal(self, kind: str, qq: str | int) -> bool:
        if kind not in PERSONAL_KINDS:
            raise StoreError(f"unknown subscription kind: {kind}")
        async with self._lock:
            section = self._load_sync().setdefault(kind, {})
            if str(qq) not in section:
                return False
            del section[str(qq)]
            await self._save()
            return True

    async def list_personal(self, kind: str | None = None) -> dict[str, Any]:
        async with self._lock:
            data = self._load_sync()
            if kind is not None:
                if kind not in PERSONAL_KINDS:
                    raise StoreError(f"unknown subscription kind: {kind}")
                return copy.deepcopy(data.get(kind, {}))
            return {
                name: copy.deepcopy(data.get(name, {}))
                for name in sorted(PERSONAL_KINDS)
            }

    async def update_personal(
        self, kind: str, qq: str | int, **fields: Any
    ) -> dict[str, Any]:
        if kind not in PERSONAL_KINDS:
            raise StoreError(f"unknown subscription kind: {kind}")
        async with self._lock:
            item = self._require_personal(kind, qq)
            item.update(fields)
            await self._save()
            return copy.deepcopy(item)

    async def update_last_notified(
        self, kind: str, qq: str | int, value: int
    ) -> dict[str, Any]:
        if kind not in {"sanity", "tickets"}:
            raise StoreError(f"kind does not support last_notified: {kind}")
        async with self._lock:
            item = self._require_personal(kind, qq)
            item["last_notified"] = int(value)
            await self._save()
            return copy.deepcopy(item)

    async def update_meta(self, key: str, value: Any) -> dict[str, Any]:
        async with self._lock:
            meta = self._load_sync().setdefault("meta", {})
            meta[str(key)] = value
            await self._save()
            return copy.deepcopy(meta)

    async def get_meta(self) -> dict[str, Any]:
        async with self._lock:
            return copy.deepcopy(self._load_sync().get("meta", {}))

    def _require_announcement(self, group_id: str | int) -> dict[str, Any]:
        item = self._load_sync().setdefault("announcements", {}).get(str(group_id))
        if not item:
            raise StoreError(f"announcement subscription not found: {group_id}")
        return item

    def _require_personal(self, kind: str, qq: str | int) -> dict[str, Any]:
        item = self._load_sync().setdefault(kind, {}).get(str(qq))
        if not item:
            raise StoreError(f"{kind} subscription not found: {qq}")
        return item


def _find_binding(
    bindings: list[dict[str, Any]], role_id: str, server_id: int
) -> dict[str, Any] | None:
    for item in bindings:
        if str(item.get("role_id")) == role_id and int(item.get("server_id") or 1) == server_id:
            return item
    return None


def _resolve_index(bindings: list[dict[str, Any]], ref: str | int) -> int:
    if not bindings:
        raise BindingNotFound("no bindings")
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        index = int(ref) - 1
        if not (0 <= index < len(bindings)):
            raise BindingNotFound(f"binding index out of range: {ref}")
        return index
    text = str(ref)
    for index, item in enumerate(bindings):
        if text in {
            str(item.get("role_id")),
            str(item.get("binding_id")),
            str(item.get("role_name")),
        }:
            return index
    raise BindingNotFound(f"binding not found: {ref}")


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
