"""Local, read-only status checks and a serialized NoneBot restart."""
import asyncio
import logging
import os
import secrets
import shutil
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
# SnowLuma WebUI 的地址。容器部署常把端口绑到内网 IP（如 192.0.2.226:5099）而不是
# 回环地址，写死 127.0.0.1 会探测失败、面板误报「未运行」。
# 用 SNOWLUMA_URL 指定；不指定时依次试这几个候选地址。
SNOWLUMA_URL = os.environ.get('SNOWLUMA_URL', '')
SNOWLUMA_URLS = [u for u in [
    SNOWLUMA_URL,
    'http://127.0.0.1:5099',
    'http://host.docker.internal:5099',
] if u]
SNOWLUMA_PORT = 5099
# Linux 部署时托管 bot.py 的 systemd 单元；换了单元名用环境变量覆盖
SERVICE_NAME = os.environ.get('QQBOT_SERVICE', 'qqbot.service')
log = logging.getLogger(__name__)


def snowluma_candidates():
    """探测用的候选地址：显式配置优先，否则从本机网卡推导出 http://<内网IP>:5099。"""
    if SNOWLUMA_URL:
        return [SNOWLUMA_URL]
    seen, result = set(), []
    for url in SNOWLUMA_URLS:
        if url not in seen:
            seen.add(url); result.append(url)
    for address in local_addresses():
        url = f'http://{address}:{SNOWLUMA_PORT}'
        if url not in seen:
            seen.add(url); result.append(url)
    return result


def local_addresses():
    """本机非回环 IPv4 地址（不起网络请求；拿不到就返回空）。"""
    addresses = []
    try:
        import socket
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = info[4][0]
            if address and not address.startswith('127.') and address not in addresses:
                addresses.append(address)
    except OSError:
        return []
    return addresses


class Monitor:
    def __init__(self, password_file=PASSWORD_FILE, transport=None):
        self.password_file = password_file
        self.transport = transport
        self.token = None
        self.snowluma_base = None
        self.retry_login_after = 0.0
        self.login_lock = asyncio.Lock()

    def client(self):
        return httpx.AsyncClient(timeout=3, trust_env=False, transport=self.transport)

    async def snowluma_url(self, client):
        """返回能连上的 SnowLuma 基地址；一个都连不上时抛 ConnectError。

        找到后记住，后续探测不再逐个试，省掉每次 3 秒的超时等待。
        """
        if self.snowluma_base:
            return self.snowluma_base
        last = None
        for base in snowluma_candidates():
            try:
                await client.get(base + '/')
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException) as exc:
                # 候选地址要能容忍各种失败：拒绝连接、被中途断开（host.docker.internal
                # 就是这种）、超时 —— 只要还有一个能通，就不该整体判失败。
                last = exc
                continue
            self.snowluma_base = base
            return base
        if last is not None:
            raise last
        raise httpx.ConnectError('no snowluma candidate', request=None)

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
                base = await self.snowluma_url(client)
                async with self.login_lock:
                    if not self.token:
                        try: password = self.password_file.read_text(encoding='utf-8').strip()
                        except OSError: password = ''
                        if not password:
                            response = await client.get(base + '/')
                            online = response.status_code < 500
                            return {'state':'online' if online else 'unknown', 'injected':None, 'connected':None,
                                    'detail':'正在运行；未配置 tools/snowluma-webui-pass.txt，看不到注入状态' if online else 'WebUI 无响应',
                                    'base':base, 'reason':'no-password' if online else 'unreachable'}
                        if time.monotonic() < self.retry_login_after:
                            response = await client.get(base + '/')
                            online = response.status_code < 500
                            return {'state':'online' if online else 'unknown', 'injected':None, 'connected':None,
                                    'detail':'登录失败，稍后重试' if online else 'WebUI 无响应', 'base':base,
                                    'reason':'login-failed'}
                        response = await client.post(base + '/api/login', json={'password':password})
                        response.raise_for_status()
                        data = response.json()
                        if not isinstance(data, dict):
                            return {'state':'online', 'injected':None, 'connected':None, 'base':base, 'reason':'login-failed'}
                        self.token = data.get('token')
                        if not isinstance(self.token, str) or not self.token:
                            self.token = None
                            self.retry_login_after = time.monotonic() + 60
                            return {'state':'online', 'injected':None, 'connected':None, 'base':base, 'reason':'login-failed',
                                    'detail':'WebUI 登录未返回令牌，检查 tools/snowluma-webui-pass.txt'}
                response = await client.get(base + '/api/processes', headers={'Authorization':'Bearer '+self.token})
                if response.status_code in (401, 403):
                    self.token = None
                    return {'state':'online', 'injected':None, 'connected':None, 'base':base, 'reason':'login-failed',
                            'detail':'WebUI 令牌已失效，检查 tools/snowluma-webui-pass.txt'}
                response.raise_for_status()
                data = response.json()
                processes = data.get('list') if isinstance(data, dict) else None
                if not isinstance(processes, list):
                    return {'state':'online', 'injected':None, 'connected':None, 'base':base, 'reason':'unexpected'}
                found = [p for p in processes if isinstance(p, dict) and p.get('injected') and str(p.get('uin') or '') == TARGET_UIN]
                unknown = any(isinstance(p, dict) and p.get('injected') and str(p.get('uin') or '') in ('', '0') for p in processes)
                return {'state':'online', 'injected':True if found else None if unknown else False,
                        'connected':any(p.get('connected') for p in found) if found else None,
                        'base':base, 'reason':'ok'}
        except httpx.HTTPStatusError:
            self.token = None
            self.retry_login_after = time.monotonic() + 60
            return {'state':'online', 'injected':None, 'connected':None}
        except httpx.ConnectError:
            self.token = None
            self.snowluma_base = None
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


class LinuxSystemdRestart:
    """systemd 托管时的重启：`systemctl restart`，用主 PID 变化判断新进程就绪。

    面板进程需要能调用 systemctl（root，或在 sudoers 里放行这条命令）；
    否则 `available` 会返回 False，按钮显示「不支持自动重启」而不是点了没反应。
    """
    def __init__(self, monitor, unit=SERVICE_NAME, runner=None):
        self.monitor = monitor
        self.unit = unit
        self.runner = runner or subprocess.run
        self.expected_token = None
        self.previous_pid = None
        self.previous_state = None

    @property
    def available(self):
        if sys.platform.startswith('win'): return False
        if shutil.which('systemctl') is None: return False
        try:
            result = self.runner(['systemctl', 'cat', self.unit], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def main_pid(self):
        try:
            result = self.runner(['systemctl', 'show', self.unit, '-p', 'MainPID'], capture_output=True, timeout=10, text=True)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode: return None
        text = (result.stdout or '').strip()
        if not text.startswith('MainPID='): return None
        try: return int(text.split('=', 1)[1])
        except ValueError: return None

    def stop_and_start(self):
        if not self.available: raise RuntimeError('当前环境不支持自动重启')
        try:
            result = self.runner(['systemctl', 'restart', self.unit], capture_output=True, timeout=60, text=True)
        except subprocess.TimeoutExpired:
            raise RuntimeError('重启超时，请检查 systemctl 状态')
        except OSError as exc:
            raise RuntimeError(f'无法调用 systemctl：{exc}')
        if result.returncode:
            detail = ((result.stderr or result.stdout or '').strip().splitlines() or [''])[0]
            raise RuntimeError(f'systemctl restart 失败{("：" + detail) if detail else ""}')

    async def __call__(self):
        self.expected_token = None
        previous = await self.monitor.bot_health()
        self.previous_state = previous.get('state')
        self.previous_pid = previous.get('pid') if previous.get('state') == 'ready' else None
        if self.previous_pid is None:
            self.previous_pid = self.main_pid()
        await asyncio.to_thread(self.stop_and_start)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            health = await self.monitor.bot_health()
            pid = health.get('pid') or self.main_pid()
            if health.get('state') == 'ready' and (self.previous_pid is None or (pid and pid != self.previous_pid)):
                return
            await asyncio.sleep(2)
        raise RuntimeError('机器人尚未就绪，请查看日志；按钮将在启动完成后恢复')


class Restart:
    """按平台挑实现：Windows 走 restarter 脚本，Linux 走 systemd。"""

    def __init__(self, monitor, unit=SERVICE_NAME):
        self.monitor = monitor
        self.unit = unit
        self.backends = [WindowsRestart(monitor), LinuxSystemdRestart(monitor, unit)]
        self.active = next((b for b in self.backends if b.available), None)
        self.expected_token = None
        self.previous_pid = None
        self.previous_state = None

    @property
    def available(self):
        return self.active is not None

    def __getattr__(self, name):
        active = self.__dict__.get('active')
        if active is not None and hasattr(active, name):
            return getattr(active, name)
        raise AttributeError(name)

    async def __call__(self):
        if self.active is None: raise RuntimeError('当前环境不支持自动重启')
        await self.active()
        self.expected_token = self.active.expected_token
        self.previous_pid = self.active.previous_pid
        self.previous_state = self.active.previous_state


class WindowsRestart:
    """Windows 上的重启：调 restarter 脚本停进程，再带一次性 token 起新进程。"""

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
