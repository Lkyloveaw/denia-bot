"""Local-network administrator console for bot data."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import memory_logic as memory
import group_topics_logic as topics
import weather_service as weather
import master_settings
import parser_settings
from bot_monitor import Monitor, Restart, RestartController, WindowsRestart  # noqa: F401 - 测试仍按旧名引用

ROOT = Path(__file__).resolve().parent
AUTH_FILE = ROOT / 'data' / 'dashboard-auth.json'
attempts = {}
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
monitor = Monitor()
restart_worker = Restart(monitor)
restart_controller = RestartController(restart_worker)

def set_password(password, must_change=False):
    if not 12 <= len(password) <= 256: raise ValueError('密码长度须为 12 到 256 位')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = AUTH_FILE.with_name(AUTH_FILE.name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump({'salt':salt.hex(),'hash':digest,'must_change':must_change}, f)
    os.replace(temp, AUTH_FILE)

def password_matches(password, saved):
    got = hashlib.scrypt(password.encode(),salt=bytes.fromhex(saved['salt']),n=16384,r=8,p=1).hex()
    return hmac.compare_digest(got,saved['hash'])

SESSION_MAX_AGE = 8 * 3600
"""会话有效期（秒）。"""


def session_store_path() -> Path:
    return ROOT / 'data' / 'dashboard-sessions.json'


def load_sessions() -> dict:
    """从磁盘恢复会话。

    原来会话只存在内存里，面板一重启全员掉登录 —— 光是配置变更重启就把人踢出去。
    现在落盘，重启后仍然有效。
    """
    try:
        raw = json.loads(session_store_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    now = time.time()
    if not isinstance(raw, dict):
        return {}
    return {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float)) and v > now}


def save_sessions(data: dict) -> None:
    """原子写入会话文件；失败不影响登录本身。"""
    path = session_store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(temp, path)
    except OSError:
        pass


sessions = load_sessions()


def prune_sessions() -> dict:
    """清掉过期会话并返回有效会话。"""
    now = time.time()
    alive = {k: v for k, v in sessions.items() if v > now}
    if len(alive) != len(sessions):
        sessions.clear()
        sessions.update(alive)
        save_sessions(sessions)
    return alive


def issue_session() -> str:
    """新建一个会话并落盘，返回 token。"""
    token = secrets.token_urlsafe(32)
    prune_sessions()
    sessions[token] = time.time() + SESSION_MAX_AGE
    save_sessions(sessions)
    return token


def drop_session(token: str) -> None:
    sessions.pop(token, None)
    save_sessions(sessions)


def authorized(req, allow_change=False):
    token = req.cookies.get('admin_session', '')
    expiry = sessions.get(token, 0)
    if not expiry or expiry < time.time():
        sessions.pop(token, None)
        raise HTTPException(401, '请登录')
    try:
        must_change = json.loads(AUTH_FILE.read_text(encoding='utf-8')).get('must_change', False)
    except (OSError, ValueError):
        raise HTTPException(503, '无法读取管理员配置')
    if must_change and not allow_change: raise HTTPException(403, '请先修改初始密码')
    return token

def mutate(req, allow_change=False):
    token = authorized(req, allow_change=allow_change)
    origin = req.headers.get('origin','')
    if req.headers.get('x-csrf-token') != token or not origin or urlsplit(origin).netloc != req.headers.get('host'):
        raise HTTPException(403, '请求来源无效')

@app.get('/', response_class=HTMLResponse)
def index():
    return (ROOT / 'dashboard.html').read_text(encoding='utf-8')

@app.post('/api/login')
async def login(req: Request):
    ip = req.client.host
    recent = [t for t in attempts.get(ip,[]) if t > time.time()-300]
    attempts[ip] = recent
    if len(recent) >= 8: raise HTTPException(429, '尝试次数过多，请稍后再试')
    body = await req.json()
    try:
        saved = json.loads(AUTH_FILE.read_text(encoding='utf-8'))
        valid = password_matches(str(body.get('password',''))[:256], saved)
    except (OSError, ValueError, KeyError): valid = False
    if not valid:
        recent.append(time.time())
        raise HTTPException(401, '密码错误或后台尚未配置')
    attempts.pop(ip,None)
    token = issue_session()
    response = JSONResponse({'csrf':token, 'must_change':bool(saved.get('must_change', False))})
    # 显式给 Path=/，任何子路径都带上；SameSite=Lax 允许从地址栏直接打开时保留会话
    response.set_cookie('admin_session', token, httponly=True, samesite='lax', max_age=SESSION_MAX_AGE, path='/')
    return response

@app.get('/api/session')
def session_info(req: Request):
    """页面加载时问一句「我还登录着吗」，省得每次刷新都重新输密码。"""
    token = authorized(req)
    try:
        must_change = json.loads(AUTH_FILE.read_text(encoding='utf-8')).get('must_change', False)
    except (OSError, ValueError):
        must_change = False
    return {'csrf': token, 'must_change': must_change}

@app.post('/api/password')
async def change_password(req: Request):
    mutate(req, allow_change=True)
    body = await req.json()
    password = body.get('password')
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise HTTPException(400, '密码长度须为 12 到 256 位')
    saved = json.loads(AUTH_FILE.read_text(encoding='utf-8'))
    if password_matches(password, saved): raise HTTPException(400, '新密码不能与原密码相同')
    set_password(password)
    sessions.clear()
    save_sessions(sessions)       # 改密码一律让所有设备重新登录
    (AUTH_FILE.parent / 'dashboard-initial-password').unlink(missing_ok=True)
    response = JSONResponse({'ok':True})
    response.delete_cookie('admin_session', path='/')
    return response

@app.post('/api/logout')
async def logout(req: Request):
    token = req.cookies.get('admin_session', '')
    if token: drop_session(token)
    response = JSONResponse({'ok':True})
    response.delete_cookie('admin_session', path='/')
    return response

@app.get('/api/state')
def state(req: Request):
    authorized(req)
    with memory._connect(memory.DEFAULT_PATH) as c:
        users = [r[0] for r in c.execute('SELECT user_id FROM memories UNION SELECT user_id FROM memory_candidates UNION SELECT user_id FROM memory_preferences ORDER BY user_id')]
    with topics._connect(topics.DEFAULT_PATH) as c:
        groups = [dict(r) for r in c.execute('SELECT group_id,enabled FROM topic_groups ORDER BY group_id')]
    cfg = weather.config()
    return {'users':users,'groups':groups,'weather':{'host':cfg.get('host',''),'configured':bool(cfg.get('key'))},'pushes':weather.list_pushes()}

@app.get('/api/bot-status')
async def bot_status(req: Request):
    authorized(req)
    status = await monitor.status()
    if restart_controller.running and restart_controller.error:
        health = await monitor.bot_health()
        new_token = restart_worker.expected_token and health.get('start_token') == restart_worker.expected_token
        new_pid = health.get('pid') and (
            (restart_worker.previous_pid and health.get('pid') != restart_worker.previous_pid)
            or (restart_worker.previous_pid is None and restart_worker.previous_state in ('offline', 'unknown'))
        )
        if health.get('state') == 'ready' and (new_token or new_pid):
            restart_controller.observe_ready()
    status['restart'] = {'running':restart_controller.running, 'error':restart_controller.error, 'available':restart_worker.available}
    try:
        status['cache'] = parser_settings.cache_stats()
    except (OSError, ValueError):
        status['cache'] = {'files':0, 'bytes':0, 'oldest':'', 'newest':''}
    return status

@app.post('/api/bot-restart', status_code=202)
async def bot_restart(req: Request):
    mutate(req)
    if not restart_worker.available: raise HTTPException(503, '当前环境不支持自动重启')
    if not await restart_controller.start(): raise HTTPException(409, '机器人正在重启，请等待启动完成')
    return {'ok':True}

@app.get('/api/user/{uid}')
def user(uid: str, req: Request):
    authorized(req)
    if not uid.isdigit(): raise HTTPException(400)
    return {'memories':memory.list_memories(memory.DEFAULT_PATH,uid),'candidates':memory.list_candidates(memory.DEFAULT_PATH,uid),'enabled':memory.is_enabled(memory.DEFAULT_PATH,uid)}

@app.get('/api/master-aware')
def master_aware_settings(req: Request):
    authorized(req)
    try: return master_settings.load()
    except (OSError, ValueError): raise HTTPException(503, '主人识别配置无法读取')

@app.post('/api/master-aware')
async def change_master_aware(req: Request):
    mutate(req)
    body = await req.json()
    try: return master_settings.save(body)
    except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/parser')
def parser_config(req: Request):
    authorized(req)
    try: return parser_settings.public()
    except (OSError, ValueError, KeyError): raise HTTPException(503, '解析器配置无法读取')

@app.post('/api/parser')
async def set_parser_config(req: Request):
    mutate(req)
    try: return parser_settings.save(await req.json())
    except FileExistsError as e: raise HTTPException(409, str(e))
    except (ValueError, TypeError) as e: raise HTTPException(400, str(e))
    except (OSError, sqlite3.Error): raise HTTPException(503, '解析器配置无法保存，请稍后重试')

@app.get('/api/parser-events')
def parser_events(req: Request, limit: int = 100):
    authorized(req)
    try: return parser_settings.events(limit=max(1, min(limit, 500)))
    except (OSError, ValueError): raise HTTPException(503, '解析日志无法读取')

@app.post('/api/parser-events/clear')
async def clear_parser_events(req: Request):
    mutate(req)
    try: return parser_settings.clear_events()
    except (OSError, ValueError): raise HTTPException(503, '解析日志无法清理')

from endfield_panel import register as register_endfield

register_endfield(app, authorized=authorized, mutate=mutate, root=ROOT)


@app.get('/api/token')
def token_usage(req: Request, days: int = 7):
    authorized(req)
    try: return parser_settings.token_summary(days=days)
    except (OSError, ValueError): raise HTTPException(503, 'Token 统计无法读取')

@app.post('/api/token/clear')
async def clear_token_usage(req: Request):
    mutate(req)
    try: return parser_settings.clear_tokens()
    except (OSError, ValueError): raise HTTPException(503, 'Token 统计无法清理')

@app.get('/api/group/{gid}')
def group(gid: str, req: Request):
    authorized(req)
    if not gid.isdigit(): raise HTTPException(400)
    return {'enabled':topics.enabled(topics.DEFAULT_PATH,gid),'topics':topics.top(topics.DEFAULT_PATH,gid,limit=20), 'speakers':topics.speaker_top(topics.DEFAULT_PATH,gid,limit=20)}

@app.post('/api/topic')
async def topic(req: Request):
    mutate(req); b=await req.json(); gid=str(b.get('group','')); action=b.get('action')
    if not gid.isdigit() or int(gid)<=0 or action not in ('开','关','清除'): raise HTTPException(400)
    if action=='清除': topics.clear(topics.DEFAULT_PATH,gid)
    else: topics.set_enabled(topics.DEFAULT_PATH,gid,action=='开')
    return {'ok':True}

@app.post('/api/memory')
async def change_memory(req: Request):
    mutate(req); b=await req.json(); uid=str(b.get('user','')); action=b.get('action'); target=str(b.get('id',''))
    if not uid.isdigit(): raise HTTPException(400)
    if action in ('开启','关闭'): memory.set_enabled(memory.DEFAULT_PATH,uid,action=='开启'); ok=True
    elif action=='忘记': ok=memory.forget(memory.DEFAULT_PATH,uid,target)
    elif action=='纠正': ok=memory.correct(memory.DEFAULT_PATH,uid,target,str(b.get('text','')))
    elif action in ('accept','replace','discard'): ok=memory.resolve_candidate(memory.DEFAULT_PATH,uid,target,action)
    else: raise HTTPException(400)
    if not ok: raise HTTPException(400, '操作失败，请检查内容或编号')
    return {'ok':True}

@app.post('/api/weather')
async def set_weather(req: Request):
    mutate(req); b=await req.json()
    try: weather.save_config(str(b.get('host','')),str(b.get('key','')))
    except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True}

@app.post('/api/push')
async def push(req: Request):
    mutate(req); b=await req.json()
    try:
        if b.get('action')=='delete': weather.delete_push(int(b['id']))
        else: weather.add_push(weather.PATH,str(b.get('kind','')),str(b.get('target','')),str(b.get('city','')),str(b.get('clock','')))
    except (ValueError,KeyError): raise HTTPException(400,'推送配置无效')
    return {'ok':True}
