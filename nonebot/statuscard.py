# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""服务器状态卡：采集、出图、告警、服务监测、日报/周报的接线层。

纯逻辑都在同级模块里，这个文件只做「把东西接起来」：

- `status_logic`  格式化、视图装配、阈值判定、报表聚合（无 I/O）
- `status_collect` psutil / 系统命令采集
- `status_store`  JSON 状态 + SQLite 采样库
- `status_image`  达妮娅主题 Pillow 渲染
- `status_monitor` 五类服务探测 + 判定
- `status_llm`    包一层 provider 记 LLM 质量

四个后台任务各管一段，互不阻塞：

1. **采样**（60 秒）—— 网络/磁盘速率、月流量、收发计数落盘
2. **告警**（60 秒）—— 资源阈值、掉线/恢复、LLM 连续失败
3. **监测**（60 秒）—— 服务可用性探测
4. **定时**（30 秒）—— 每日状态图、日报、周报，按标记补发

所有命令仅超级用户可用：状态卡会暴露主机名与进程名，只给主人。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from nonebot import get_bots, get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from muicebot.plugin import PluginMetadata

import status_collect as collect
import status_config as config_mod
import superuser_settings
import status_image as image
import status_logic as logic
import status_llm as llm
import status_monitor as monitor
import status_store as store

try:
    import token_logic
except ImportError:  # pragma: no cover - 没有 token 账本时报表少一段，不致命
    token_logic = None  # type: ignore[assignment]

__plugin_meta__ = PluginMetadata(
    name='statuscard',
    description='服务器状态卡与运维报表',
    usage='主人：查看状态 / 状态卡诊断 / 测试通知 / 立即检测 / 预览日报 / 预览周报',
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
CONFIG_PATH = DATA / 'statuscard.json'
STATE_PATH = DATA / 'statuscard-state.json'
DB_PATH = DATA / 'statuscard.sqlite3'
CACHE_DIR = DATA / 'statuscard'

COMMANDS = ('查看状态', '状态卡', '状态卡诊断', '测试通知', '立即检测', '预览日报', '预览周报')

#: 这些动作算「发了一条消息」。用于卡片上的「回复」计数。
SEND_ACTIONS = frozenset({
    'send_msg',
    'send_group_msg',
    'send_private_msg',
    'send_group_forward_msg',
    'send_private_forward_msg',
})

# 配置默认值与读写放在 `status_config` 里：面板进程也要读同一份文件，
# 从插件模块借函数会把 nonebot 的 matcher 一起 import 进去。

def load_config(path: Path | None = None) -> dict[str, Any]:
    """读配置（缺字段补默认）。保留这个包装是为了让接线层只认一个入口。"""
    return config_mod.load_config(path or CONFIG_PATH)


def save_config(config: dict[str, Any], path: Path | None = None) -> None:
    config_mod.save_config(config, path or CONFIG_PATH)


# ---------------------------------------------------------------------------
# 运行时
# ---------------------------------------------------------------------------


class Runtime:
    """把配置、存储、缓存和后台任务装在一起。

    面板进程不共享这个对象（它是另一个进程），所以面板读的是同一份 JSON 与
    SQLite 文件——这也是状态与采样必须落盘而不是放内存的主要原因。

    `lazy=True` 时不在构造阶段建库：插件是 import 时就实例化的，如果那时就
    创建 SQLite，单测 import 一下就会在真实 `data/` 里留下文件。真正的建库
    放到 `prepare()`（启动钩子里）。
    """

    def __init__(self, *, base: Path | None = None, lazy: bool = False):
        data_dir = Path(base) if base is not None else DATA
        self.data_dir = data_dir
        self.config_path = data_dir / 'statuscard.json'
        self.state_path = data_dir / 'statuscard-state.json'
        self.db_path = data_dir / 'statuscard.sqlite3'
        self.cache_dir = data_dir / 'statuscard'
        self.config = load_config(self.config_path)
        self.state = store.StateStore(self.state_path, log=logger)
        self.samples: store.SampleStore | None = None if lazy else store.SampleStore(self.db_path, log=logger)
        self.quality = llm.LLMQuality(self.state, log=logger)
        self.aggregator = logic.AlertAggregator(self._alert_conf().get('aggregate_seconds', 120))
        loaded = self.state.section('counters') or {}
        self.counters = {'recv': int(loaded.get('recv') or 0), 'sent': int(loaded.get('sent') or 0)}
        self.bot_cache: dict[str, Any] = {}
        self.avatar_cache: dict[str, tuple[float, bytes | None]] = {}
        self.website_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self.cooldowns: dict[str, float] = {}
        self.last_render_ms: float = 0.0
        self.last_push: dict[str, Any] = {}
        self.tasks: list[asyncio.Task] = []
        self.started_at = time.time()
        self._config_mtime = self._config_stamp()

    def prepare(self) -> None:
        """启动时调用：建目录、建库、载入计数。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.samples is None:
            self.samples = store.SampleStore(self.db_path, log=logger)
        self.reload()

    @property
    def sample_store(self) -> store.SampleStore:
        """采样库。没走 `prepare()` 就用到它是接线错误，这里显式说清楚。"""
        if self.samples is None:
            raise RuntimeError('采样库尚未初始化，请先调用 Runtime.prepare()')
        return self.samples

    # ---------------------------------------------------------------- 便捷取配置
    def _alert_conf(self) -> dict[str, Any]:
        section = self.config.get('alerts')
        return section if isinstance(section, dict) else {}

    def _display_conf(self) -> dict[str, Any]:
        section = self.config.get('display')
        return section if isinstance(section, dict) else {}

    def _monitor_conf(self) -> dict[str, Any]:
        section = self.config.get('monitors')
        return section if isinstance(section, dict) else {}

    def _report_conf(self) -> dict[str, Any]:
        section = self.config.get('report')
        return section if isinstance(section, dict) else {}

    def _config_stamp(self) -> float:
        try:
            return self.config_path.stat().st_mtime
        except OSError:
            return 0.0

    def maybe_reload(self) -> bool:
        """面板保存过配置就热加载。

        没有文件监听器，就用 mtime 在采样循环里顺便看一眼：面板改完最多一分钟
        生效，不用重启机器人。
        """
        stamp = self._config_stamp()
        if stamp <= self._config_mtime:
            return False
        self._config_mtime = stamp
        self.reload()
        logger.info('[status] 检测到配置变更，已热加载')
        return True

    def reload(self) -> None:
        self.config = load_config(self.config_path)
        self.aggregator = logic.AlertAggregator(self._alert_conf().get('aggregate_seconds', 120))
        self.counters = dict(self.state.section('counters') or {}) or {'recv': 0, 'sent': 0}
        self.counters.setdefault('recv', 0)
        self.counters.setdefault('sent', 0)

    # ---------------------------------------------------------------- 目标
    def targets(self, kind: str | None = None) -> list[dict[str, Any]]:
        """推送目标：模块自带优先，否则用告警的默认目标。

        上游有个「通知中心」按类型分群，本项目单机单群，去掉那一层反而少一个
        配错的地方。`kind` 保留成参数，留给以后要分群时用。
        """
        monitors = self._monitor_conf()
        alerts = self._alert_conf()
        raw = monitors.get('targets') if kind == 'monitor' and monitors.get('targets') else alerts.get('targets')
        return logic.parse_targets(raw)

    # ---------------------------------------------------------------- 计数
    def bump(self, key: str, amount: int = 1) -> None:
        self.counters[key] = int(self.counters.get(key) or 0) + amount

    def flush_counters(self) -> None:
        self.state.update_section('counters', **self.counters)
        self.state.save()

    # ---------------------------------------------------------------- 头像
    async def avatar(self, self_id: str) -> bytes | None:
        if not self_id.isdigit():
            return None
        ttl = int(self._display_conf().get('avatar_cache_seconds') or 86400)
        cached = self.avatar_cache.get(self_id)
        if cached and time.time() - cached[0] < ttl and cached[1]:
            return cached[1]
        path = self.cache_dir / f'avatar-{self_id}.png'
        try:
            if path.is_file() and time.time() - path.stat().st_mtime < ttl:
                data = path.read_bytes()
                self.avatar_cache[self_id] = (time.time(), data)
                return data
        except OSError:
            pass
        url = f'https://q1.qlogo.cn/g?b=qq&nk={self_id}&s=640'
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.content
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.avatar_cache[self_id] = (time.time(), data)
            return data
        except Exception as exc:  # noqa: BLE001 - 头像拿不到就用占位图
            logger.debug(f'[status] 头像下载失败：{exc}')
            self.avatar_cache[self_id] = (time.time(), None)
            return None


# ---------------------------------------------------------------------------
# OneBot 侧采集
# ---------------------------------------------------------------------------

_BOT_TTL = {'online': 30.0, 'friends': 300.0, 'groups': 300.0, 'login': 300.0, 'version': 300.0}


async def _call(bot: Bot, action: str, *, timeout: float = 5.0, **params: Any) -> Any:
    # 必须用 call_api：NoneBot 的 Bot.__getattr__ 会把任意未知属性当成 API 名，
    # 写成 call_action 会被当成 API 名转发，变成 call_api("call_action", ...)，报「位置参数多了」。
    return await asyncio.wait_for(bot.call_api(action, **params), timeout=timeout)


def _as_list(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get('data')
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return None


def _as_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get('data')
        return data if isinstance(data, dict) else payload
    return {}


def _persona_nickname() -> str:
    """人格设定里的名字，作为拿不到 QQ 昵称时的显示名。

    `get_login_info` 拿不到昵称时（超时、协议端差异），卡片上至少显示「达妮娅」
    而不是一行冷冰冰的「Bot」。
    """
    try:
        from muicebot.templates.loader import load_templates_config

        config = load_templates_config() or {}
        name = str(config.get('ai_nickname') or '').strip()
        return name[:24]
    except Exception:  # noqa: BLE001 - 读不到就用兜底
        return ''


def _pick_bot() -> Bot | None:
    bots = get_bots()
    return next(iter(bots.values()), None) if bots else None


async def collect_bot_info(runtime: Runtime) -> dict[str, Any]:
    """好友/群/群员/协议端/在线状态。每个字段独立缓存，一个慢调用不拖垮全卡。"""
    bot = _pick_bot()
    now = time.time()
    cache = runtime.bot_cache
    data: dict[str, Any] = {
        'nick': '', 'self_id': '', 'online': None, 'protocol': '', 'protocol_version': '',
        'friends': None, 'groups': None, 'members': None,
    }
    if bot is None:
        data['nick'] = '未连接'
        return data

    def fresh(key: str) -> bool:
        item = cache.get(key)
        return bool(item) and now - item[0] < _BOT_TTL.get(key, 300)

    tasks = {}

    async def fetch_login() -> None:
        login = _as_dict(await _call(bot, 'get_login_info'))
        data['self_id'] = str(login.get('user_id') or '')
        data['nick'] = str(login.get('nickname') or '')
        cache['login'] = (now, dict(data))

    async def fetch_version() -> None:
        version = _as_dict(await _call(bot, 'get_version_info'))
        data['protocol'] = str(version.get('app_name') or version.get('app_full_name') or 'OneBot')
        data['protocol_version'] = str(version.get('app_version') or version.get('version') or '')
        cache['version'] = (now, {'protocol': data['protocol'], 'protocol_version': data['protocol_version']})

    async def fetch_friends() -> None:
        friends = _as_list(await _call(bot, 'get_friend_list'))
        data['friends'] = len(friends) if friends is not None else None
        cache['friends'] = (now, data['friends'])

    async def fetch_groups() -> None:
        groups = _as_list(await _call(bot, 'get_group_list'))
        if groups is not None:
            data['groups'] = len(groups)
            data['members'] = sum(int(group.get('member_count') or 0) for group in groups)
            cache['groups'] = (now, (data['groups'], data['members']))

    async def fetch_online() -> None:
        status = _as_dict(await _call(bot, 'get_status'))
        online = bool(status.get('online')) if 'online' in status else True
        data['online'] = online
        cache['online'] = (now, online)

    if fresh('login') and isinstance(cache.get('login'), tuple):
        data.update(cache['login'][1] or {})
    if fresh('version') and isinstance(cache.get('version'), tuple):
        data.update(cache['version'][1] or {})
    if fresh('friends'):
        data['friends'] = cache['friends'][1]
    if fresh('groups'):
        groups_value, members_value = cache['groups'][1]
        data['groups'] = groups_value
        data['members'] = members_value
    if fresh('online'):
        data['online'] = cache['online'][1]

    jobs = []
    if not fresh('login'):
        jobs.append(fetch_login())
    if not fresh('version'):
        jobs.append(fetch_version())
    if not fresh('friends'):
        jobs.append(fetch_friends())
    if not fresh('groups'):
        jobs.append(fetch_groups())
    if not fresh('online'):
        jobs.append(fetch_online())
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)

    # 查询接口不可用时用本地信息兜底：昵称取人格配置，号码取收到过的事件里带的
    if not data['self_id']:
        data['self_id'] = str(runtime.state.get('self_id') or '')
    if not data['nick']:
        data['nick'] = str(cache.get('login', (0, {}))[1].get('nick') or '') or _persona_nickname() or 'Bot'
    if not data['protocol']:
        data['protocol'] = 'OneBot'
    data['sent'] = int(runtime.counters.get('sent') or 0)
    data['recv'] = int(runtime.counters.get('recv') or 0)
    data['sent_scope'] = '适配器发送计数'
    data['recv_scope'] = '消息事件计数'
    return data


# ---------------------------------------------------------------------------
# 快照与渲染
# ---------------------------------------------------------------------------


def _rates_from_state(runtime: Runtime, now: float) -> tuple[float, float, float, float]:
    """从状态里取上一次的计数，算出网络与磁盘速率。"""
    net_state = runtime.state.section('net')
    io_state = runtime.state.section('disk_io')
    interface = str(net_state.get('interface') or '')
    current_net = collect.net_counters(interface)
    current_io = collect.disk_io_counters()
    last_net = net_state.get('counter') if isinstance(net_state.get('counter'), dict) else None
    last_io = io_state.get('counter') if isinstance(io_state.get('counter'), dict) else None
    span = float(net_state.get('last_sample') or 0)
    elapsed = max(0.0, now - span) if span else 0.0
    up, down = collect.net_rates(last_net, current_net, elapsed)
    read, write = collect.disk_io_rates(last_io, current_io, elapsed)
    return up, down, read, write


async def probe_websites(runtime: Runtime, *, force: bool = False) -> list[dict[str, Any]]:
    display = runtime._display_conf()
    targets = display.get('website_targets') or []
    cache_seconds = float(display.get('website_cache_seconds') or 60)
    cached_at, cached = runtime.website_cache
    if not force and cached and time.time() - cached_at < cache_seconds:
        return cached

    items = monitor.normalize_items([
        {
            'type': 'http',
            'name': str(item.get('name') or '') if isinstance(item, dict) else '',
            'url': str(item.get('url') or '') if isinstance(item, dict) else str(item),
            'timeout_seconds': (item.get('timeout_seconds') if isinstance(item, dict) else 3) or 3,
        }
        for item in targets
    ])
    results = []
    for item in items:
        if item.get('invalid'):
            continue
        outcome = await monitor.probe(item)
        results.append({
            'name': item['name'],
            'status': outcome.get('detail', ''),
            'delay_ms': outcome.get('delay_ms', 0.0),
            'ok': bool(outcome.get('ok')),
        })
    runtime.website_cache = (time.time(), results)
    return results


async def build_snapshot(runtime: Runtime, *, now: float | None = None) -> dict[str, Any]:
    """采集一张卡需要的全部原始数字。"""
    now = time.time() if now is None else float(now)
    display = runtime._display_conf()
    disk_paths = display.get('disk_paths') or None
    top_n = int(display.get('process_top_n') or 8)
    if display.get('compact'):
        top_n = min(top_n, 5)

    up, down, read, write = _rates_from_state(runtime, now)

    cpu, memory, swap, disks, process, system = await asyncio.gather(
        asyncio.to_thread(collect.cpu_snapshot),
        asyncio.to_thread(collect.memory_snapshot),
        asyncio.to_thread(collect.swap_snapshot),
        asyncio.to_thread(collect.disk_snapshot, disk_paths),
        asyncio.to_thread(collect.process_snapshot, top_n, max_age=collect._PROC_CACHE_TTL),
        asyncio.to_thread(collect.system_rows),
        return_exceptions=True,
    )
    if isinstance(cpu, BaseException):
        cpu = {'percent': 0.0, 'cores': 0, 'threads': 0, 'brand': 'CPU', 'freq_mhz': 0.0}
    if isinstance(memory, BaseException):
        memory = {'total': 0, 'used': 0, 'percent': 0.0}
    if isinstance(swap, BaseException):
        swap = {'total': 0, 'used': 0, 'percent': 0.0}
    if isinstance(disks, BaseException):
        disks = []
    if isinstance(process, BaseException):
        process = {'total': None, 'running': None, 'top': []}
    if isinstance(system, BaseException):
        system = []

    bot = await collect_bot_info(runtime)
    if bot.get('self_id'):
        bot['avatar'] = await runtime.avatar(str(bot['self_id']))

    net_state = runtime.state.section('net')
    history = net_state.get('history') if isinstance(net_state.get('history'), list) else []
    month_label = logic.month_label(str(net_state.get('month') or datetime.now().strftime('%Y-%m')))
    counters = collect.net_counters(str(net_state.get('interface') or ''))

    websites = await probe_websites(runtime)
    return {
        'uptime': collect.uptime_seconds(now),
        'cpu': cpu,
        'memory': memory,
        'swap': swap,
        'disks': disks,
        'disk_io': {'read_rate': read, 'write_rate': write},
        'network': {
            'up_rate': up,
            'down_rate': down,
            'sent_total': counters.get('sent'),
            'recv_total': counters.get('recv'),
            'month_up': net_state.get('month_up'),
            'month_down': net_state.get('month_down'),
            'month_label': month_label,
            'interface': str(net_state.get('interface') or ''),
            'history': history,
        },
        'process': process,
        'system': system,
        'websites': websites,
        'bot': bot,
        'compact': bool(display.get('compact')),
        'footer': '本地渲染 · 数据不出本机',
    }


async def render_card(runtime: Runtime, *, force: bool = False) -> bytes:
    """出图。返回 PNG bytes；失败抛 `status_image.RenderError`。"""
    started = time.perf_counter()
    snapshot = await build_snapshot(runtime)
    view = logic.build_view(
        snapshot,
        redact=runtime._display_conf().get('redact') or [],
    )
    png = await asyncio.to_thread(
        image.render_status_card,
        view,
        width=int(runtime._display_conf().get('width') or 1170),
    )
    runtime.last_render_ms = (time.perf_counter() - started) * 1000
    return png


# ---------------------------------------------------------------------------
# 推送
# ---------------------------------------------------------------------------


async def push(runtime: Runtime, targets: list[dict[str, Any]], message: Any, *, label: str = '推送') -> bool:
    """按目标逐个发送，任意一个成功即算成功。

    只发一次：拿不到连接或目标非法才换下一个目标，绝不因为超时重发——
    QQ 侧的回执可能只是丢了，重发会让群里出现两条。
    """
    bot = _pick_bot()
    if bot is None:
        logger.warning(f'[status] {label}失败：没有已连接的 Bot')
        runtime.last_push = {'ok': False, 'detail': '没有已连接的 Bot', 'ts': time.time()}
        return False
    if not targets:
        logger.warning(f'[status] {label}失败：没有配置推送目标')
        runtime.last_push = {'ok': False, 'detail': '没有配置推送目标', 'ts': time.time()}
        return False

    detail = ''
    for target in targets:
        action = 'send_group_msg' if target['kind'] == 'group' else 'send_private_msg'
        params = {'group_id': target['id']} if target['kind'] == 'group' else {'user_id': target['id']}
        try:
            await asyncio.wait_for(bot.call_api(action, **params, message=message), timeout=20)
            runtime.last_push = {
                'ok': True,
                'detail': f'已发送到 {target["kind"]}:{target["id"]}',
                'ts': time.time(),
            }
            runtime.state.set('last_push', runtime.last_push)
            runtime.state.save()
            return True
        except Exception as exc:  # noqa: BLE001 - 单个目标失败换下一个
            detail = f'{target["kind"]}:{target["id"]} {type(exc).__name__}: {exc}'
            logger.warning(f'[status] {label}发送失败：{detail}')
    runtime.last_push = {'ok': False, 'detail': detail, 'ts': time.time()}
    runtime.state.set('last_push', runtime.last_push)
    runtime.state.save()
    return False


# ---------------------------------------------------------------------------
# 事件与告警
# ---------------------------------------------------------------------------


def record_event(runtime: Runtime, now: int, kind: str, title: str, detail: str = '') -> None:
    runtime.sample_store.add_event(now, kind, title, detail)


def _alert_text(name: str, value_text: str) -> str:
    return f'【异常】{name}\n当前：{value_text}'


def _recovery_text(name: str, duration: float) -> str:
    return f'【恢复】{name}\n持续：{logic.format_duration(duration)}'


async def evaluate_resources(runtime: Runtime, *, now: float) -> None:
    """资源阈值告警。每个指标一个规则，状态存在 JSON 里，重启不丢。"""
    alerts = runtime.state.section('alerts')
    rules = alerts.get('rules') if isinstance(alerts.get('rules'), dict) else {}
    conf = runtime._alert_conf()
    sustain = int(conf.get('sustain_samples') or 3)
    cooldown = float(conf.get('cooldown_seconds') or 1800)
    thresholds = {
        'cpu': float(conf.get('cpu') or 85),
        'ram': float(conf.get('memory') or 90),
        'swap': float(conf.get('swap') or 90),
        'disk': float(conf.get('disk') or 85),
    }
    labels = {'cpu': 'CPU 使用率', 'ram': '内存使用率', 'swap': 'SWAP 使用率', 'disk': '磁盘使用率'}

    cpu = await asyncio.to_thread(collect.cpu_snapshot)
    memory = await asyncio.to_thread(collect.memory_snapshot)
    swap = await asyncio.to_thread(collect.swap_snapshot)
    paths = runtime._display_conf().get('disk_paths') or collect.default_disk_paths()
    disks = await asyncio.to_thread(collect.disk_snapshot, paths)

    samples: list[tuple[str, str, float, float, str]] = [
        ('cpu', labels['cpu'], float(cpu.get('percent') or 0), thresholds['cpu'],
         f'{float(cpu.get("percent") or 0):.1f}%'),
        ('ram', labels['ram'], float(memory.get('percent') or 0), thresholds['ram'],
         f'{float(memory.get("percent") or 0):.1f}%（{logic.format_bytes(memory.get("used"))} / {logic.format_bytes(memory.get("total"))}）'),
        ('swap', labels['swap'], float(swap.get('percent') or 0), thresholds['swap'],
         f'{float(swap.get("percent") or 0):.1f}%'),
    ]
    for disk in disks:
        if disk.get('percent') is None:
            continue
        key = 'disk' if len(disks) == 1 else f'disk:{disk["path"]}'
        name = labels['disk'] if len(disks) == 1 else f'{labels["disk"]}（{disk["path"]}）'
        samples.append((key, name, float(disk['percent']), thresholds['disk'],
                        f'{float(disk["percent"]):.1f}%（{logic.format_bytes(disk.get("used"))} / {logic.format_bytes(disk.get("total"))}）'))

    try:
        load = collect.load_per_core()
        if load is not None:
            load1, per_core, cores = load
            samples.append((
                'load', '系统负载', per_core, float(conf.get('load_per_core') or 2.0),
                f'{load1:.2f}（{cores} 核，{per_core:.2f}/核）',
            ))
    except Exception:  # noqa: BLE001 - 没有 loadavg 的平台跳过
        pass

    notify = bool(conf.get('enabled'))
    for key, name, value, threshold, text in samples:
        decision = logic.evaluate_rule(
            rules.get(key),
            value,
            threshold,
            sustain_samples=sustain,
            cooldown_seconds=cooldown,
            now=now,
        )
        rules[key] = decision['state']
        if decision['alert']:
            record_event(runtime, int(now), 'resource', name, text)
            if notify:
                item = {'name': name, 'text': _alert_text(name, text)}
                if runtime.aggregator.add(item, now=now):
                    await push(runtime, runtime.targets('alert'), item['text'], label='资源告警')
        if decision['recovery']:
            runtime.sample_store.resolve_event(name, int(now))
            record_event(runtime, int(now), 'recovery', name, '已恢复')
            if notify and conf.get('notify_recovery', True):
                await push(runtime, runtime.targets('alert'),
                           _recovery_text(name, decision['duration']), label='恢复通知')

    runtime.state.set('alerts', {'rules': rules})
    runtime.state.save()

    # 聚合窗口里攒下的告警，到期合成一条
    pending = runtime.aggregator.take(now=now)
    if pending and notify:
        # 聚合器里存的已经是成品文本，这里不能再套一层 _alert_text，
        # 否则群里会出现「【异常】CPU…当前：【异常】CPU…当前：95%」。
        await push(runtime, runtime.targets('alert'),
                   '\n\n'.join(str(item.get('text') or '') for item in pending),
                   label='聚合告警')


async def check_offline(runtime: Runtime, *, now: float) -> None:
    """Bot 在线状态。

    判定以**连接**为准，查询只作否定证据：

    1. `get_bots()` 里没有 Bot —— 通道本身断了，连续两次确认离线
       （重连瞬间会有空档，一次就报会误伤）
    2. 有 Bot 且 `get_status` 明确回 `online=false` —— 确认离线
    3. 有 Bot 但状态查询失败 —— 在线。NapCat 重启期间 `get_status` 必然失败，
       但 QQ 侧并没掉线；把失败的探测当成掉线就是一条假警报

    查询失败的原因记进状态，`状态卡诊断` 能看到；但不影响判定。
    """
    bot = _pick_bot()
    state = runtime.state.section('offline')
    missing = int(state.get('missing') or 0)
    online: bool | None = None
    detail = ''
    error = ''

    if bot is None:
        missing += 1
        state['missing'] = missing
        online = False if missing >= 2 else None
        detail = '没有已连接的 Bot'
    else:
        missing = 0
        state['missing'] = 0
        online = True
        try:
            status = _as_dict(await _call(bot, 'get_status', timeout=3))
            if 'online' in status:
                online = bool(status['online'])
        except Exception as exc:  # noqa: BLE001 - 接口缺失/超时都不算掉线
            error = f'{type(exc).__name__}: {exc}'[:160]

    state['last_error'] = error
    decision = logic.evaluate_offline(state, online, now=now)
    runtime.state.set('offline', decision['state'])
    runtime.state.save()

    conf = runtime._alert_conf()
    if decision['offline']:
        record_event(runtime, int(now), 'offline', 'Bot 掉线', detail or 'OneBot 返回 online=false')
        if conf.get('enabled') and conf.get('offline_notify', True):
            await push(runtime, runtime.targets('alert'),
                       f'【掉线】Bot 已离线\n时间：{logic.format_clock(now)}', label='掉线通知')
    if decision['recovered']:
        runtime.sample_store.resolve_event('Bot 掉线', int(now))
        record_event(runtime, int(now), 'recovery', 'Bot 掉线', '已恢复')
        if conf.get('enabled') and conf.get('offline_notify', True):
            await push(runtime, runtime.targets('alert'),
                       f'【恢复】Bot 已重新上线\n离线时长：{logic.format_duration(decision["duration"])}',
                       label='恢复通知')


async def check_provider(runtime: Runtime, *, now: float) -> None:
    conf = runtime._alert_conf().get('provider_watch') or {}
    if not conf.get('enabled'):
        return
    section = runtime.state.section('provider')
    streak = runtime.quality.streak
    threshold = int(conf.get('threshold') or 3)
    alerting = bool(section.get('alerting'))
    last_sent = float(section.get('last_sent') or 0)
    cooldown = float(conf.get('cooldown_seconds') or 1800)

    if streak >= threshold and not alerting and now - last_sent >= cooldown:
        section = {'alerting': True, 'last_sent': now, 'since': now}
        runtime.state.set('provider', section)
        runtime.state.save()
        record_event(runtime, int(now), 'llm', 'LLM 连续失败', f'连续 {streak} 次')
        await push(runtime, runtime.targets('alert'),
                   f'【异常】LLM 连续失败\n当前连续失败：{streak} 次', label='LLM 告警')
    elif streak == 0 and alerting:
        duration = max(0.0, now - float(section.get('since') or now))
        runtime.state.set('provider', {'alerting': False, 'last_sent': last_sent, 'since': 0})
        runtime.state.save()
        runtime.sample_store.resolve_event('LLM 连续失败', int(now))
        record_event(runtime, int(now), 'recovery', 'LLM 连续失败', '已恢复')
        await push(runtime, runtime.targets('alert'),
                   f'【恢复】LLM 服务已恢复\n中断时长：{logic.format_duration(duration)}', label='LLM 恢复')


# ---------------------------------------------------------------------------
# 服务监测
# ---------------------------------------------------------------------------


async def run_monitors(runtime: Runtime, *, now: float, notify: bool = True) -> list[dict[str, Any]]:
    """跑一轮全部监测项，返回每条的结果。

    分两遍：先探测、再判定。只有先拿到「依赖项这一轮到底通不通」，才能实现
    `suppress_when_down`——上游的「重复层抑制」。以前是一边探一边判，抑制配置
    只能解析、无法生效，那种「配了没作用」的字段比没有更糟。
    """
    conf = runtime._monitor_conf()
    items = monitor.normalize_items(conf.get('items') or [])
    active = [item for item in items if item.get('enabled') is not False]
    state = runtime.state.section('monitor')
    silenced = bool(conf.get('silence_enabled')) and monitor.should_silence(str(conf.get('silence_until') or ''))
    max_bytes = int(conf.get('max_response_bytes') or 262144)

    probes: dict[str, dict[str, Any]] = {}
    for item in active:
        probes[item['name']] = await monitor.probe(item, max_response_bytes=max_bytes)
    failing = {name for name, outcome in probes.items() if not outcome.get('ok')}

    results: list[dict[str, Any]] = []
    for item in active:
        name = item['name']
        outcome = probes[name]
        decision = monitor.evaluate_monitor(
            state.get(name),
            bool(outcome.get('ok')),
            fail_threshold=int(conf.get('fail_threshold') or 2),
            cooldown_seconds=float(conf.get('cooldown_seconds') or 1800),
            now=now,
        )
        state[name] = {**decision['state'], 'detail': outcome.get('detail', ''), 'kind': item['kind']}
        dependency = str(item.get('suppress_when_down') or '')
        suppressed = bool(dependency) and dependency in failing
        results.append({
            'name': name,
            'kind': item['kind'],
            'ok': bool(outcome.get('ok')),
            'detail': outcome.get('detail', ''),
            'config_error': bool(outcome.get('config_error')),
            'suppressed': suppressed,
        })

        if decision['alert']:
            record_event(runtime, int(now), 'service', name, str(outcome.get('detail') or ''))
            sendable = notify and not silenced and not suppressed and not outcome.get('config_error')
            if sendable:
                await push(runtime, runtime.targets('monitor'),
                           f'【异常】服务不可用\n{name}（{item["kind"]}）\n{outcome.get("detail", "")}',
                           label='监测告警')
        if decision['recovery']:
            runtime.sample_store.resolve_event(name, int(now))
            record_event(runtime, int(now), 'recovery', name, '已恢复')
            if notify and not silenced and conf.get('notify_recovery', True):
                await push(runtime, runtime.targets('monitor'),
                           f'【恢复】服务已恢复\n{name}\n中断：{logic.format_duration(decision["duration"])}',
                           label='监测恢复')

    runtime.state.set('monitor', state)
    runtime.state.save()
    return results


# ---------------------------------------------------------------------------
# 报表
# ---------------------------------------------------------------------------


def _token_summary() -> dict[str, Any]:
    if token_logic is None:
        return {}
    try:
        ledger = token_logic.TokenLedger(ROOT / 'data' / 'token-usage.json')
        data = ledger.summary(days=7)
        totals = data.get('totals') or {}
        models = data.get('models') or []
        return {
            'yesterday': totals.get('yesterday', 0),
            'week': totals.get('week', 0),
            'all': totals.get('all', 0),
            'top_model': models[0]['model'] if models else '',
        }
    except Exception:  # noqa: BLE001 - 报表少一段比整张报错好
        return {}


def build_report_view(runtime: Runtime, *, kind: str, day: str) -> dict[str, Any]:
    conf = runtime._report_conf()
    sections = conf.get('weekly_sections') if kind == 'weekly' and conf.get('weekly_sections') else conf.get('sections')
    history_days = int(conf.get('history_days') or 7)
    window = history_days if kind == 'weekly' else 1
    start, _ = store.day_window(day)
    since = start - (window - 1) * 86400

    resources = runtime.sample_store.list_resources(since)
    websites = runtime.sample_store.list_website_samples(since)
    processes = runtime.sample_store.list_process_samples(since)
    events = runtime.sample_store.list_events(since)
    snapshots = runtime.sample_store.list_snapshots(days=window + 1)
    tokens = _token_summary()
    quality = runtime.quality.summary(day=day)

    return logic.build_report(
        kind,
        day=day,
        days=window,
        host=str(runtime.state.get('host') or ''),
        sections=sections or [],
        resources=resources,
        websites=websites,
        processes=processes,
        events=events,
        snapshots=snapshots,
        tokens=tokens,
        llm=quality,
        footer='内容口径与状态卡一致 · 本地 Pillow 渲染',
    )


async def send_report(runtime: Runtime, *, kind: str, day: str, targets: list[dict[str, Any]]) -> bool:
    conf = runtime._report_conf()
    report = build_report_view(runtime, kind=kind, day=day)
    if conf.get('with_image', True):
        try:
            png = await asyncio.to_thread(image.render_report, report, width=int(runtime._display_conf().get('width') or 1170))
            if await push(runtime, targets, MessageSegment.image(png), label=f'{kind} 报表'):
                runtime.sample_store.add_report_run(day, int(time.time()), True, 'image')
                return True
        except image.RenderError as exc:
            logger.warning(f'[status] 报表出图失败，回退文字：{exc}')
    text = '\n'.join(
        [f'{report["title"]}（{report["subtitle"]}）']
        + [f'· {section["title"]}：' + ' '.join(f'{row[0]}={row[1]}' for row in section.get('rows') or [])
           for section in report.get('sections') or []]
    )
    ok = await push(runtime, targets, text, label=f'{kind} 报表')
    runtime.sample_store.add_report_run(day, int(time.time()), ok, 'text')
    return ok


# ---------------------------------------------------------------------------
# 后台任务
# ---------------------------------------------------------------------------


async def sample_loop(runtime: Runtime) -> None:
    """采样：网络/磁盘速率、月流量、历史折线。"""
    while True:
        try:
            runtime.maybe_reload()
            conf = runtime._display_conf()
            interval = max(10, int(conf.get('sample_seconds') or 60))
            now = time.time()
            net_state = runtime.state.section('net')
            interface = collect.pick_interface(net_state.get('interface') or None)
            counters = collect.net_counters(interface)
            io_counters = collect.disk_io_counters()
            last_sample = float(net_state.get('last_sample') or 0)
            elapsed = max(0.0, now - last_sample) if last_sample else 0.0
            up, down = collect.net_rates(net_state.get('counter'), counters, elapsed)
            read, write = collect.disk_io_rates(
                (runtime.state.section('disk_io') or {}).get('counter'), io_counters, elapsed)

            month = logic.update_month_traffic({
                'month': net_state.get('month'),
                'month_up': net_state.get('month_up'),
                'month_down': net_state.get('month_down'),
                'sent_base': net_state.get('sent_base'),
                'recv_base': net_state.get('recv_base'),
            }, int(counters['sent']), int(counters['recv']))

            history = net_state.get('history') if isinstance(net_state.get('history'), list) else []
            history.append({'ts': now, 'up': up, 'down': down})
            history = history[-60:]

            runtime.state.set('net', {
                'counter': counters,
                'interface': interface,
                'last_sample': now,
                'interval': interval,
                'history': history,
                'month': month.get('month'),
                'month_up': month.get('month_up'),
                'month_down': month.get('month_down'),
                'sent_base': month.get('sent_base'),
                'recv_base': month.get('recv_base'),
            })
            runtime.state.set('disk_io', {'counter': io_counters, 'last_sample': now})
            runtime.state.set('host', next((row['value'] for row in collect.system_rows() if row['label'] == '主机名'), ''))
            runtime.flush_counters()
            await _persist_panel_state(runtime)

            # 采样库存的是「报表粒度」，比卡片折线稀疏，避免库里堆满分钟点
            report_conf = runtime._report_conf()
            every = max(60, int(report_conf.get('resource_sample_seconds') or 300))
            last_db = float((runtime.state.section('sampler') or {}).get('last_db') or 0)
            if report_conf.get('collect', True) and now - last_db >= every:
                runtime.state.set('sampler', {'last_db': now})
                await _write_samples(runtime, now, up, down)

            await _maybe_snapshot(runtime)
            runtime.quality.ensure()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 采样失败不能让循环停
            logger.exception('[status] 采样循环异常')
        await asyncio.sleep(max(10, int(runtime._display_conf().get('sample_seconds') or 60)))


async def _persist_panel_state(runtime: Runtime) -> None:
    """把 QQ 侧状态写进状态文件。

    管理面板是另一个进程，读不到这个进程里的 bot 缓存；不落盘的话，面板上的
    好友/群/在线状态就只能显示 `--`。写入的是缓存值，不会额外打 OneBot 接口。
    """
    try:
        bot = await collect_bot_info(runtime)
        runtime.state.set('panel', {
            'ts': time.time(),
            'online': bot.get('online'),
            'friends': bot.get('friends'),
            'groups': bot.get('groups'),
            'members': bot.get('members'),
            'nick': bot.get('nick'),
            'self_id': bot.get('self_id'),
            'protocol': bot.get('protocol'),
            'protocol_version': bot.get('protocol_version'),
            'sent': runtime.counters.get('sent'),
            'recv': runtime.counters.get('recv'),
        })
        runtime.state.save()
    except Exception:  # noqa: BLE001 - 面板少一块数据不该影响采样
        logger.debug('[status] 面板状态写入失败', exc_info=True)


async def _write_samples(runtime: Runtime, now: float, up: float, down: float) -> None:
    cpu = await asyncio.to_thread(collect.cpu_snapshot)
    memory = await asyncio.to_thread(collect.memory_snapshot)
    swap = await asyncio.to_thread(collect.swap_snapshot)
    paths = runtime._display_conf().get('disk_paths') or collect.default_disk_paths()
    disks = await asyncio.to_thread(collect.disk_snapshot, paths)
    disk_pct = max((float(item['percent']) for item in disks if item.get('percent') is not None), default=None)

    runtime.sample_store.add_resource(
        int(now),
        cpu=float(cpu.get('percent') or 0),
        mem=float(memory.get('percent') or 0),
        swap=float(swap.get('percent') or 0),
        disk=disk_pct,
        net_up=up,
        net_down=down,
    )

    websites = await probe_websites(runtime)
    if websites:
        runtime.sample_store.add_website_samples(int(now), websites)

    top_n = int(runtime._report_conf().get('process_top_n') or 5)
    process = await asyncio.to_thread(collect.process_snapshot, top_n)
    rows = []
    for item in process.get('top') or []:
        rows.append({'kind': 'cpu', 'name': item['name'], 'value': item.get('cpu_pct_total', item['cpu'])})
    by_mem = sorted(process.get('top') or [], key=lambda entry: -float(entry.get('mem') or 0))[:top_n]
    for item in by_mem:
        rows.append({'kind': 'mem', 'name': item['name'], 'value': item['mem']})
    if rows:
        runtime.sample_store.add_process_samples(int(now), rows)


async def _maybe_snapshot(runtime: Runtime) -> None:
    """每天 00:05 之后记一次快照，用来算「昨日接收/发送」。"""
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    if now.hour == 0 and now.minute < 5:
        return
    if runtime.sample_store.snapshot_ready(today):
        return
    bot = await collect_bot_info(runtime)
    runtime.sample_store.record_snapshot(
        today,
        friends=bot.get('friends'),
        groups=bot.get('groups'),
        members=bot.get('members'),
        recv_total=runtime.counters.get('recv'),
        sent_total=runtime.counters.get('sent'),
    )


async def alert_loop(runtime: Runtime) -> None:
    while True:
        try:
            now = time.time()
            await check_offline(runtime, now=now)
            await evaluate_resources(runtime, now=now)
            await check_provider(runtime, now=now)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception('[status] 告警循环异常')
        await asyncio.sleep(max(20, int(runtime._alert_conf().get('interval_seconds') or 60)))


async def monitor_loop(runtime: Runtime) -> None:
    while True:
        try:
            if runtime._monitor_conf().get('enabled'):
                await run_monitors(runtime, now=time.time())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception('[status] 监测循环异常')
        await asyncio.sleep(max(20, int(runtime._monitor_conf().get('interval_seconds') or 60)))


async def schedule_loop(runtime: Runtime) -> None:
    """每日状态图、日报、周报。按「今天发过没有」标记，错过当天会补发。"""
    while True:
        try:
            await _run_schedule(runtime)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception('[status] 定时循环异常')
        await asyncio.sleep(30)


async def _run_schedule(runtime: Runtime) -> None:
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    clock = now.strftime('%H:%M')
    markers = runtime.state.section('schedule')
    first_day = str(runtime.state.get('first_seen') or '')

    alerts = runtime._alert_conf()
    daily = alerts.get('daily_status') or {}
    if daily.get('enabled'):
        target_time = logic.parse_hhmm(str(daily.get('time') or '09:00'))
        if target_time and _due(markers.get('daily_status'), today, clock, target_time, first_day):
            ok = await _send_daily_status(runtime, image_on=bool(daily.get('with_image', True)))
            # 成败都记账：失败时如果继续留空标记，会每 30 秒重试一次并把日志刷满。
            markers['daily_status'] = today
            runtime.state.set('schedule', markers)
            runtime.state.save()
            if not ok:
                logger.warning('[status] 每日定时状态图发送失败，今天不再重试')

    report = runtime._report_conf()
    if report.get('enabled'):
        target_time = logic.parse_hhmm(str(report.get('time') or '21:00'))
        if target_time and _due(markers.get('daily_report'), today, clock, target_time, first_day):
            yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
            ok = await send_report(runtime, kind='daily', day=yesterday, targets=_report_targets(runtime))
            markers['daily_report'] = today
            runtime.state.set('schedule', markers)
            runtime.state.save()
            if not ok:
                logger.warning('[status] 日报发送失败，今天不再重试（可用 预览日报 手动补）')

    if report.get('weekly_enabled') and now.weekday() == 6:
        target_time = logic.parse_hhmm(str(report.get('weekly_time') or '21:30'))
        if target_time and _due(markers.get('weekly_report'), today, clock, target_time, first_day):
            yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
            ok = await send_report(runtime, kind='weekly', day=yesterday, targets=_report_targets(runtime))
            markers['weekly_report'] = today
            runtime.state.set('schedule', markers)
            runtime.state.save()
            if not ok:
                logger.warning('[status] 周报发送失败，今天不再重试（可用 预览周报 手动补）')

    # 每天第一次跑过整点后顺手清理一次
    if markers.get('pruned') != today and now.hour >= 4:
        removed = runtime.sample_store.prune(
            resource_days=int(report.get('retention_days') or 30),
            event_days=int(report.get('event_retention_days') or 90),
        )
        markers['pruned'] = today
        runtime.state.set('schedule', markers)
        runtime.state.save()
        if any(removed.values()):
            logger.info(f'[status] 清理过期采样：{removed}')


def _due(marker: Any, today: str, clock: str, target: tuple[int, int], first_day: str) -> bool:
    """今天这一班该不该发。

    分三种情况，别把它们混在一起：

    - **今天已发过** —— 不发。
    - **有历史标记、今天没发** —— 补发。机器人昨晚重启错过了整点，醒来要补。
    - **从来没有标记**（刚装好）—— 只在正好到点的那一分钟触发。否则装完插件
      的下一秒就会往群里推一张卡，而用户只是刚改完配置。
    """
    target_clock = f'{target[0]:02d}:{target[1]:02d}'
    if clock < target_clock:
        return False
    raw = str(marker or '')
    if raw >= today:
        return False
    if not raw:
        return clock == target_clock
    if first_day and first_day >= today:
        return False
    return logic.daily_marker_due(raw, today, first_day=first_day or None)


def _report_targets(runtime: Runtime) -> list[dict[str, Any]]:
    targets = runtime.targets('report')
    return targets or runtime.targets()


async def _send_daily_status(runtime: Runtime, *, image_on: bool) -> bool:
    targets = runtime.targets('daily')
    if not targets:
        logger.warning('[status] 每日定时状态图没有推送目标')
        return False
    if not image_on:
        snapshot = await build_snapshot(runtime)
        view = logic.build_view(snapshot, redact=runtime._display_conf().get('redact') or [])
        return await push(runtime, targets, logic.summary_text(view), label='每日状态图')
    try:
        png = await render_card(runtime)
        return await push(runtime, targets, MessageSegment.image(png), label='每日状态图')
    except image.RenderError as exc:
        logger.warning(f'[status] 每日状态图出图失败，回退文字：{exc}')
        snapshot = await build_snapshot(runtime)
        view = logic.build_view(snapshot, redact=runtime._display_conf().get('redact') or [])
        return await push(runtime, targets, logic.summary_text(view), label='每日状态图')


# ---------------------------------------------------------------------------
# 接线
# ---------------------------------------------------------------------------

runtime = Runtime(lazy=True)


def _is_admin(event: MessageEvent) -> bool:
    supers = {str(user) for user in getattr(get_driver().config, 'superusers', None) or set()}
    return str(event.get_user_id()) in supers


def _command(event: MessageEvent) -> str:
    return event.get_plaintext().strip().lstrip('./').strip()


async def _rule(event: MessageEvent) -> bool:
    return _command(event) in COMMANDS


matcher = on_message(rule=_rule, priority=1, block=True)


@matcher.handle()
async def handle_command(bot: Bot, event: MessageEvent) -> None:
    command = _command(event)
    if not _is_admin(event):
        await matcher.finish('这个指令仅主人可用。')

    if command == '查看状态' or command == '状态卡':
        await send_status_card(bot, event)
    elif command == '状态卡诊断':
        await matcher.finish(diagnose())
    elif command == '测试通知':
        await test_notify(event)
    elif command == '立即检测':
        await matcher.finish(await manual_check())
    elif command == '预览日报':
        await preview_report(event, kind='daily')
    elif command == '预览周报':
        await preview_report(event, kind='weekly')


async def send_status_card(bot: Bot, event: MessageEvent) -> None:
    conf = runtime._display_conf()
    session = f'{event.get_session_id()}'
    cooldown = float(conf.get('cooldown_seconds') or 10)
    now = time.time()
    if cooldown > 0 and now - float(runtime.cooldowns.get(session) or 0) < cooldown:
        await matcher.finish('状态图刚发过，稍等一会儿再试。')
    runtime.cooldowns[session] = now
    if len(runtime.cooldowns) > 500:
        runtime.cooldowns.clear()

    # 不再先发一句「生成中…」：本地出图是几百毫秒的事，多一条消息只是噪音。
    # 出图失败时下面会回退成文字摘要，那时用户自然会知道。
    try:
        png = await render_card(runtime)
    except image.RenderError as exc:
        logger.warning(f'[status] 状态卡出图失败：{exc}')
        snapshot = await build_snapshot(runtime)
        view = logic.build_view(snapshot, redact=conf.get('redact') or [])
        await matcher.finish(logic.summary_text(view))
        return
    try:
        await bot.send(event, MessageSegment.image(png))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f'[status] 状态卡发送失败：{exc}')
        await matcher.finish('图片发送失败，请检查协议端连接。')


def _admin_line() -> str:
    """当前生效的超级管理员人数与来源，方便确认面板改动有没有生效。

    人数读 `driver.config.superusers`——那是各插件判定真正用的值；来源看名单
    文件在不在。两处分开取，避免「文件读不出来」把人数也一起报错。
    """
    try:
        from_file = superuser_settings.load() is not None
        current = {str(user) for user in (getattr(get_driver().config, 'superusers', None) or ())}
    except Exception:  # noqa: BLE001 - 诊断本身不能因为读名单失败而挂掉
        return '超级管理员：读取失败'
    where = '面板名单' if from_file else '.env 初始值'
    return f'超级管理员：{len(current)} 人（{where}）'


def diagnose() -> str:
    """排障命令：把「现在到底能不能出图、数据从哪来」一次说清。"""
    bots = get_bots()
    bot = next(iter(bots.values()), None)
    bot_cache = runtime.bot_cache
    stats = runtime.sample_store.stats()
    counters = runtime.counters
    lines = [
        '【状态卡诊断】',
        f'版本：{__plugin_meta__.name} · 本地 Pillow 渲染',
        f'平台：OneBot v11 · 已连接 Bot：{len(bots)} 个',
        f'目标 Bot：{", ".join(str(getattr(b, "self_id", "?")) for b in bots.values()) or "无"}',
        f'在线状态缓存：{"有" if bot_cache.get("online") else "无"}'
        f'（探测失败 {int(runtime.state.section("offline").get("probe_failures") or 0)} 次'
        f'{"；最后错误 " + str(runtime.state.section("offline").get("last_error")) if runtime.state.section("offline").get("last_error") else ""}）',
        f'渲染：宽 {runtime._display_conf().get("width")} · 最近耗时 {runtime.last_render_ms:.0f}ms',
        f'采样库：{"就绪" if stats.get("ready") else "不可用"} · 路径 {stats.get("path", "-")}',
        f'行数：{stats.get("counts", {})}',
        f'收发计数：收 {counters.get("recv", 0)} / 发 {counters.get("sent", 0)}',
        f'告警：{"开" if runtime._alert_conf().get("enabled") else "关"}'
        f' · 目标 {len(runtime.targets("alert"))} 个',
        f'监测：{"开" if runtime._monitor_conf().get("enabled") else "关"}'
        f' · 条目 {len(monitor.normalize_items(runtime._monitor_conf().get("items") or []))} 个',
        f'日报：{"开" if runtime._report_conf().get("enabled") else "关"}'
        f' · 周报：{"开" if runtime._report_conf().get("weekly_enabled") else "关"}',
        f'进程运行：{logic.format_duration(collect.process_uptime())}'
        f' · 开机：{logic.format_duration(collect.uptime_seconds())}',
        f'LLM 连续失败：{runtime.quality.streak}',
        _admin_line(),
        f'最近推送：{runtime.last_push.get("detail", "无记录")}',
    ]
    limit = collect.cgroup_memory_limit()
    if limit:
        lines.append(
            f'⚠ 容器内存上限 {logic.format_bytes(limit)}：psutil 读到的是宿主机内存，'
            f'卡片百分比在容器里会偏低'
        )
    for item in monitor.normalize_items(runtime._monitor_conf().get('items') or []):
        if item.get('invalid'):
            lines.append(f'⚠ 监测项「{item["name"]}」{item["invalid"]}')
        elif item.get('duplicates'):
            lines.append(f'⚠ 监测项「{item["name"]}」有 {item["duplicates"]} 条重复，已忽略')
    return '\n'.join(lines)


async def test_notify(event: MessageEvent) -> None:
    target = logic.parse_target(
        f'group:{event.group_id}' if getattr(event, 'group_id', None) else f'private:{event.get_user_id()}'
    )
    if not target:
        await matcher.finish('无法确定当前会话的推送目标。')
    ok = await push(runtime, [target], '【测试】推送链路正常。', label='测试通知')
    await matcher.finish('测试通知已发送。' if ok else '测试通知发送失败，请查看日志。')


async def manual_check() -> str:
    items = [item for item in monitor.normalize_items(runtime._monitor_conf().get("items") or []) if item.get('enabled') is not False]
    if not items:
        return '还没有配置监测目标。'
    results = await run_monitors(runtime, now=time.time(), notify=False)
    abnormal = [item for item in results if not item['ok']]
    lines = [f'【立即检测】共 {len(results)} 项，异常 {len(abnormal)} 项']
    for item in abnormal or results:
        flag = '✗' if not item['ok'] else '✓'
        lines.append(f'{flag} {item["name"]}：{item["detail"]}')
    return '\n'.join(lines)


async def preview_report(event: MessageEvent, *, kind: str) -> None:
    now = datetime.now()
    day = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    targets = logic.parse_targets(
        [f'group:{event.group_id}' if getattr(event, 'group_id', None) else f'private:{event.get_user_id()}']
    )
    await push(runtime, targets, '报表生成中…', label='报表提示')
    ok = await send_report(runtime, kind=kind, day=day, targets=targets)
    if not ok:
        await matcher.finish('报表发送失败，请查看日志。')


async def on_message_count(event: MessageEvent) -> None:
    runtime.bump('recv')


recv_matcher = on_message(priority=0, block=False)


@recv_matcher.handle()
async def _count_recv(event: MessageEvent) -> None:
    if event.post_type != 'message':
        return
    await on_message_count(event)
    # 查询接口偶发失败时 self_id 会拿不到，从收到的事件里补一份
    self_id = str(getattr(event, 'self_id', '') or '')
    if self_id and self_id != str(runtime.state.get('self_id') or ''):
        runtime.state.set('self_id', self_id)


def _install_send_counter(bot: Bot) -> None:
    """包一层 `call_api` 数「发出去了几条」。

    NoneBot 没有全局的发送钩子，但所有发送最终都走 `bot.call_api`。只在这个
    bot 实例上包一层，只记成功、异常原样抛出——发送路径不能因为统计而改变行为。
    """
    if getattr(bot, '_statuscard_counting', False):
        return
    original = bot.call_api

    async def counted(action: str, **params: Any) -> Any:
        result = await original(action, **params)
        if str(action) in SEND_ACTIONS:
            runtime.bump('sent')
        return result

    try:
        bot.call_api = counted  # type: ignore[method-assign]
        bot._statuscard_counting = True  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 包不上就只显示收到数
        logger.debug('[status] 发送计数安装失败，卡片上的「回复」将不准确')


@get_driver().on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    _install_send_counter(bot)
    for row in await asyncio.gather(_call(bot, 'get_login_info', timeout=6), return_exceptions=True):
        if isinstance(row, dict):
            user_id = _as_dict(row).get('user_id')
            if user_id:
                runtime.state.set('self_id', str(user_id))
                runtime.state.save()


@get_driver().on_startup
async def _startup() -> None:
    runtime.prepare()
    runtime.quality.install()
    await asyncio.to_thread(collect.prime_cpu)
    await asyncio.to_thread(collect.prime_process_cpu)

    config = runtime.config
    logger.info(
        f'[status] 状态卡就绪：宽 {config["display"]["width"]}，'
        f'告警 {"开" if config["alerts"]["enabled"] else "关"}，'
        f'监测 {"开" if config["monitors"]["enabled"] else "关"}，'
        f'日报 {"开" if config["report"]["enabled"] else "关"}'
    )
    for item in monitor.normalize_items(config['monitors'].get('items') or []):
        if item.get('invalid'):
            logger.warning(f'[status] 监测项「{item["name"]}」不可用：{item["invalid"]}')

    for label, section, clock_key in (
        ('每日定时状态图', config['alerts'].get('daily_status') or {}, 'time'),
        ('日报', config['report'], 'time'),
    ):
        if not section.get('enabled'):
            continue
        parsed = logic.parse_hhmm(str(section.get(clock_key) or ''))
        if parsed:
            wait = logic.seconds_until(datetime.now(), *parsed)
            logger.info(f'[status] {label}已启用，约 {wait / 3600:.1f} 小时后触发')

    runtime.tasks = [
        asyncio.create_task(sample_loop(runtime)),
        asyncio.create_task(alert_loop(runtime)),
        asyncio.create_task(monitor_loop(runtime)),
        asyncio.create_task(schedule_loop(runtime)),
    ]


@get_driver().on_shutdown
async def _shutdown() -> None:
    for task in runtime.tasks:
        task.cancel()
    if runtime.tasks:
        await asyncio.gather(*runtime.tasks, return_exceptions=True)
    runtime.tasks = []
    runtime.flush_counters()
