"""Local-network administrator console for bot data."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
import appearance_panel
import app_version
import bilibili_panel
import bot_updater
import bot_identity
import dashboard_onboarding
import meme_logic
import meme_catalog
import memory_logic as memory
import model_chat
import model_pref_panel
import group_topics_logic as topics
import weather_service as weather
import epic_free_config as epic_config
import external_services
import game_event_panel
import embedding_panel
import disaster_config as disaster_config_mod
import disaster_render as disaster_render_mod
import render_settings
import disaster_store as disaster_store_mod
import log_viewer
import master_settings
import parser_credentials
import parser_settings
import persona_settings
import plugin_control
import status_collect as status_live
import status_cluster as status_cluster_mod
import status_config as status_config_mod
import status_grafana as status_grafana_mod
import status_logic as status_logic_mod
import status_llm as status_llm_mod
import status_store as status_store_mod
import system_info
import superuser_settings
import theme_panel
import xhs_login
from bot_monitor import Monitor, Restart, RestartController, WindowsRestart  # noqa: F401 - 测试仍按旧名引用

ROOT = Path(__file__).resolve().parent
AUTH_FILE = ROOT / 'data' / 'dashboard-auth.json'
_GROUP_ID = re.compile(r'^[0-9]{1,20}$')
attempts = {}
log = logging.getLogger(__name__)


def _group_id(value, *, empty_message='群号无效'):
    text = '' if value is None else str(value).strip()
    if not text:
        raise HTTPException(400, empty_message)
    if not _GROUP_ID.fullmatch(text):
        raise HTTPException(400, '群号无效')
    if not any(char != '0' for char in text):
        raise HTTPException(400, '群号无效')
    return text


def onboarding_path() -> Path:
    """First-run guide state lives beside the auth file for test isolation."""
    return dashboard_onboarding.path_for(AUTH_FILE)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await xhs_login.close()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
meme_library = meme_logic.Library()
monitor = Monitor()
restart_worker = Restart(monitor)
restart_controller = RestartController(restart_worker)
updater = bot_updater.BotUpdater()

INITIAL_PASSWORD_FILE = 'dashboard-initial-password'
"""首次初始化时写入一次性明文密码的文件名（与认证文件同目录）。"""


def _write_auth_file(path, password, must_change):
    """原子写入认证文件，权限固定为 0600。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump({'salt':salt.hex(),'hash':digest,'must_change':must_change}, f)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def set_password(password, must_change=False):
    if not 12 <= len(password) <= 256: raise ValueError('密码长度须为 12 到 256 位')
    _write_auth_file(AUTH_FILE, password, must_change)


def initialize_password(password=None, *, path=None):
    """首次部署时创建认证文件，返回一次性明文密码。

    已有认证文件时拒绝覆盖：密码只能在面板内修改，避免误操作把现有管理员锁在门外。
    未指定密码时随机生成，并把明文写入同目录的 dashboard-initial-password（0600）。
    """
    target = Path(path) if path is not None else AUTH_FILE
    if target.exists():
        raise FileExistsError(f'认证文件已存在，拒绝覆盖：{target}')
    if password is None:
        password = secrets.token_urlsafe(24)
    elif not 12 <= len(password) <= 256:
        raise ValueError('密码长度须为 12 到 256 位')
    _write_auth_file(target, password, True)
    initial = target.parent / INITIAL_PASSWORD_FILE
    fd = os.open(initial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f: f.write(password + '\n')
    os.chmod(initial, 0o600)
    return password

def password_matches(password, saved):
    got = hashlib.scrypt(password.encode(),salt=bytes.fromhex(saved['salt']),n=16384,r=8,p=1).hex()
    return hmac.compare_digest(got,saved['hash'])


def must_change_pending():
    """认证文件是否仍要求首次设置密码；读不到时按「已完成」处理。"""
    try:
        saved = json.loads(AUTH_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return bool(saved.get('must_change', False)) if isinstance(saved, dict) else False

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


@app.get('/api/memes')
def get_memes(req: Request):
    authorized(req)
    categories = meme_library.categories()
    for category in categories:
        category['images'] = meme_library.images(category['id'])
    return {'categories': categories, 'rules': meme_library.rules()}


@app.post('/api/memes/category')
async def save_meme_category(req: Request):
    mutate(req)
    body = await req.json()
    try:
        return {'id': meme_library.put_category(body.get('name', ''), body.get('description', ''), body.get('id'))}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post('/api/memes/category/delete')
async def delete_meme_category(req: Request):
    mutate(req)
    body = await req.json()
    try:
        meme_library.delete_category(body.get('id', ''))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {'ok': True}


@app.post('/api/memes/import')
async def import_memes(req: Request):
    mutate(req)
    try:
        return await asyncio.to_thread(meme_catalog.import_tree, meme_library)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/memes/auto-tag')
async def auto_tag_memes(req: Request):
    mutate(req)
    try:
        body = await req.json()
    except Exception as exc:
        raise HTTPException(400, '请求体不是合法 JSON') from exc
    category = str((body or {}).get('category') or '') if isinstance(body, dict) else ''
    try:
        return await asyncio.to_thread(meme_catalog.auto_tag_library, meme_library, category or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/memes/metadata')
async def update_meme_metadata(req: Request):
    mutate(req)
    try:
        body = await req.json()
    except Exception as exc:
        raise HTTPException(400, '请求体不是合法 JSON') from exc
    if not isinstance(body, dict):
        raise HTTPException(400, '请求体必须是对象')
    try:
        return meme_library.set_metadata(body.get('id', ''), tags=body.get('tags'), weight=body.get('weight'))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/memes/image')
async def upload_meme_image(req: Request, category: str, tags: str | None = None, weight: float | None = None):
    mutate(req)
    content = bytearray()
    async for chunk in req.stream():
        content.extend(chunk)
        if len(content) > meme_logic.MAX_IMAGE_BYTES:
            raise HTTPException(413, '图片大小须在 5 MB 以内')
    payload = bytes(content)
    try:
        def save_upload():
            mime = meme_logic.image_mime(payload)
            category_name = next((item['name'] for item in meme_library.categories() if item['id'] == category), '')
            final_tags = meme_catalog.auto_tags(category_name, '', payload) if tags is None else tags
            final_weight = meme_catalog.auto_weight(category_name) if weight is None else weight
            return meme_library.add_image(category, payload, tags=final_tags, weight=final_weight, mime=mime)

        image_id, created = await asyncio.to_thread(save_upload)
        return {'id': image_id, 'created': created}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get('/api/memes/image/{image_id}')
def get_meme_image(req: Request, image_id: str):
    authorized(req)
    try:
        content, mime = meme_library.image_info(image_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return Response(content, media_type=mime, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@app.post('/api/memes/image/delete')
async def delete_meme_image(req: Request):
    mutate(req)
    body = await req.json()
    try:
        meme_library.delete_image(body.get('id', ''))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {'ok': True}


@app.post('/api/memes/rules')
async def save_meme_rules(req: Request):
    mutate(req)
    body = await req.json()
    try:
        return meme_library.set_rules(body.get('enabled'), body.get('disabled_scopes'))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

@app.get('/', response_class=HTMLResponse)
def index():
    html = (ROOT / 'dashboard.html').read_text(encoding='utf-8')
    return html.replace('{{APP_VERSION}}', app_version.VERSION)

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
    """页面加载时问一句「我还登录着吗」，省得每次刷新都重新输密码。

    首次设置期间也要能拿到 must_change，否则刷新后会停在登录页而进不了设置页。
    """
    token = authorized(req, allow_change=True)
    return {'csrf': token, 'must_change': must_change_pending()}

@app.post('/api/setup-password')
async def setup_password(req: Request):
    """首次登录的一次性密码设置：只对已登录且仍待设置的管理员开放。"""
    mutate(req, allow_change=True)
    try:
        saved = json.loads(AUTH_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise HTTPException(503, '无法读取管理员配置')
    if not isinstance(saved, dict) or not saved.get('must_change', False):
        raise HTTPException(409, '首次设置已完成，请使用修改密码功能')
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(400, '请求内容无效')
    password = body.get('password')
    confirm = body.get('confirm')
    if not isinstance(password, str) or not isinstance(confirm, str) or not 12 <= len(password) <= 256:
        raise HTTPException(400, '密码长度须为 12 到 256 位')
    if not hmac.compare_digest(password.encode('utf-8'), confirm.encode('utf-8')):
        raise HTTPException(400, '两次输入的密码不一致')
    if password_matches(password, saved):
        raise HTTPException(400, '新密码不能与原密码相同')
    set_password(password, must_change=False)
    try:
        dashboard_onboarding.mark_pending(onboarding_path())
    except OSError:
        log.warning('无法写入首次设置引导状态', exc_info=True)
    sessions.clear()
    save_sessions(sessions)
    (AUTH_FILE.parent / INITIAL_PASSWORD_FILE).unlink(missing_ok=True)
    response = JSONResponse({'ok': True})
    response.delete_cookie('admin_session', path='/')
    return response


@app.get('/api/onboarding')
def onboarding_state(req: Request):
    authorized(req)
    return dashboard_onboarding.load(onboarding_path())


@app.post('/api/onboarding')
async def set_onboarding_state(req: Request):
    mutate(req)
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(400, '请求内容无效')
    if body.get('basics_done') is True:
        try:
            return dashboard_onboarding.complete(onboarding_path())
        except OSError:
            raise HTTPException(503, '引导状态无法保存')
    try:
        return dashboard_onboarding.set_step(onboarding_path(), body.get('basics_step'))
    except ValueError:
        raise HTTPException(400, '引导步骤无效')
    except OSError:
        raise HTTPException(503, '引导状态无法保存')


@app.post('/api/password')
async def change_password(req: Request):
    """首次设置完成后的常规改密；设置期间不提供这条路径。"""
    mutate(req, allow_change=True)
    if must_change_pending():
        raise HTTPException(409, '请先完成首次密码设置')
    body = await req.json()
    password = body.get('password')
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise HTTPException(400, '密码长度须为 12 到 256 位')
    saved = json.loads(AUTH_FILE.read_text(encoding='utf-8'))
    if password_matches(password, saved): raise HTTPException(400, '新密码不能与原密码相同')
    set_password(password)
    sessions.clear()
    save_sessions(sessions)       # 改密码一律让所有设备重新登录
    (AUTH_FILE.parent / INITIAL_PASSWORD_FILE).unlink(missing_ok=True)
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
    return {'users':users,'groups':groups,'weather':{'host':cfg.get('host',''),'configured':bool(cfg.get('key'))},'pushes':weather.list_pushes(),'epic':epic_config.read()}

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

def update_error(exc: bot_updater.UpdateError):
    """把 Git 错误变成稳定、可展示的 HTTP 状态。"""
    if exc.code in {'LOCAL_CHANGES', 'DIVERGED', 'NO_UPSTREAM'}:
        raise HTTPException(409, str(exc))
    if exc.code == 'TIMEOUT':
        raise HTTPException(504, str(exc))
    raise HTTPException(503, str(exc))

def update_view(result):
    return {
        'version': app_version.VERSION,
        'restart_available': restart_worker.available,
        **result,
    }

@app.get('/api/update')
def update_status(req: Request):
    authorized(req)
    return update_view(updater.status())

@app.post('/api/update')
async def update_bot(req: Request):
    mutate(req)
    body = await req.json()
    if not isinstance(body, dict) or body.get('action') not in {'check', 'apply'}:
        raise HTTPException(400, '更新动作无效')
    if body['action'] == 'check':
        try:
            result = await asyncio.to_thread(updater.check)
        except bot_updater.UpdateError as exc:
            update_error(exc)
        return update_view(result)
    if not restart_worker.available:
        raise HTTPException(503, '当前环境不支持自动重启，已阻止更新')
    if restart_controller.running:
        raise HTTPException(409, '机器人正在重启，请等待启动完成')
    try:
        result = await asyncio.to_thread(updater.apply)
    except bot_updater.UpdateError as exc:
        update_error(exc)
    if not result.get('updated'):
        return update_view(result)
    if not await restart_controller.start():
        raise HTTPException(409, '代码已更新，但机器人正在重启；请等待启动完成后手动检查')
    result['restart_started'] = True
    warnings = []
    if result.get('requirements_changed'):
        warnings.append('requirements.lock 已变化，请确认依赖已更新后再使用新版本')
    if result.get('migration_changed'):
        warnings.append('数据库迁移脚本已变化，请确认迁移已完成')
    if warnings:
        result['warning'] = '；'.join(warnings)
    return JSONResponse(status_code=202, content=update_view(result))

@app.get('/api/plugins')
def plugin_settings(req: Request):
    authorized(req)
    return plugin_control.view()

@app.post('/api/plugins')
async def set_plugin_settings(req: Request):
    mutate(req)
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(400, '请求体必须是对象')
    before = plugin_control.load()
    try:
        saved = plugin_control.save(body.get('disabled'), body.get('onboarded'))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError:
        raise HTTPException(503, '插件设置无法保存')
    gsuid_before = 'gsuid' not in before['disabled']
    gsuid_after = 'gsuid' not in saved['disabled']
    if gsuid_before != gsuid_after:
        service = await asyncio.to_thread(
            external_services.apply_plugin_change, 'gsuid', gsuid_after
        )
        if service:
            saved['service'] = service
            if service.get('warning'):
                saved['warning'] = service['warning']
    return saved

@app.get('/api/user/{uid}')
def user(uid: str, req: Request):
    authorized(req)
    if not uid.isdigit(): raise HTTPException(400)
    return memory.describe_user(memory.DEFAULT_PATH, uid)

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

@app.get('/api/bot-name')
def bot_name_settings(req: Request):
    authorized(req)
    return bot_identity.public()

@app.post('/api/bot-name')
async def change_bot_name(req: Request):
    mutate(req)
    body = await req.json()
    if not isinstance(body, dict): raise HTTPException(400, '请求体必须是对象')
    try:
        if body.get('reset'):
            return bot_identity.reset()
        return bot_identity.save(body.get('name'))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError:
        raise HTTPException(503, '机器人名称无法保存')

@app.get('/api/model-pref')
def model_pref_config(req: Request):
    authorized(req)
    try:
        return model_pref_panel.view()
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, '模型设置无法读取')

@app.post('/api/model-pref')
async def set_model_pref_config(req: Request):
    mutate(req)
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(400, '请求体必须是对象')
    if 'primary' not in body or 'secondary' not in body:
        raise HTTPException(400, '必须同时提供 primary 和 secondary')
    try:
        return model_pref_panel.save_models(body.get('primary'), body.get('secondary'))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    except OSError:
        raise HTTPException(503, '模型设置无法保存')

@app.post('/api/model-pref/provider')
async def set_model_provider(req: Request):
    mutate(req)
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(400, '请求体必须是对象')
    try:
        return model_pref_panel.save_provider(body.get('base_url'), body.get('api_key'))
    except model_pref_panel.Unpublished:
        raise HTTPException(409, '模型配置尚未发布：请先核对 configs/models.yml 并重新发布摘要')
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    except OSError:
        raise HTTPException(503, '提供商配置无法保存')

@app.post('/api/model-pref/upstream')
async def list_model_upstream(req: Request):
    mutate(req)
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(400, '请求体必须是对象')
    try:
        return model_pref_panel.fetch_upstream(body.get('base_url'), body.get('api_key'))
    except model_pref_panel.UpstreamError as e:
        raise HTTPException(502, str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    except OSError:
        raise HTTPException(503, '无法读取模型提供商配置')

@app.post('/api/model/chat')
async def test_model_chat(req: Request):
    mutate(req)
    try:
        body = await req.json()
    except Exception as exc:
        raise HTTPException(400, '请求体不是合法 JSON') from exc
    if not isinstance(body, dict):
        raise HTTPException(400, '请求体必须是对象')
    try:
        return await model_chat.chat(body.get('messages'), model=body.get('model'))
    except model_chat.ChatError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log.warning('model chat test failed: %s', type(exc).__name__)
        raise HTTPException(502, '模型测试失败') from exc

@app.get('/api/persona')
def persona_config(req: Request):
    authorized(req)
    try:
        return persona_settings.public()
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, '人格模板配置无法读取')

@app.get('/api/persona/current')
def persona_current(req: Request):
    authorized(req)
    try:
        return persona_settings.current_view()
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, '当前人格正文无法读取')

@app.post('/api/persona/current')
async def set_persona_current(req: Request):
    mutate(req)
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(400, '请求体必须是对象')
    try:
        # Only the body changes: models.yml and the active template stay as-is.
        return persona_settings.save_current(body.get('content'))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    except OSError:
        raise HTTPException(503, '当前人格正文无法保存')

@app.post('/api/persona')
async def set_persona_config(req: Request):
    mutate(req)
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(400, '请求体必须是对象')
    try:
        # ``preset`` is the durable flow: copy a tracked preset into
        # templates/current.jinja2, then point models.yml at ``current``.
        # ``template`` keeps the old direct-preset behavior for API callers.
        if 'preset' in body:
            return persona_settings.load_preset(body.get('preset'), body.get('scope', 'active'))
        return persona_settings.save(body.get('template'), body.get('scope', 'active'))
    except persona_settings.Unpublished as e:
        raise HTTPException(409, str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    except OSError:
        raise HTTPException(503, '人格模板配置无法保存')

@app.get('/api/parser')
def parser_config(req: Request):
    authorized(req)
    try: return parser_settings.public()
    except (OSError, ValueError, KeyError): raise HTTPException(503, '解析器配置无法读取')

@app.post('/api/parser')
async def set_parser_config(req: Request):
    mutate(req)
    try:
        payload = await req.json()
    except (ValueError, TypeError):
        raise HTTPException(400, '请求格式无效')
    if not isinstance(payload, dict):
        raise HTTPException(400, '请求格式无效')
    login_id = payload.pop('xhs_login_id', None)
    if login_id is not None:
        if not xhs_login.valid_session_id(login_id):
            raise HTTPException(400, '扫码会话无效')
        platforms = payload.get('platforms')
        if platforms is None:
            platforms = {}
            payload['platforms'] = platforms
        if not isinstance(platforms, dict):
            raise HTTPException(400, '解析平台格式无效')
        xhs_changes = platforms.get('xhs')
        if xhs_changes is not None and not isinstance(xhs_changes, dict):
            raise HTTPException(400, '小红书平台格式无效')
        if isinstance(xhs_changes, dict) and xhs_changes.get('cookies'):
            raise HTTPException(400, '扫码保存时不要同时填写小红书 Cookie')
        cleared = payload.get('clear_secrets')
        if isinstance(cleared, list) and 'xhs.cookies' in cleared:
            raise HTTPException(400, '扫码保存时不要同时清除小红书 Cookie')
        admin_session = req.cookies.get('admin_session', '')
        try:
            claimed = await xhs_login.claim(admin_session, login_id)
        except xhs_login.XhsLoginError as e:
            raise HTTPException(e.status, e.detail)
        xhs_changes = platforms.get('xhs')
        if xhs_changes is None:
            xhs_changes = {}
            platforms['xhs'] = xhs_changes
        xhs_changes['cookies'] = claimed['cookies']
        try:
            saved = parser_settings.save(payload)
        except Exception as exc:
            try:
                await xhs_login.settle(admin_session, login_id, claimed['claim_id'], False)
            except xhs_login.XhsLoginError:
                pass
            if isinstance(exc, FileExistsError):
                raise HTTPException(409, str(exc))
            if isinstance(exc, (ValueError, TypeError)):
                raise HTTPException(400, str(exc))
            if isinstance(exc, (OSError, sqlite3.Error)):
                raise HTTPException(503, '解析器配置无法保存，请稍后重试')
            raise
        try:
            await xhs_login.settle(admin_session, login_id, claimed['claim_id'], True)
        except xhs_login.XhsLoginError:
            saved = dict(saved)
            saved['qr_cleanup_pending'] = True
        return saved
    try:
        return parser_settings.save(payload)
    except FileExistsError as e: raise HTTPException(409, str(e))
    except (ValueError, TypeError) as e: raise HTTPException(400, str(e))
    except (OSError, sqlite3.Error): raise HTTPException(503, '解析器配置无法保存，请稍后重试')

@app.post('/api/parser/bilibili/cookies')
async def import_bilibili_cookies(req: Request):
    mutate(req)
    try:
        body = await req.json()
    except (ValueError, TypeError):
        raise HTTPException(400, '请求格式无效')
    raw = body.get('cookies') if isinstance(body, dict) else None
    if not isinstance(raw, str):
        raise HTTPException(400, '请提供 Cookie 字符串或 JSON')
    try:
        cookies = parser_credentials.parse_cookie_input(raw)
        rows = parser_credentials.parse_bilibili_cookie_rows(raw)
        return parser_credentials.save_bilibili_cookies(cookies, rows=rows)
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    except OSError:
        raise HTTPException(503, 'B 站 Cookie 无法保存，请稍后重试')


@app.post('/api/parser/cookies')
async def import_parser_cookies(req: Request):
    mutate(req)
    try:
        body = await req.json()
    except (ValueError, TypeError):
        raise HTTPException(400, '请求格式无效')
    if not isinstance(body, dict):
        raise HTTPException(400, '请求格式无效')
    platform = body.get('platform')
    raw = body.get('cookies')
    if not isinstance(platform, str) or not isinstance(raw, str):
        raise HTTPException(400, '请提供平台和 Cookie 内容')
    try:
        return parser_credentials.save_platform_cookies(platform, raw)
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    except OSError:
        raise HTTPException(503, 'Cookie 文件无法写入，请稍后重试')


@app.post('/api/parser/bilibili/qr')
async def bilibili_qr(req: Request):
    mutate(req)
    try:
        png = await parser_credentials.create_bilibili_qr()
    except ImportError as e:
        raise HTTPException(503, f'缺少 B 站登录依赖：{e}')
    except Exception as e:
        raise HTTPException(502, f'生成 B 站二维码失败：{e}')
    return Response(content=png, media_type='image/png', headers={'Cache-Control': 'no-store'})


@app.post('/api/parser/bilibili/qr/status')
async def bilibili_qr_status(req: Request):
    mutate(req)
    try:
        return await parser_credentials.poll_bilibili_qr()
    except ImportError as e:
        raise HTTPException(503, f'缺少 B 站登录依赖：{e}')
    except Exception as e:
        return {'status': 'error', 'message': f'读取二维码状态失败：{e}'}


async def _xhs_login_body(req: Request) -> dict:
    try:
        body = await req.json()
    except (ValueError, TypeError):
        raise HTTPException(400, '请求格式无效')
    if not isinstance(body, dict):
        raise HTTPException(400, '请求格式无效')
    return body


@app.post('/api/parser/xhs-login/start')
async def xhs_login_start(req: Request):
    mutate(req)
    try:
        payload = await xhs_login.start(req.cookies.get('admin_session', ''))
    except xhs_login.XhsLoginError as e:
        raise HTTPException(e.status, e.detail)
    return JSONResponse(payload, headers={'Cache-Control': 'no-store'})


@app.post('/api/parser/xhs-login/status')
async def xhs_login_status(req: Request):
    mutate(req)
    body = await _xhs_login_body(req)
    session_id = body.get('id')
    if not isinstance(session_id, str):
        raise HTTPException(400, '扫码会话无效')
    try:
        payload = await xhs_login.status(req.cookies.get('admin_session', ''), session_id)
    except xhs_login.XhsLoginError as e:
        raise HTTPException(e.status, e.detail)
    return JSONResponse(payload, headers={'Cache-Control': 'no-store'})


@app.post('/api/parser/xhs-login/cancel')
async def xhs_login_cancel(req: Request):
    mutate(req)
    body = await _xhs_login_body(req)
    session_id = body.get('id')
    if not isinstance(session_id, str):
        raise HTTPException(400, '扫码会话无效')
    try:
        payload = await xhs_login.cancel(req.cookies.get('admin_session', ''), session_id)
    except xhs_login.XhsLoginError as e:
        raise HTTPException(e.status, e.detail)
    return JSONResponse(payload, headers={'Cache-Control': 'no-store'})


@app.get('/api/parser-events')
def parser_events(req: Request, limit: int = 100):
    authorized(req)
    try: return parser_settings.events(limit=max(1, min(limit, 500)))
    except (OSError, ValueError): raise HTTPException(503, '解析日志无法读取')


@app.get('/api/logs')
def runtime_logs(req: Request, name: str = 'nonebot.log', tail: int = 200):
    authorized(req)
    try:
        return log_viewer.read_log(name, tail)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except OSError:
        raise HTTPException(503, '运行日志无法读取')

@app.post('/api/parser-events/clear')
async def clear_parser_events(req: Request):
    mutate(req)
    try: return parser_settings.clear_events()
    except (OSError, ValueError): raise HTTPException(503, '解析日志无法清理')

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
    gid = _group_id(gid)
    return {'enabled':topics.enabled(topics.DEFAULT_PATH,gid),'topics':topics.top(topics.DEFAULT_PATH,gid,limit=20), 'speakers':topics.speaker_top(topics.DEFAULT_PATH,gid,limit=20)}

@app.post('/api/topic')
async def topic(req: Request):
    mutate(req)
    try:
        b = await req.json()
    except Exception as exc:
        raise HTTPException(400, '请求体不是合法 JSON') from exc
    if not isinstance(b, dict):
        raise HTTPException(400, '请求体必须是对象')
    gid = _group_id(b.get('group', ''), empty_message='请先选择或输入群号')
    action = b.get('action')
    if action not in ('开', '关', '清除'):
        raise HTTPException(400, '操作无效')
    if action == '清除': topics.clear(topics.DEFAULT_PATH,gid)
    else: topics.set_enabled(topics.DEFAULT_PATH,gid,action=='开')
    return {'ok':True, 'group':gid}

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


@app.post('/api/epic')
async def set_epic(req: Request):
    mutate(req); body = await req.json()
    try:
        action = str(body.get('action', ''))
        if action == 'add':
            epic_config.add_target(body.get('kind', ''), body.get('id', ''))
        elif action == 'delete':
            epic_config.remove_target(body.get('kind', ''), body.get('id', ''))
        elif action == 'save':
            patch = {key: body[key] for key in ('country', 'locale', 'poll_minutes', 'push_enabled', 'include_upcoming', 'card_enabled') if key in body}
            epic_config.update(patch)
        elif action == 'test':
            epic_config.request_test()
        else:
            raise ValueError('未知操作')
    except ValueError as error:
        raise HTTPException(400, str(error))
    return {'ok': True}


# ---------------------------------------------------------------- 灾害预警
# 配置只落 data/disaster_warning.json；机器人进程与面板进程共用这一份。
# 令牌写进文件后不再回显，面板只拿到 token_configured / 到期日。

def disaster_config_path():
    return ROOT / 'data' / 'disaster_warning.json'


def disaster_store_path():
    return ROOT / 'data' / 'disaster_warning.sqlite3'


@app.get('/api/disaster')
def get_disaster(req: Request):
    authorized(req)
    try:
        return disaster_config_mod.public_view(disaster_config_mod.load_config(disaster_config_path()))
    except (OSError, ValueError):
        raise HTTPException(503, '灾害预警配置无法读取')


@app.post('/api/disaster')
async def set_disaster(req: Request):
    mutate(req)
    body = await req.json()
    path = disaster_config_path()
    config = disaster_config_mod.load_config(path)
    try:
        updated = disaster_config_mod.apply_patch(config, body if isinstance(body, dict) else {})
        disaster_config_mod.save_config(updated, path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError:
        raise HTTPException(503, '灾害预警配置无法保存')
    return {'ok': True, 'config': disaster_config_mod.public_view(updated)}


@app.get('/api/disaster/history')
def get_disaster_history(req: Request, limit: int = 20, days: int = 7):
    authorized(req)
    store = disaster_store_mod.EventStore(disaster_store_path())
    try:
        return {
            'stats': store.stats(days=max(1, min(days, 90))),
            'events': store.recent(limit=max(1, min(limit, 50))),
        }
    finally:
        store.close()


@app.get('/api/disaster/render-status')
async def disaster_render_status(req: Request):
    authorized(req)
    config = disaster_config_mod.load_config(disaster_config_path())
    render = config.get('render') or {}
    if not render.get('enabled'):
        return {'configured': False, 'ok': False, 'message': '远程渲染已关闭'}
    url = render_settings.load()['url']
    try:
        status = await disaster_render_mod.health(url, timeout=3)
    except Exception as exc:
        return {'configured': True, 'ok': False, 'message': str(exc)}
    ready = status.get('ready') is True
    return {
        'configured': True,
        'ok': bool(status.get('ok', True)) and ready,
        'ready': ready,
        'status': status,
    }

@app.get('/api/renderer')
async def renderer_settings(req: Request):
    authorized(req)
    config = render_settings.load()
    try:
        status = await disaster_render_mod.health(config['url'], timeout=3)
    except Exception:
        status = {'ok': False, 'ready': False, 'error': '渲染服务未就绪'}
    return {**config, 'status': status}


@app.post('/api/renderer')
async def save_renderer_settings(req: Request):
    mutate(req)
    body = await req.json()
    try:
        return render_settings.save(body.get('url', ''))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------- 服务器状态
# 面板与机器人是两个进程，只共享文件：状态 JSON 里有 QQ 侧快照，SQLite 里有
# 资源采样。这里只读，不做任何采集调度——那属于机器人进程。

_status_cache = {}


def status_paths():
    base = ROOT / 'data'
    return base / 'statuscard.json', base / 'statuscard-state.json', base / 'statuscard.sqlite3'


def status_stores():
    _, state_path, db_path = status_paths()
    if 'samples' not in _status_cache:
        _status_cache['samples'] = status_store_mod.SampleStore(db_path)
    # 状态 JSON 由机器人进程写入。面板是独立进程，若缓存 StateStore，就会一直
    # 读启动那一刻的计数器/LLM 直方图；每次概览请求重新读一份很便宜。
    _status_cache['state'] = status_store_mod.StateStore(state_path)
    return _status_cache['samples'], _status_cache['state']


def downsample(points, limit=240):
    """点数太多就等间隔抽稀；抽稀后仍保留首尾两个点。"""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    picked = [points[int(index * step)] for index in range(limit)]
    if picked[-1] is not points[-1]:
        picked[-1] = points[-1]
    return picked


@app.get('/api/status/overview')
def status_overview(req: Request, days: int = 1):
    authorized(req)
    config = status_config_mod.load_config(status_paths()[0])
    if not config['enabled']:
        return {'enabled': False, 'config': status_config_mod.public_view(config)}
    try:
        days = max(1, min(30, int(days)))
    except (TypeError, ValueError):
        days = 1
    samples, state = status_stores()
    now = time.time()
    since = int(now - days * 86400)
    quality = status_llm_mod.LLMQuality(state)
    llm = quality.summary(days=days)
    rate = status_logic_mod.message_rate(
        state.section('counters_history').get('samples') or [], now=now
    )
    try:
        live = status_live.panel_snapshot()
    except Exception:
        live = {}
    try:
        temperature = status_grafana_mod.fetch_temp(
            config.get('grafana') or {}, root=ROOT
        )
    except Exception:
        temperature = None
    if temperature and isinstance(live.get('cpu'), dict):
        live['cpu']['temp'] = temperature
    points = [
        {'t': row['ts'], 'c': row['cpu'], 'm': row['mem']}
        for row in samples.list_resources(since)
        if row.get('cpu') is not None
    ]
    events = list(reversed(samples.list_events(since, limit=400)))[:20]
    panel = state.section('panel')
    report = samples.latest_report_run()
    return {
        'enabled': True,
        'now': now,
        'range_days': days,
        'live': live,
        'host': str(state.get('host') or ''),
        'system': system_info.snapshot(),
        'bot': panel,
        'counters': state.section('counters'),
        'llm': llm,
        'rate': rate,
        'network': {
            'month_up': state.section('net').get('month_up'),
            'month_down': state.section('net').get('month_down'),
            'interface': state.section('net').get('interface'),
            'last_sample': state.section('net').get('last_sample'),
        },
        'history': {'days': days, 'points': downsample(points)},
        'events': events,
        'report': report,
        'stores': {key: value for key, value in samples.stats().items() if key != 'counts'},
        'counts': samples.stats().get('counts', {}),
        'config': status_config_mod.public_view(config),
        'grafana': status_grafana_mod.panel_view(config.get('grafana') or {}, root=ROOT),
    }


def status_probe_config(req: Request):
    mutate(req)
    config = status_config_mod.load_config(status_paths()[0])
    if not config['enabled']:
        raise HTTPException(409, '服务器状态已关闭')
    return config


@app.post('/api/status/grafana/datasources')
async def list_status_grafana_datasources(req: Request):
    """用面板填写的地址和 Key 列出数据源，不保存。"""
    config = status_probe_config(req)
    body = await req.json()
    incoming = (body or {}).get('grafana') if isinstance(body, dict) else None
    settings = dict(config.get('grafana') or {})
    token = ''
    if isinstance(incoming, dict):
        settings.update({key: value for key, value in incoming.items() if key != 'token'})
        token = str(incoming.get('token') or '').strip()
    settings['enabled'] = True
    items = status_grafana_mod.fetch_datasources(settings, token=token, root=ROOT)
    if items is None:
        return {'ok': False, 'error': status_grafana_mod.last_error() or '读取失败'}
    return {'ok': True, 'items': items}


@app.post('/api/status/config')
async def set_status_config(req: Request):
    mutate(req)
    body = await req.json()
    path = status_paths()[0]
    config = status_config_mod.load_config(path)
    payload = body if isinstance(body, dict) else {}
    grafana_patch = payload.get('grafana')
    if isinstance(grafana_patch, dict):
        token = grafana_patch.get('token')
        if isinstance(token, str) and token.strip():
            status_grafana_mod.save_secret(token.strip(), root=ROOT)
        payload = {
            **payload,
            'grafana': {key: value for key, value in grafana_patch.items() if key != 'token'},
        }
    updated = status_config_mod.apply_patch(config, payload)
    status_config_mod.save_config(updated, path)
    return {
        'ok': True,
        'config': status_config_mod.public_view(updated),
        'grafana': status_grafana_mod.panel_view(updated.get('grafana') or {}, root=ROOT),
    }


@app.post('/api/status/grafana/test')
async def test_status_grafana(req: Request):
    """用面板当前填写的参数试查一次，不保存。"""
    config = status_probe_config(req)
    body = await req.json()
    incoming = (body or {}).get('grafana') if isinstance(body, dict) else None
    patch = {}
    token = ''
    if isinstance(incoming, dict):
        patch = {key: value for key, value in incoming.items() if key != 'token'}
        token = str(incoming.get('token') or '').strip()
    merged = status_config_mod.apply_patch(config, {'grafana': patch}) if patch else config
    settings = dict(merged.get('grafana') or {})
    settings['enabled'] = True
    token = token or status_grafana_mod.load_secret(ROOT)
    if not token:
        return {'ok': False, 'error': '未配置 Grafana API Key'}
    result = status_grafana_mod.fetch_temp(settings, token=token, root=ROOT, use_cache=False)
    if not result:
        return {'ok': False, 'error': status_grafana_mod.last_error() or '查询没有返回数据'}
    return {'ok': True, **result}


@app.post('/api/status/cluster/test')
async def test_status_cluster(req: Request):
    """用面板当前填写的集群参数试查一次，不保存。

    集群查询永远绕开缓存：面板按键就是用来验证「刚填的 bucket/窗口对不对」的。
    任意异常（bucket 非法、Grafana 报错、超时）都收成 `{ok:false}`，让前端提示
    原文，而不是抛 500。
    """
    config = status_probe_config(req)
    body = await req.json()
    payload = body if isinstance(body, dict) else {}
    patch = {}
    token = ''
    cluster_patch = payload.get('cluster')
    if isinstance(cluster_patch, dict):
        patch['cluster'] = dict(cluster_patch)
    grafana_patch = payload.get('grafana')
    if isinstance(grafana_patch, dict):
        patch['grafana'] = {key: value for key, value in grafana_patch.items() if key != 'token'}
        token = str(grafana_patch.get('token') or '').strip()
    merged = status_config_mod.apply_patch(config, patch) if patch else config
    cluster_settings = dict(merged.get('cluster') or {})
    grafana_settings = dict(merged.get('grafana') or {})
    # 总开关打开时，允许先验证尚未启用的 Grafana 和集群配置。
    cluster_settings['enabled'] = True
    grafana_settings['enabled'] = True
    token = token or status_grafana_mod.load_secret(ROOT)
    if not token:
        return {'ok': False, 'error': '未配置 Grafana API Key'}
    try:
        view = status_cluster_mod.fetch(
            cluster_settings, grafana_settings, root=ROOT, token=token, use_cache=False
        )
    except Exception as exc:
        return {'ok': False, 'error': str(exc) or type(exc).__name__}
    items = view if isinstance(view, dict) else {}
    return {
        'ok': True,
        'title': items.get('title') or '',
        'summary': items.get('summary') or {},
        'nodes': [
            {
                'name': str(item.get('name') or ''),
                'cpu_text': str(item.get('cpu_text') or ''),
                'memory_text': str(item.get('memory_text') or ''),
            }
            for item in items.get('nodes') or []
            if isinstance(item, dict)
        ],
        'guests': [
            {
                'vmid': item.get('vmid'),
                'name': str(item.get('name') or ''),
                'status_label': str(item.get('status_label') or ''),
            }
            for item in items.get('guests') or []
            if isinstance(item, dict)
        ],
        'storages': [
            {
                'name': str(item.get('name') or ''),
                'percent_text': str(item.get('percent_text') or ''),
            }
            for item in items.get('storages') or []
            if isinstance(item, dict)
        ],
    }


# ---------------------------------------------------------------- 超级管理员
# 名单落在 data/superusers.json，机器人进程启动时加载、之后每 30 秒按 mtime
# 热应用。面板与机器人是两个进程，只有这个文件是它们之间的唯一契约。

@app.get('/api/superusers')
def get_superusers(req: Request):
    authorized(req)
    env_users = superuser_settings.read_env_superusers(ROOT / '.env')
    users, source = superuser_settings.resolve(env_users)
    return {
        'users': users,
        'source': source,
        'env_users': env_users,
        'max': superuser_settings.MAX_USERS,
    }


@app.post('/api/superusers')
async def set_superusers(req: Request):
    mutate(req)
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(400, '请求格式无效')
    if body.get('reset'):
        superuser_settings.reset()
        env_users = superuser_settings.read_env_superusers(ROOT / '.env')
        users, source = superuser_settings.resolve(env_users)
        return {'ok': True, 'users': users, 'source': source, 'reset': True}
    try:
        saved = superuser_settings.save(body.get('users'))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, '管理员名单保存失败') from exc
    return {'ok': True, 'users': saved, 'source': 'file'}


app.include_router(game_event_panel.router(authorized, mutate))

appearance_panel.register(app, authorized=authorized, mutate=mutate, root=ROOT)
theme_panel.register(app, authorized=authorized, mutate=mutate, root=ROOT)
bilibili_panel.register(app, authorized=authorized, mutate=mutate, root=ROOT)
embedding_panel.register(app, authorized=authorized, mutate=mutate, root=ROOT)


if __name__ == '__main__':
    # 首次部署入口：只创建认证文件，不加载 NoneBot、不启动 uvicorn。
    import argparse
    import sys

    parser = argparse.ArgumentParser(description='初始化本地管理面板密码')
    parser.add_argument('--init', action='store_true', help='创建 data/dashboard-auth.json 并打印一次性密码')
    parser.add_argument('--password', help='指定初始密码（12 到 256 位），省略则随机生成')
    args = parser.parse_args()
    if not args.init:
        parser.error('需要 --init 才会创建认证文件')
    try:
        initial_password = initialize_password(args.password)
    except FileExistsError as exc:
        print(exc, file=sys.stderr)
        print('密码已有配置；如需重置，请先备份并移走该文件，或在面板内修改密码。', file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    print('已生成面板初始密码（只显示这一次）：')
    print(f'  {initial_password}')
    print('首次登录后必须修改密码。')
    print('启动面板：')
    print('  uvicorn dashboard:app --host 127.0.0.1 --port 8092 --no-access-log')
