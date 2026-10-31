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
from fastapi.responses import HTMLResponse

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
        config = ecfg.EndfieldConfig.from_mapping(body)
        ecfg.save_config(config, config_path)
        return ecfg.public_view(config)

    @app.get("/api/endfield/bindings")
    async def endfield_bindings(req: Request) -> Mapping[str, Any]:
        authorized(req)
        users = await estore.BindingStore(bindings_path).all_users()
        rows = []
        for user in users.values():
            for item in user.get("bindings", []):
                rows.append(estore.public_binding(item))
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
        info = key_info()
        if not info["configured"]:
            return {"ok": False, "error": "key not configured"}
        key = _read_key(secrets_path, env_fallback=True)
        client = ec.EndfieldClient(key, timeout=10.0)
        try:
            await client.get_announcement_latest()
            return {"ok": True}
        except ec.EndfieldError as exc:
            return {"ok": False, "error": type(exc).__name__, "message": exc.message}
        finally:
            await client.close()

    @app.get("/endfield", response_class=HTMLResponse)
    def endfield_page() -> str:
        return _PAGE


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


_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>终末地 · 面板</title>
<style>
body{margin:0;background:#0f131b;color:#e8edf7;font-family:'Noto Sans CJK SC','Microsoft YaHei',sans-serif}
main{max-width:960px;margin:0 auto;padding:32px 20px}
h1{font-size:28px;margin:0 0 8px}.muted{color:#93a4bf;font-size:14px}
section{background:#161d29;border:1px solid #253147;border-radius:12px;padding:18px;margin:16px 0}
h2{font-size:18px;margin:0 0 10px}label{display:block;margin:8px 0 4px;color:#9fb0c9;font-size:13px}
input{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #2d3a51;background:#0f1520;color:#e8edf7}
button{margin:8px 8px 0 0;padding:9px 14px;border:0;border-radius:8px;background:#3b82f6;color:#fff;cursor:pointer}
button.secondary{background:#334155}pre{white-space:pre-wrap;word-break:break-word;color:#b9c7dc;font-size:13px}
a{color:#7dd3fc}
</style></head><body><main>
<h1>终末地</h1><p class="muted">Key、状态、配置与只读数据。写绑定/订阅请回聊天命令。<a href="/">返回面板</a></p>
<section><h2>API Key</h2><div id="key" class="muted">加载中…</div>
<label>新 Key（不会回显）</label><input id="keyInput" type="password" autocomplete="off">
<button onclick="saveKey()">保存</button><button class="secondary" onclick="deleteKey()">删除</button>
<button class="secondary" onclick="testKey()">测试连接</button></section>
<section><h2>状态</h2><pre id="status">加载中…</pre></section>
<section><h2>配置</h2><pre id="config">加载中…</pre>
<button onclick="saveConfig()">保存配置</button></section>
<section><h2>绑定 / 订阅</h2><pre id="data">加载中…</pre></section>
<script>
async function api(path, opts){const r=await fetch(path,Object.assign({headers:{'Content-Type':'application/json'}},opts));const t=await r.text();let j;try{j=JSON.parse(t)}catch(e){j=t}if(!r.ok)throw new Error(r.status+' '+JSON.stringify(j));return j}
async function load(){try{const [k,s,b,sub]=await Promise.all([api('/api/endfield/key'),api('/api/endfield/status'),api('/api/endfield/bindings'),api('/api/endfield/subscriptions')]);document.getElementById('key').textContent=JSON.stringify(k);document.getElementById('status').textContent=JSON.stringify(s,null,2);document.getElementById('data').textContent=JSON.stringify({bindings:b,subscriptions:sub},null,2);document.getElementById('config').textContent=JSON.stringify(s.config,null,2)}catch(e){document.getElementById('status').textContent='请先登录主面板：'+e.message}}
async function saveKey(){const api_key=document.getElementById('keyInput').value.trim();if(!api_key)return;try{await api('/api/endfield/key',{method:'POST',body:JSON.stringify({api_key})});document.getElementById('keyInput').value='';load()}catch(e){alert(e.message)}}
async function deleteKey(){if(!confirm('删除面板 Key？'))return;try{await api('/api/endfield/key',{method:'DELETE'});load()}catch(e){alert(e.message)}}
async function testKey(){try{const r=await api('/api/endfield/key/test',{method:'POST',body:'{}'});alert(JSON.stringify(r))}catch(e){alert(e.message)}}
async function saveConfig(){try{const cfg=JSON.parse(document.getElementById('config').textContent);await api('/api/endfield/config',{method:'POST',body:JSON.stringify(cfg)});load()}catch(e){alert(e.message)}}
load();
</script></main></body></html>"""
