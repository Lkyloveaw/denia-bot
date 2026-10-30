"""Local, read-only status checks and a serialized NoneBot restart."""
import asyncio
import logging
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BOT_DIR = ROOT / 'nonebot'
PASSWORD_FILE = ROOT / 'tools' / 'snowluma-webui-pass.txt'
TARGET_UIN = '3892894069'
HEALTH_URL = 'http://127.0.0.1:8080/dashboard-health'
SNOWLUMA_URL = 'http://127.0.0.1:5099'
log = logging.getLogger(__name__)


class Monitor:
    def __init__(self, password_file=PASSWORD_FILE, transport=None):
        self.password_file = password_file
        self.transport = transport
        self.token = None
        self.retry_login_after = 0.0
        self.login_lock = asyncio.Lock()

    def client(self):
        return httpx.AsyncClient(timeout=3, trust_env=False, transport=self.transport)

    async def bot_health(self):
        try:
            async with self.client() as client:
                response = await client.get(HEALTH_URL)
                if response.status_code != 200:
                    return {'state':'unknown', 'connected':None}
                data = response.json()
                if not isinstance(data, dict): return {'state':'unknown', 'connected':None}
                if data.get('service') != 'qqbot-nonebot' or data.get('ready') is not True:
                    return {'state':'unknown', 'connected':None}
                return {'state':'ready', 'connected':bool(data.get('connected')), 'start_token':data.get('start_token'), 'pid':data.get('pid')}
        except httpx.ConnectError:
            return {'state':'offline', 'connected':None}
        except (httpx.RequestError, ValueError):
            return {'state':'unknown', 'connected':None}

    async def snowluma(self):
        try:
            async with self.client() as client:
                async with self.login_lock:
                    if not self.token:
                        try: password = self.password_file.read_text(encoding='utf-8').strip()
                        except OSError: password = ''
                        if not password or time.monotonic() < self.retry_login_after:
                            response = await client.get(SNOWLUMA_URL + '/')
                            return {'state':'online' if response.status_code < 500 else 'unknown', 'injected':None, 'connected':None}
                        response = await client.post(SNOWLUMA_URL + '/api/login', json={'password':password})
                        response.raise_for_status()
                        data = response.json()
                        if not isinstance(data, dict): return {'state':'online', 'injected':None, 'connected':None}
                        self.token = data.get('token')
                        if not isinstance(self.token, str) or not self.token:
                            self.token = None
                            self.retry_login_after = time.monotonic() + 60
                            return {'state':'online', 'injected':None, 'connected':None}
                response = await client.get(SNOWLUMA_URL + '/api/processes', headers={'Authorization':'Bearer '+self.token})
                if response.status_code in (401, 403):
                    self.token = None
                    return {'state':'online', 'injected':None, 'connected':None}
                response.raise_for_status()
                data = response.json()
                processes = data.get('list') if isinstance(data, dict) else None
                if not isinstance(processes, list): return {'state':'online', 'injected':None, 'connected':None}
                found = [p for p in processes if isinstance(p, dict) and p.get('injected') and str(p.get('uin') or '') == TARGET_UIN]
                unknown = any(isinstance(p, dict) and p.get('injected') and str(p.get('uin') or '') in ('', '0') for p in processes)
                return {'state':'online', 'injected':True if found else None if unknown else False, 'connected':any(p.get('connected') for p in found) if found else None}
        except httpx.HTTPStatusError:
            self.token = None
            self.retry_login_after = time.monotonic() + 60
            return {'state':'online', 'injected':None, 'connected':None}
        except httpx.ConnectError:
            self.token = None
            return {'state':'offline', 'injected':None, 'connected':None}
        except (httpx.RequestError, ValueError, TypeError):
            self.token = None
            return {'state':'unknown', 'injected':None, 'connected':None}

    async def rag(self):
        try:
            async with self.client() as client:
                response = await client.get('http://127.0.0.1:8091/health')
                return {'state':'online' if response.status_code == 200 else 'unknown'}
        except httpx.ConnectError:
            return {'state':'offline'}
        except httpx.RequestError:
            return {'state':'unknown'}

    async def status(self):
        bot, snowluma, rag = await asyncio.gather(self.bot_health(), self.snowluma(), self.rag())
        bot.pop('start_token', None)
        return {'bot':bot, 'snowluma':snowluma, 'rag':rag}


class RestartController:
    def __init__(self, worker):
        self.worker = worker
        self.running = False
        self.error = ''
        self.task = None
        self.lock = asyncio.Lock()

    async def start(self):
        async with self.lock:
            if self.running: return False
            self.running, self.error = True, ''
            self.task = asyncio.create_task(self._run())
            return True

    async def _run(self):
        try:
            await self.worker()
        except RuntimeError as exc:
            self.error = str(exc) or '重启失败，请检查后台日志'
        except Exception:
            log.exception('Restart failed')
            self.error = '重启失败，请查看管理后台日志'
        else:
            self.running = False

    def observe_ready(self):
        if self.error:
            self.running = False
            self.error = ''


class WindowsRestart:
    def __init__(self, monitor):
        self.monitor = monitor
        self.expected_token = None
        self.previous_pid = None
        self.previous_state = None

    @property
    def available(self):
        return sys.platform == 'win32' and (ROOT / 'tools' / 'restart-nonebot.ps1').is_file()

    def stop_and_start(self):
        if not self.available: raise RuntimeError('当前环境不支持自动重启')
        python = BOT_DIR / '.venv' / 'Scripts' / 'python.exe'
        if not python.is_file() or not (BOT_DIR / 'bot.py').is_file():
            raise RuntimeError('找不到机器人程序或 Python 环境')
        logdir = ROOT / 'logs'
        logdir.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW
        stop = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'tools' / 'restart-nonebot.ps1')], capture_output=True, timeout=35, creationflags=flags)
        if stop.returncode:
            raise RuntimeError('无法安全停止机器人，请检查机器人进程和端口')
        self.expected_token = secrets.token_urlsafe(24)
        env = dict(os.environ, QQBOT_START_TOKEN=self.expected_token, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        with (logdir / 'nonebot.out.log').open('ab') as out, (logdir / 'nonebot.err.log').open('ab') as err:
            subprocess.Popen([str(python), 'bot.py'], cwd=str(BOT_DIR), env=env, stdout=out, stderr=err, creationflags=flags | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)

    async def __call__(self):
        self.expected_token = None
        previous = await self.monitor.bot_health()
        self.previous_state = previous.get('state')
        self.previous_pid = previous.get('pid') if previous.get('state') == 'ready' else None
        await asyncio.to_thread(self.stop_and_start)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            health = await self.monitor.bot_health()
            if health.get('state') == 'ready' and health.get('start_token') == self.expected_token:
                return
            await asyncio.sleep(2)
        raise RuntimeError('机器人尚未就绪，请查看日志；按钮将在启动完成后恢复')
