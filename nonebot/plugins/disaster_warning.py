# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""灾害预警：Wolfx 免鉴权地震源的接线层。

纯逻辑在 `disaster_logic`，HTTP 在 `disaster_sources`，去重状态在
`disaster_state`，面板配置在 `disaster_config`。这里只负责：

- 两个后台轮询任务：EEW 10 秒、列表 60 秒；
- 首轮只做基线（不把历史地震刷屏），之后只推新事件；
- 按面板里的震级/距离/本地开关过滤；
- 推送到一个运维群 + 主人私聊；
- 查询命令与管理员测试命令。
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from nonebot import get_bots, get_driver, logger, on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageSegment
from muicebot.plugin import PluginMetadata

import gate_logic as gl
import disaster_china_logic as china_logic
import disaster_china_sources as china_sources
import disaster_config as config_mod
import disaster_logic as logic
import disaster_render as render_mod
import disaster_sources as sources
import disaster_state as state_mod
import disaster_store as store_mod
import disaster_tone as tone_mod

__plugin_meta__ = PluginMetadata(
    name='disaster_warning',
    description='灾害预警：Wolfx 地震 + Jian 气象/海啸 + EQSC 台风',
    usage='地震列表 / 地震预警 / 气象预警 / 台风 / 海啸 / 灾害源状态 / 地震源状态 / 地震测试',
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
CONFIG_PATH = DATA / 'disaster_warning.json'
STATE_PATH = DATA / 'disaster_warning_state.json'

EEW_INTERVAL = 10
LIST_INTERVAL = 60
#: 列表接口正常返回 50 条；首轮少于这个数说明可能拿到空/半截响应，不设基线，
#: 否则下一轮恢复后会把整页历史当成新事件全推出去。
LIST_BASELINE_MIN = 40
EQSC_DEFAULT_INTERVAL = 120
EQSC_MIN_INTERVAL = 30
EQSC_MAX_INTERVAL = 600
JIAN_RETRY_MAX = 120

COMMANDS = (
    '地震列表', '地震预警', '地震源状态', '地震测试',
    '气象预警', '台风', '海啸', '灾害源状态',
)

#: `.help` 的说明。导入时就注册，别等 on_startup —— `.help` 可能更早跑。
gl.register_help_rows([
    ('地震列表', '最近地震（CENC/JMA，可带 cenc|jma 与数量）'),
    ('地震预警', '最近收到的地震预警/情报'),
    ('地震源状态', 'Wolfx 免鉴权源的轮询与推送状态（仅主人）'),
    ('地震测试', '发一条样例地震消息到当前会话（仅主人）'),
    ('气象预警', '最近气象预警（Jian；可带数量）'),
    ('台风', '当前活跃台风（EQSC）'),
    ('海啸', '最近海啸预警（Jian；可带数量）'),
    ('灾害源状态', 'Wolfx/Jian/EQSC 源状态与最近错误（仅主人）'),
])

_command_re = re.compile(
    r'^\s*[./]?(地震列表|地震预警|地震源状态|地震测试|气象预警|台风|海啸|灾害源状态)\s*(.*?)\s*$'
)

PushFn = Callable[[str, dict[str, Any], bytes | None], Awaitable[bool]]
RenderFn = Callable[[logic.EarthquakeEvent, dict[str, Any], str], Awaitable[bytes | None]]


async def _close_source(source: Any) -> None:
    close = getattr(source, 'close', None)
    if close is None:
        return
    try:
        await close()
    except Exception:
        pass


def _parse_command(text: str) -> tuple[str, str] | None:
    match = _command_re.match(text or '')
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def _parse_list_args(args: str) -> tuple[str, int]:
    source = 'cenc'
    limit = 5
    for token in (args or '').split():
        low = token.lower()
        if low in ('cenc', '中国', '中国地震台网'):
            source = 'cenc'
        elif low in ('jma', '日本', '日本气象厅'):
            source = 'jma'
        elif token.isdigit():
            limit = max(1, min(int(token), 20))
    return source, limit


def _parse_limit(args: str, default: int = 3, maximum: int = 20) -> int:
    for token in (args or '').split():
        if token.isdigit():
            return max(1, min(int(token), maximum))
    return default


def _is_admin(event: Event) -> bool:
    try:
        users = get_driver().config.superusers or ()
    except ValueError:
        return False
    return str(event.get_user_id()) in {str(user) for user in users}


def _rule(event: Event) -> bool:
    return _parse_command(event.get_plaintext()) is not None


matcher = on_message(rule=_rule, priority=1, block=True)


class Runtime:
    """把配置、状态、源客户端和两个轮询任务装在一起。"""

    def __init__(self, *, base: Path | None = None,
                 source: sources.WolfxSource | None = None,
                 push: PushFn | None = None,
                 render: RenderFn | None = None,
                 jian_factory: Callable[[str], Any] | None = None,
                 eqsc_factory: Callable[[str], Any] | None = None):
        self.data_dir = Path(base) if base is not None else DATA
        self.config_path = self.data_dir / 'disaster_warning.json'
        self.state_path = self.data_dir / 'disaster_warning_state.json'
        self.store_path = self.data_dir / 'disaster_warning.sqlite3'
        self.config = config_mod.load_config(self.config_path)
        self.state = state_mod.load(self.state_path)
        self.store = store_mod.EventStore(self.store_path, log=logger)
        self.source = source or sources.WolfxSource()
        self._owns_source = source is None
        self._push_override = push
        self._render_override = render
        self._jian_factory = jian_factory
        self._eqsc_factory = eqsc_factory
        self.lists: dict[str, list[logic.EarthquakeEvent]] = {'cenc': [], 'jma': []}
        self.latest: list[logic.EarthquakeEvent] = []
        self.weather: list[china_logic.WeatherAlert] = []
        self.tsunamis: list[china_logic.TsunamiAlert] = []
        self.typhoons: list[china_logic.Typhoon] = []
        self._eqsc_active: dict[str, china_logic.Typhoon] = {}
        self._eqsc_baseline = True
        self.tasks: list[asyncio.Task] = []
        self._save_lock = asyncio.Lock()

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reload_config()
        self.state = state_mod.load(self.state_path)
        self.store.prune(store_mod.KEEP_DAYS)

    def reload_config(self) -> dict[str, Any]:
        self.config = config_mod.load_config(self.config_path)
        return self.config

    def _enabled(self, config: dict[str, Any]) -> bool:
        if not config.get('enabled'):
            return False
        return bool((config.get('sources') or {}).get('earthquake_free', {}).get('enabled'))

    @staticmethod
    def _targets(config: dict[str, Any]) -> list[tuple[str, str]]:
        push = config.get('push') or {}
        if not push.get('enabled'):
            return []
        targets: list[tuple[str, str]] = []
        group_id = str(push.get('group_id') or '').strip()
        owner_id = str(push.get('owner_id') or '').strip()
        if group_id.isdigit():
            targets.append(('group', group_id))
        if owner_id.isdigit():
            targets.append(('private', owner_id))
        return targets

    async def start(self) -> None:
        if self.tasks:
            return
        self.tasks = [
            asyncio.create_task(self._eew_loop(), name='disaster-eew'),
            asyncio.create_task(self._list_loop(), name='disaster-list'),
            asyncio.create_task(self._jian_loop(), name='disaster-jian'),
            asyncio.create_task(self._eqsc_loop(), name='disaster-eqsc'),
        ]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        if self._owns_source:
            await _close_source(self.source)
        self.store.close()
        await self._save_state()

    async def _save_state(self) -> None:
        async with self._save_lock:
            state_mod.prune(self.state)
            state_mod.save(self.state, self.state_path)
            self.store.sync_health(self.state['last_poll'], self.state['errors'])

    async def _eew_loop(self) -> None:
        while True:
            try:
                config = self.reload_config()
                if self._enabled(config):
                    await self.poll_eew()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - 轮询任务不该被单次异常打死
                logger.warning(f'[disaster] EEW 轮询异常: {exc}')
            await asyncio.sleep(EEW_INTERVAL)

    async def _list_loop(self) -> None:
        while True:
            try:
                config = self.reload_config()
                if self._enabled(config):
                    await self.poll_lists()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                logger.warning(f'[disaster] 地震列表轮询异常: {exc}')
            await asyncio.sleep(LIST_INTERVAL)

    async def poll_eew(self) -> None:
        config = self.reload_config()
        if not self._enabled(config):
            return
        await self._poll_source('cenc_eew', self.source.cenc_eew, logic.parse_cenc_eew, config)
        await self._poll_source('jma_eew', self.source.jma_eew, logic.parse_jma_eew, config)
        await self._save_state()

    async def poll_lists(self) -> None:
        config = self.reload_config()
        if not self._enabled(config):
            return
        await self._poll_source('cenc_list', self.source.cenc_list, logic.parse_cenc_list, config)
        await self._poll_source('jma_list', self.source.jma_list, logic.parse_jma_list, config)
        await self._save_state()

    async def _poll_source(self, kind: str, fetch: Callable[[], Awaitable[dict[str, Any]]],
                           parser: Callable[[Any], Any], config: dict[str, Any]) -> None:
        try:
            data = await fetch()
            parsed = parser(data)
            if parsed is None:
                events: list[logic.EarthquakeEvent] = []
            elif isinstance(parsed, list):
                events = parsed
            else:
                events = [parsed]
        except sources.SourceError as exc:
            state_mod.record_poll(self.state, kind, error=str(exc))
            logger.warning(f'[disaster] {exc}')
            await self._save_state()
            return
        except Exception as exc:
            state_mod.record_poll(self.state, kind, error=f'{type(exc).__name__}: {exc}')
            logger.warning(f'[disaster] {kind} 解析失败: {exc}')
            await self._save_state()
            return

        state_mod.record_poll(self.state, kind)
        self._cache(kind, events)
        if not self.state['initialized'].get(kind):
            if kind.endswith('_list') and len(events) < LIST_BASELINE_MIN:
                await self._save_state()
                return
            # 首轮只做基线：历史地震与当前正在挂着的 EEW 不刷屏。
            self.state['initialized'][kind] = True
            if kind.endswith('_list'):
                self.state['baseline_at'][kind] = max(
                    (event.occurred_at for event in events if event.occurred_at),
                    default='',
                )
            for event in events:
                state_mod.mark_seen(self.state, event.key)
            await self._save_state()
            return
        baseline = self.state['baseline_at'].get(kind, '')
        for event in events:
            # 半页列表后来补全时，比基线旧的事件一律不补推。
            if kind.endswith('_list') and baseline and event.occurred_at and event.occurred_at <= baseline:
                continue
            await self._dispatch(event, config)
        await self._save_state()

    # ------------------------------------------------------------ 中国三类

    @staticmethod
    def _china_enabled(config: dict[str, Any], name: str) -> bool:
        if not config.get('enabled'):
            return False
        return bool(((config.get('sources') or {}).get(name) or {}).get('enabled'))

    def _create_jian(self, token: str):
        if self._jian_factory is not None:
            return self._jian_factory(token)
        return china_sources.JianSource(token, on_token=self._persist_jian_token)

    def _create_eqsc(self, token: str):
        if self._eqsc_factory is not None:
            return self._eqsc_factory(token)
        return china_sources.EqscSource(token)

    def _persist_jian_token(self, token: str, expires_date: str) -> None:
        current_token = str(
            ((self.config.get('sources') or {}).get('jian') or {}).get('token') or ''
        ).strip()
        if not token or token == current_token:
            return
        try:
            current = config_mod.load_config(self.config_path)
            updated = config_mod.apply_patch(current, {
                'sources': {'jian': {'token': token, 'token_expires_at': expires_date or ''}},
            })
            config_mod.save_config(updated, self.config_path)
            self.config = updated
            logger.info('[disaster] Jian 长期 Token 已更新并保存')
        except Exception as exc:
            logger.warning(f'[disaster] 保存 Jian 长期 Token 失败: {exc}')

    async def _jian_loop(self) -> None:
        backoff = 5
        while True:
            try:
                config = self.reload_config()
                source_cfg = (config.get('sources') or {}).get('jian') or {}
                token = str(source_cfg.get('token') or '').strip()
                if not self._china_enabled(config, 'jian') or not token:
                    await asyncio.sleep(30)
                    continue
                source = self._create_jian(token)
                try:
                    connection = await source.connect()
                    auth = getattr(source, 'auth', None)
                    if auth is not None:
                        refresh_token = str(getattr(auth, 'refresh_token', '') or '')
                        if refresh_token and refresh_token != token:
                            self._persist_jian_token(
                                refresh_token,
                                str(getattr(auth, 'refresh_expires_date', '') or ''),
                            )
                    backoff = 5
                    state_mod.record_poll(self.state, 'jian')
                    await self._save_state()
                    while True:
                        message = await connection.recv()
                        if message is None:
                            break
                        await self.handle_jian_message(message)
                finally:
                    await _close_source(source)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state_mod.record_poll(self.state, 'jian', error=f'{type(exc).__name__}: {exc}')
                logger.warning(f'[disaster] Jian 连接异常: {exc}')
                await self._save_state()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, JIAN_RETRY_MAX)
                continue
            await asyncio.sleep(5)

    async def _eqsc_loop(self) -> None:
        while True:
            interval = EQSC_DEFAULT_INTERVAL
            try:
                config = self.reload_config()
                source_cfg = (config.get('sources') or {}).get('eqsc') or {}
                token = str(source_cfg.get('token') or '').strip()
                if not self._china_enabled(config, 'eqsc') or not token:
                    await asyncio.sleep(interval)
                    continue
                await self.poll_typhoons()
                try:
                    interval = int(source_cfg.get('poll_seconds') or EQSC_DEFAULT_INTERVAL)
                except (TypeError, ValueError):
                    interval = EQSC_DEFAULT_INTERVAL
                interval = max(EQSC_MIN_INTERVAL, min(interval, EQSC_MAX_INTERVAL))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state_mod.record_poll(
                    self.state, 'eqsc', error=f'{type(exc).__name__}: {exc}'
                )
                logger.warning(f'[disaster] EQSC 台风轮询异常: {exc}')
                await self._save_state()
            await asyncio.sleep(interval)

    async def handle_jian_message(self, message: Any,
                                  config: dict[str, Any] | None = None) -> int:
        if not isinstance(message, dict):
            return 0
        msg_type = str(message.get('type') or '').strip().lower()
        if msg_type in ('heartbeat', 'pong'):
            return 0
        if msg_type == 'all':
            self._baseline_jian_all(message)
            await self._save_state()
            return 0
        config = config or self.reload_config()
        if not self._china_enabled(config, 'jian'):
            return 0
        payload = message.get('Data') if isinstance(message.get('Data'), (dict, list)) else message
        sent = 0
        if msg_type in ('weather', 'china_weather_alarm', 'weatheralert'):
            alerts = china_logic.parse_jian_weather_list(payload)
            self._cache_weather(alerts)
            for alert in alerts:
                if await self._dispatch_weather(alert, config):
                    sent += 1
            state_mod.record_poll(self.state, 'jian_weather')
        elif msg_type in ('nmefc-tsunami', 'china_tsunami', 'tsunami'):
            alerts = china_logic.parse_jian_tsunami_list(payload)
            self._cache_tsunami(alerts)
            for alert in alerts:
                if await self._dispatch_tsunami(alert, config):
                    sent += 1
            state_mod.record_poll(self.state, 'jian_tsunami')
        else:
            return 0
        await self._save_state()
        return sent

    def _baseline_jian_all(self, message: dict[str, Any]) -> None:
        self.state['initialized']['jian'] = True
        for hint, value in china_logic.iter_hinted_payloads(message):
            low = hint.lower()
            if 'tsunami' in low or 'nmefc' in low:
                for alert in china_logic.parse_jian_tsunami_list(value):
                    state_mod.mark_seen(self.state, alert.key)
            elif 'weather' in low:
                for alert in china_logic.parse_jian_weather_list(value):
                    state_mod.mark_seen(self.state, alert.key)
            elif isinstance(value, dict):
                if ('warningInfo' in value or 'originTime' in value) and (
                    'level' in value or 'title' in value
                ):
                    for alert in china_logic.parse_jian_tsunami_list(value):
                        state_mod.mark_seen(self.state, alert.key)
                elif any(key in value for key in (
                    'weather_type', 'weatherType', 'effective', 'province', 'city',
                )):
                    for alert in china_logic.parse_jian_weather_list(value):
                        state_mod.mark_seen(self.state, alert.key)
        logger.info('[disaster] Jian 首帧全量已建立基线')

    def _cache_weather(self, alerts: list[china_logic.WeatherAlert]) -> None:
        for alert in alerts:
            self.weather = [item for item in self.weather if item.key != alert.key]
            self.weather.insert(0, alert)
        self.weather = self.weather[:20]

    def _cache_tsunami(self, alerts: list[china_logic.TsunamiAlert]) -> None:
        for alert in alerts:
            self.tsunamis = [item for item in self.tsunamis if item.key != alert.key]
            self.tsunamis.insert(0, alert)
        self.tsunamis = self.tsunamis[:20]

    async def _dispatch_weather(self, alert: china_logic.WeatherAlert,
                                config: dict[str, Any]) -> bool:
        if alert.key in self.state['seen']:
            return False
        state_mod.mark_seen(self.state, alert.key)
        tone, identity = tone_mod.tone_for('weather', alert, self.state['seen'])
        if identity:
            state_mod.mark_seen(self.state, identity)
        self._record_weather(alert, 'filtered', tone)
        if not china_logic.within_weather_filters(alert, config):
            return False
        if not self._targets(config):
            return False
        text = china_logic.format_weather(alert, config, tone)
        image = await self._render_china('weather', alert, config, tone)
        sent = await self._push(text, config, image, event_key=alert.key)
        self._record_weather(alert, 'pushed' if sent else 'failed', tone)
        if sent:
            state_mod.increment_pushed(self.state)
            return True
        return False

    async def _dispatch_tsunami(self, alert: china_logic.TsunamiAlert,
                                config: dict[str, Any]) -> bool:
        if alert.key in self.state['seen']:
            return False
        state_mod.mark_seen(self.state, alert.key)
        tone, identity = tone_mod.tone_for('tsunami', alert, self.state['seen'])
        if identity:
            state_mod.mark_seen(self.state, identity)
        self._record_tsunami(alert, 'filtered', tone)
        if not china_logic.within_tsunami_filters(alert, config):
            return False
        if not self._targets(config):
            return False
        text = china_logic.format_tsunami(alert, config, tone)
        image = await self._render_china('tsunami', alert, config, tone)
        sent = await self._push(text, config, image, event_key=alert.key)
        self._record_tsunami(alert, 'pushed' if sent else 'failed', tone)
        if sent:
            state_mod.increment_pushed(self.state)
            return True
        return False

    async def poll_typhoons(self, source: Any | None = None) -> int:
        config = self.reload_config()
        if not self._china_enabled(config, 'eqsc'):
            return 0
        token = str(((config.get('sources') or {}).get('eqsc') or {}).get('token') or '').strip()
        own = source is None
        if own:
            if not token:
                return 0
            source = self._create_eqsc(token)
        try:
            items = await source.fetch_typhoons()
        except Exception as exc:
            state_mod.record_poll(self.state, 'eqsc_typhoon', error=f'{type(exc).__name__}: {exc}')
            logger.warning(f'[disaster] EQSC 台风拉取失败: {exc}')
            await self._save_state()
            return 0
        finally:
            if own:
                await _close_source(source)

        parsed = china_logic.parse_eqsc_typhoon_list(items)
        self.typhoons = parsed
        state_mod.record_poll(self.state, 'eqsc_typhoon')
        if not parsed:
            await self._save_state()
            return 0
        if self._eqsc_baseline:
            self._eqsc_baseline = False
            self.state['initialized']['eqsc_typhoon'] = True
            for typhoon in parsed:
                if typhoon.is_active:
                    self._eqsc_active[typhoon.typhoon_id] = typhoon
                    state_mod.mark_seen(self.state, typhoon.key)
            await self._save_state()
            return 0

        current = {item.typhoon_id: item for item in parsed if item.is_active}
        sent = 0
        for typhoon in current.values():
            if await self._dispatch_typhoon(typhoon, config):
                sent += 1
            self._eqsc_active[typhoon.typhoon_id] = typhoon
        for typhoon_id, previous in list(self._eqsc_active.items()):
            if typhoon_id not in current:
                if await self._dispatch_typhoon_stopped(previous, config):
                    sent += 1
                del self._eqsc_active[typhoon_id]
        await self._save_state()
        return sent

    async def _dispatch_typhoon(self, typhoon: china_logic.Typhoon,
                                config: dict[str, Any]) -> bool:
        if typhoon.key in self.state['seen']:
            return False
        state_mod.mark_seen(self.state, typhoon.key)
        tone, identity = tone_mod.tone_for('typhoon', typhoon, self.state['seen'])
        if identity:
            state_mod.mark_seen(self.state, identity)
        self._record_typhoon(typhoon, 'filtered', tone)
        if not china_logic.within_typhoon_filters(typhoon, config):
            return False
        if not self._targets(config):
            return False
        text = china_logic.format_typhoon(typhoon, config, tone)
        image = await self._render_china('typhoon', typhoon, config, tone)
        sent = await self._push(text, config, image, event_key=typhoon.key)
        self._record_typhoon(typhoon, 'pushed' if sent else 'failed', tone)
        if sent:
            state_mod.increment_pushed(self.state)
            return True
        return False

    async def _dispatch_typhoon_stopped(self, typhoon: china_logic.Typhoon,
                                        config: dict[str, Any]) -> bool:
        if typhoon.stop_key in self.state['seen']:
            return False
        state_mod.mark_seen(self.state, typhoon.stop_key)
        tone = tone_mod.subsequent_tone()
        self._record_typhoon_stop(typhoon, 'filtered', tone)
        if not china_logic.within_typhoon_filters(typhoon, config):
            return False
        if not self._targets(config):
            return False
        text = china_logic.format_typhoon_stopped(typhoon, config, tone)
        image = await self._render_china('typhoon', typhoon, config, tone, stopped=True)
        sent = await self._push(text, config, image, event_key=typhoon.stop_key)
        self._record_typhoon_stop(typhoon, 'pushed' if sent else 'failed', tone)
        if sent:
            state_mod.increment_pushed(self.state)
            return True
        return False

    def _cache(self, kind: str, events: list[logic.EarthquakeEvent]) -> None:
        if kind == 'cenc_list':
            self.lists['cenc'] = events
        elif kind == 'jma_list':
            self.lists['jma'] = events
        else:
            for event in events:
                self.latest = [item for item in self.latest if item.key != event.key]
                self.latest.insert(0, event)
            self.latest = self.latest[:10]

    def _record_quake(self, event: logic.EarthquakeEvent, status: str,
                      tone: str = 'neutral', detail: str = '') -> None:
        self.store.record_event(
            event_key=event.key, kind='earthquake', status=status,
            source=event.source, event_id=event.event_id,
            report_num=event.report_num, title=event.title,
            place=event.place or '', magnitude=event.magnitude,
            occurred_at=event.occurred_at or '', tone=tone, detail=detail,
        )

    def _record_weather(self, alert: china_logic.WeatherAlert, status: str,
                        tone: str = 'neutral') -> None:
        region = ''.join(part for part in (alert.province, alert.city, alert.district) if part)
        self.store.record_event(
            event_key=alert.key, kind='weather', status=status, source='jian',
            event_id=alert.alert_id, title=alert.title or alert.headline,
            place=region, occurred_at=alert.effective_at or '', tone=tone,
            detail=f'{alert.color}/{alert.code}',
        )

    def _record_tsunami(self, alert: china_logic.TsunamiAlert, status: str,
                        tone: str = 'neutral') -> None:
        self.store.record_event(
            event_key=alert.key, kind='tsunami', status=status, source='jian',
            event_id=alert.alert_id, report_num=alert.number, title=alert.title,
            place=alert.place or '', magnitude=alert.magnitude,
            occurred_at=alert.origin_time or '', tone=tone, detail=alert.level,
        )

    def _record_typhoon(self, typhoon: china_logic.Typhoon, status: str,
                        tone: str = 'neutral') -> None:
        self.store.record_event(
            event_key=typhoon.key, kind='typhoon', status=status, source='eqsc',
            event_id=typhoon.typhoon_id, title=typhoon.display_name,
            occurred_at=typhoon.updated_at or '', tone=tone,
            detail=typhoon.level,
        )

    def _record_typhoon_stop(self, typhoon: china_logic.Typhoon, status: str,
                             tone: str = 'neutral') -> None:
        self.store.record_event(
            event_key=typhoon.stop_key, kind='typhoon_stop', status=status,
            source='eqsc', event_id=typhoon.typhoon_id,
            title=f'{typhoon.typhoon_id} {typhoon.display_name} 停编',
            occurred_at=typhoon.updated_at or '', tone=tone, detail='停编',
        )

    async def _dispatch(self, event: logic.EarthquakeEvent, config: dict[str, Any]) -> bool:
        key = event.key
        if key in self.state['seen']:
            return False
        # 先记为已处理：过滤掉/没配目标/推送失败都不再回补，避免重启后补发旧事件。
        state_mod.mark_seen(self.state, key)
        tone = tone_mod.earthquake_tone(event)
        self._record_quake(event, 'filtered', tone)
        if event.is_training or event.is_cancel:
            reason = '训练报' if event.is_training else '取消报'
            self._record_quake(event, 'ignored', tone, reason)
            return False
        if not logic.within_filters(event, config):
            return False
        # JMA 一分钟内可能十几报；第一报与最终报推，中间报不刷屏。
        if event.source == 'jma_eew' and event.report_num > 1 and not event.is_final:
            self._record_quake(event, 'ignored', tone, 'JMA 中间报')
            return False
        if not self._targets(config):
            return False
        text = logic.format_event(event, config, tone)
        image = await self._render_image(event, config, tone)
        sent = await self._push(text, config, image, event_key=key)
        self._record_quake(event, 'pushed' if sent else 'failed', tone)
        if sent:
            state_mod.increment_pushed(self.state)
        return sent

    async def _render_image(self, event: logic.EarthquakeEvent,
                            config: dict[str, Any], tone: str = 'neutral') -> bytes | None:
        if self._render_override is not None:
            return await self._render_override(event, config, tone)
        try:
            return await render_mod.render_event(event, config, tone)
        except Exception as exc:
            logger.warning(f'[disaster] 卡片渲染失败，回退文字: {exc}')
            return None

    async def _render_china(self, kind: str, item: Any, config: dict[str, Any],
                            tone: str = 'neutral', stopped: bool = False) -> bytes | None:
        try:
            return await render_mod.render_bulletin(kind, item, config, tone, stopped)
        except Exception as exc:
            logger.warning(f'[disaster] {kind} 卡片渲染失败，回退文字: {exc}')
            return None

    async def _push(self, text: str, config: dict[str, Any],
                    image: bytes | None = None, *, event_key: str = '') -> bool:
        if self._push_override is not None:
            return bool(await self._push_override(text, config, image))
        targets = self._targets(config)
        bots = get_bots()
        if not bots or not targets:
            return False
        bot = next(iter(bots.values()))
        message = (MessageSegment.image(image) + '\n' + text) if image else text
        sent = False
        for kind, target in targets:
            ok = True
            error = ''
            try:
                await self._send(bot, kind, target, message)
            except Exception as exc:
                ok = False
                error = f'{type(exc).__name__}: {exc}'
                logger.warning(f'[disaster] 推送 {kind}:{target} 失败: {exc}')
            if event_key:
                self.store.record_push(
                    event_key, target_type=kind, target=target, ok=ok, error=error,
                )
            sent = sent or ok
        return sent

    @staticmethod
    async def _send(bot, kind: str, target: str, message) -> None:
        if kind == 'group':
            await bot.send_group_msg(group_id=int(target), message=message)
        else:
            await bot.send_private_msg(user_id=int(target), message=message)

    def status(self) -> dict[str, Any]:
        sources = self.config.get('sources') or {}
        jian = sources.get('jian') or {}
        eqsc = sources.get('eqsc') or {}
        last_poll = dict(self.state['last_poll'])
        return {
            'enabled': bool(self.config.get('enabled')),
            'earthquake_free': bool(
                (self.config.get('sources') or {}).get('earthquake_free', {}).get('enabled')
            ),
            'targets': self._targets(self.config),
            'last_poll': last_poll,
            'errors': dict(self.state['errors']),
            'cenc': len(self.lists['cenc']),
            'jma': len(self.lists['jma']),
            'latest': len(self.latest),
            'weather': len(self.weather),
            'tsunami': len(self.tsunamis),
            'typhoons': len(self.typhoons),
            'jian': {
                'enabled': bool(jian.get('enabled')),
                'configured': bool(str(jian.get('token') or '').strip()),
                'weather': len(self.weather),
                'tsunami': len(self.tsunamis),
                'last_weather': last_poll.get('jian_weather'),
                'last_tsunami': last_poll.get('jian_tsunami'),
            },
            'eqsc': {
                'enabled': bool(eqsc.get('enabled')),
                'configured': bool(str(eqsc.get('token') or '').strip()),
                'active': sum(1 for item in self.typhoons if item.is_active),
                'cached': len(self.typhoons),
                'last_poll': last_poll.get('eqsc_typhoon'),
            },
            'pushed': int(self.state['counts'].get('pushed', 0)),
            'history': self.store.stats(7),
        }


def _format_status(runtime: Runtime) -> str:
    status = runtime.status()
    targets = status['targets']
    target_text = '、'.join(
        ('群 ' + value) if kind == 'group' else ('主人 ' + value)
        for kind, value in targets
    ) or '未配置'
    if status['last_poll']:
        poll_text = ' / '.join(
            f'{name} {time.strftime("%H:%M:%S", time.localtime(value))}'
            for name, value in status['last_poll'].items()
        )
    else:
        poll_text = '尚未成功'
    history = status['history']['windows']
    h24, h7 = history['24h'], history['7d']
    lines = [
        '地震源：Wolfx 免鉴权（CENC EEW/列表 + JMA EEW/情报）',
        f"开关：总开关{'开' if status['enabled'] else '关'}，"
        f"地震附加{'开' if status['earthquake_free'] else '关'}",
        f'推送：{target_text}',
        f'最近轮询：{poll_text}',
        f"缓存：CENC {status['cenc']} 条 / JMA {status['jma']} 条；"
        f"最近预警 {status['latest']} 条；累计推送 {status['pushed']} 条",
        f"事件库：24h {h24['events']} 条 / 推送 {h24['pushed']}；"
        f"7d {h7['events']} 条 / 推送 {h7['pushed']}",
    ]
    if status['errors']:
        lines.append('最近错误：' + '；'.join(
            f'{name}: {message}' for name, message in status['errors'].items()
        ))
    return '\n'.join(lines)


def _format_china_status(runtime: Runtime) -> str:
    status = runtime.status()
    targets = status['targets']
    target_text = '、'.join(
        ('群 ' + value) if kind == 'group' else ('主人 ' + value)
        for kind, value in targets
    ) or '未配置'
    jian = status['jian']
    eqsc = status['eqsc']
    history = status['history']['windows']
    h24, h7 = history['24h'], history['7d']
    lines = [
        '灾害源状态：',
        f"总开关：{'开' if status['enabled'] else '关'}；推送：{target_text}",
        f"Jian：{'开' if jian['enabled'] else '关'}，"
        f"令牌{'已配置' if jian['configured'] else '未配置'}；"
        f"气象 {jian['weather']} 条 / 海啸 {jian['tsunami']} 条",
        f"EQSC：{'开' if eqsc['enabled'] else '关'}，"
        f"令牌{'已配置' if eqsc['configured'] else '未配置'}；"
        f"活跃台风 {eqsc['active']} 个 / 缓存 {eqsc['cached']} 个",
        f"Wolfx 地震：CENC {status['cenc']} 条 / JMA {status['jma']} 条；"
        f"最近预警 {status['latest']} 条；累计推送 {status['pushed']} 条",
        f"事件库：24h {h24['events']} 条 / 推送 {h24['pushed']}；"
        f"7d {h7['events']} 条 / 推送 {h7['pushed']}",
    ]
    polls = status['last_poll']
    if polls:
        lines.append('最近轮询：' + ' / '.join(
            f'{name} {time.strftime("%H:%M:%S", time.localtime(value))}'
            for name, value in polls.items()
        ))
    else:
        lines.append('最近轮询：尚未成功')
    if status['errors']:
        lines.append('最近错误：' + '；'.join(
            f'{name}: {message}' for name, message in status['errors'].items()
        ))
    return '\n'.join(lines)


_runtime: Runtime | None = None


@get_driver().on_startup
async def _startup() -> None:
    global _runtime
    _runtime = Runtime()
    _runtime.prepare()
    await _runtime.start()


@get_driver().on_shutdown
async def _shutdown() -> None:
    global _runtime
    if _runtime is not None:
        await _runtime.stop()
        _runtime = None


@matcher.handle()
async def _handle_command(event: Event) -> None:
    parsed = _parse_command(event.get_plaintext())
    if not parsed:
        return
    command, args = parsed
    if command in ('地震源状态', '灾害源状态', '地震测试') and not _is_admin(event):
        return
    runtime = _runtime
    if runtime is None:
        await matcher.finish('灾害预警服务尚未启动。')

    if command == '地震列表':
        source, limit = _parse_list_args(args)
        await matcher.finish(logic.format_list(runtime.lists.get(source) or [], limit))

    if command == '地震预警':
        eews = [event for event in runtime.latest if event.source.endswith('_eew')]
        if not eews:
            await matcher.finish('最近没有收到地震预警。')
        await matcher.finish('\n\n'.join(
            logic.format_event(item, runtime.config) for item in eews[:2]
        ))

    if command == '地震源状态':
        await matcher.finish(_format_status(runtime))

    if command == '气象预警':
        limit = _parse_limit(args, 3)
        config = runtime.reload_config()
        alerts = [item for item in runtime.weather
                  if china_logic.within_weather_filters(item, config)]
        if not alerts:
            await matcher.finish('最近没有符合当前地区和等级设置的气象预警。')
        await matcher.finish('\n\n'.join(
            china_logic.format_weather(item, config) for item in alerts[:limit]
        ))

    if command == '台风':
        active = [item for item in runtime.typhoons if item.is_active]
        if not active:
            await matcher.finish('最近没有活跃台风。')
        await matcher.finish('\n\n'.join(
            china_logic.format_typhoon(item, runtime.config) for item in active[:5]
        ))

    if command == '海啸':
        limit = _parse_limit(args, 2)
        if not runtime.tsunamis:
            await matcher.finish('最近没有收到海啸预警。')
        await matcher.finish('\n\n'.join(
            china_logic.format_tsunami(item, runtime.config) for item in runtime.tsunamis[:limit]
        ))

    if command == '灾害源状态':
        await matcher.finish(_format_china_status(runtime))

    if command == '地震测试':
        await matcher.finish(logic.format_event(logic.sample_event(), runtime.config))
