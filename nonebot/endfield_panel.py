"""Endfield 面板后端（MIT）。

由 dashboard.register_endfield 挂到现有 FastAPI app；鉴权复用 dashboard 的
authorized/mutate（会话 + CSRF/Origin），不自己造登录。
面板只写 endfield.json / endfield_secrets.json；绑定/订阅/心跳/audit 只读。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from endfield import client as ec
from endfield import config as ecfg
from endfield import render as er
from endfield import store as estore

Authorized = Callable[[Request], Any]
Mutate = Callable[[Request], Any]
RenderHealth = Callable[[ecfg.EndfieldConfig], Awaitable[Mapping[str, Any]]]


def register(
    app: FastAPI,
    *,
    authorized: Authorized,
    mutate: Mutate,
    root: Path,
    render_health: RenderHealth | None = None,
) -> None:
    data_dir = root / "data" / "endfield"
    config_path = root / "data" / "endfield.json"
    secrets_path = root / "data" / "endfield_secrets.json"
    bindings_path = data_dir / "bindings.json"
    subscriptions_path = data_dir / "subscriptions.json"
    heartbeat_path = data_dir / "heartbeat.json"
    audit_path = data_dir / "audit.jsonl"
    key_tests: dict[str, list[float]] = {}

    def load_config() -> ecfg.EndfieldConfig:
        return ecfg.load_config(config_path)

    def key_info() -> dict[str, Any]:
        env_key = os.environ.get("ENDFIELD_API_KEY") or ""
        panel_key = ""
        try:
            raw = json.loads(secrets_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                panel_key = str(raw.get("api_key") or "").strip()
        except (OSError, json.JSONDecodeError):
            pass
        key = panel_key or env_key
        source = "panel" if panel_key else ("env" if env_key else "none")
        return {
            "configured": bool(key),
            "masked": _mask_key(key),
            "source": source,
        }

    async def default_render_health(config: ecfg.EndfieldConfig) -> Mapping[str, Any]:
        client = er.RenderClient(config.render_url, timeout=3.0)
        try:
            return await client.health()
        except er.RenderError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            await client.close()

    @app.get("/api/endfield/status")
    async def endfield_status(req: Request) -> Mapping[str, Any]:
        authorized(req)
        config = load_config()
        heartbeat = _read_json(heartbeat_path)
        bindings = await estore.BindingStore(bindings_path).all_users()
        subscriptions = await estore.SubscriptionStore(subscriptions_path).load()
        binding_count = sum(
            len(user.get("bindings", []))
            for user in bindings.values()
            if isinstance(user, Mapping)
        )
        announcements = subscriptions.get("announcements", {})
        personal = sum(
            len(subscriptions.get(kind, {}))
            for kind in ("sanity", "tickets", "signin")
            if isinstance(subscriptions.get(kind), Mapping)
        )
        health = (
            render_health(config)
            if render_health is not None
            else default_render_health(config)
        )
        renderer = await health
        stale = _heartbeat_stale(heartbeat)
        return {
            "enabled": config.enabled,
            "key": key_info(),
            "heartbeat": heartbeat,
            "heartbeat_stale": stale,
            "bindings": {"users": len(bindings), "items": binding_count},
            "subscriptions": {
                "announcement_groups": len(announcements) if isinstance(announcements, Mapping) else 0,
                "personal": personal,
            },
            "renderer": renderer,
            "config": config.to_dict(),
        }

    @app.get("/api/endfield/config")
    def endfield_get_config(req: Request) -> Mapping[str, Any]:
        authorized(req)
        return ecfg.public_view(load_config())

    @app.post("/api/endfield/config")
    async def endfield_set_config(req: Request) -> Mapping[str, Any]:
        mutate(req)
        try:
            body = await req.json()
        except Exception as exc:
            raise HTTPException(400, "invalid json") from exc
        if not isinstance(body, Mapping):
            raise HTTPException(400, "config must be object")
        config = ecfg.EndfieldConfig.from_mapping({**load_config().to_dict(), **body})
        for key, value in body.items():
            if key not in ecfg.DEFAULTS or config.to_dict().get(key) != value:
                raise HTTPException(400, f"配置项无效：{key}")
        ecfg.save_config(config, config_path)
        return ecfg.public_view(config)

    @app.get("/api/endfield/bindings")
    async def endfield_bindings(req: Request) -> Mapping[str, Any]:
        authorized(req)
        users = await estore.BindingStore(bindings_path).all_users()
        rows = []
        for qq, user in users.items():
            for item in user.get("bindings", []):
                rows.append({"user": estore.mask_role_id(qq), **estore.public_binding(item)})
        return {"count": len(rows), "items": rows}

    @app.get("/api/endfield/subscriptions")
    async def endfield_subscriptions(req: Request) -> Mapping[str, Any]:
        authorized(req)
        data = await estore.SubscriptionStore(subscriptions_path).load()
        return {
            "announcement_groups": data.get("announcements", {}),
            "counts": {
                kind: len(data.get(kind, {}))
                for kind in ("sanity", "tickets", "signin")
            },
        }

    @app.get("/api/endfield/audit")
    def endfield_audit(req: Request, limit: int = 50) -> Mapping[str, Any]:
        authorized(req)
        rows = _tail_jsonl(audit_path, max(1, min(200, int(limit))))
        return {"items": rows}

    @app.get("/api/endfield/render/health")
    async def endfield_render_health(req: Request) -> Mapping[str, Any]:
        authorized(req)
        config = load_config()
        if render_health is not None:
            return await render_health(config)
        return await default_render_health(config)

    @app.get("/api/endfield/key")
    def endfield_key(req: Request) -> Mapping[str, Any]:
        authorized(req)
        return key_info()

    @app.post("/api/endfield/key")
    async def endfield_set_key(req: Request) -> Mapping[str, Any]:
        mutate(req)
        body = await _json_body(req)
        key = str(body.get("api_key") or "").strip()
        if len(key) < 10:
            raise HTTPException(400, "api key is too short")
        _write_json(secrets_path, {"api_key": key})
        return key_info()

    @app.delete("/api/endfield/key")
    def endfield_delete_key(req: Request) -> Mapping[str, Any]:
        mutate(req)
        try:
            secrets_path.unlink()
        except FileNotFoundError:
            pass
        return key_info()

    @app.post("/api/endfield/key/import-env")
    def endfield_import_env(req: Request) -> Mapping[str, Any]:
        mutate(req)
        key = str(os.environ.get("ENDFIELD_API_KEY") or "").strip()
        if len(key) < 10:
            raise HTTPException(400, "ENDFIELD_API_KEY is not set")
        _write_json(secrets_path, {"api_key": key})
        return key_info()

    @app.post("/api/endfield/key/test")
    async def endfield_test_key(req: Request) -> Mapping[str, Any]:
        mutate(req)
        host = getattr(getattr(req, "client", None), "host", "local")
        now = time.monotonic()
        bucket = key_tests.setdefault(str(host), [])
        bucket[:] = [stamp for stamp in bucket if now - stamp < 60]
        if len(bucket) >= 5:
            raise HTTPException(429, "too many key tests")
        bucket.append(now)
        body = await _json_body(req)
        key = str(body.get("api_key") or "").strip() or _read_key(secrets_path, env_fallback=True)
        if not key:
            return {"ok": False, "error": "key not configured"}
        client = ec.EndfieldClient(key, timeout=10.0)
        try:
            await client.get_announcement_latest()
            return {"ok": True}
        except ec.EndfieldError as exc:
            return {"ok": False, "error": type(exc).__name__, "message": "连接失败，请检查密钥、网络或网关状态。"}
        finally:
            await client.close()

    @app.get("/endfield")
    def endfield_page() -> RedirectResponse:
        return RedirectResponse("/#endfield")


def _read_key(path: Path, *, env_fallback: bool = False) -> str:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            key = str(raw.get("api_key") or "").strip()
            if key:
                return key
    except (OSError, json.JSONDecodeError):
        pass
    return str(os.environ.get("ENDFIELD_API_KEY") or "").strip() if env_fallback else ""


def _mask_key(key: str) -> str:
    text = str(key or "")
    return "" if not text else ("****" + text[-4:] if len(text) > 4 else "****")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _tail_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except json.JSONDecodeError:
            continue
    return rows


def _heartbeat_stale(heartbeat: Mapping[str, Any]) -> bool:
    try:
        ts = int(heartbeat.get("ts") or 0)
    except (TypeError, ValueError):
        return True
    return ts <= 0 or time.time() - ts > 180


async def _json_body(req: Request) -> Mapping[str, Any]:
    try:
        body = await req.json()
    except Exception as exc:
        raise HTTPException(400, "invalid json") from exc
    if not isinstance(body, Mapping):
        raise HTTPException(400, "body must be object")
    return body


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
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
