"""游戏活动：NoneBot commands and the sole calendar delivery worker.

Original implementation; behavior reference is recorded in docs/research.
Importing this module never opens a database, downloads data, or sends messages.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

from nonebot import get_bots, get_driver, logger, on_message
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed, ApiNotAvailable, NetworkError
from muicebot.plugin import PluginMetadata

import gate_logic as gl
import game_event_logic as logic
from game_event_store import Store, ConflictError
from game_event_service import GameEventService, batches
from game_event_sources import SourceClient
from game_event_message import MessageBuilder

__plugin_meta__ = PluginMetadata(name='game_events', description='游戏活动日历与分级提醒', usage='活动游戏 / 活动订阅 鸣潮 / 活动查询')
COMMANDS = logic.COMMANDS
_USAGE = {
    'subscribe': '<游戏…>', 'unsubscribe': '<游戏… | 全部>', 'query': '[游戏…]',
    'filter': '查看 | 包含 <词…> | 排除 <词…> | 清空',
    'cover': '图片 | 链接 | 关闭 | 默认', 'toggle': '开 | 关',
    'threshold': '<天数… | 默认 | 关闭>', 'refresh': '[数据 | 推送] [游戏…]',
    'dm': '自己 [游戏…] | 停止 | 测试 | 列表', 'ack': '[活动名或编号]',
}
for _name, (_action, *_aliases) in COMMANDS.items():
    _permission = ('仅主人' if _action in ('status', 'refresh') else
                   '群配置仅群主/管理员/主人；私聊仅自己' if _action in ('subscribe', 'unsubscribe', 'filter', 'cover', 'toggle', 'threshold') else
                   '仅管理自己的提醒；主人可指定其他目标' if _action == 'dm' else '当前会话')
    gl.register_help_rows([(_name + (' ' + _USAGE[_action] if _action in _USAGE else ''),
                            _permission + '；别名：' + '、'.join('.' + x for x in _aliases))])

ROOT = Path(__file__).resolve().parents[1]
store = Store()
_service = None
_sources = None
_builder = None
_task = None
_instance_lock = None
_recent = {}


class DeliveryBlocked(RuntimeError):
    blocked = True


class InstanceLock:
    """OS-owned lock; a dead process releases it without stale PID recovery."""
    def __init__(self, path):
        self.path, self.file = Path(path), None

    def acquire(self):
        if self.file:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.file = handle
        return True

    def close(self):
        if self.file:
            self.file.close()
            self.file = None


def session(event):
    return ('group', str(event.group_id)) if isinstance(event, GroupMessageEvent) else ('private', str(event.get_user_id()))


def is_admin(event):
    return str(event.get_user_id()) in {str(x) for x in (get_driver().config.superusers or ())}


def can_edit(event):
    return isinstance(event, PrivateMessageEvent) or is_admin(event) or getattr(event.sender, 'role', '') in ('owner', 'admin')


def _throttle(keys, seconds):
    now = time.monotonic()
    if any(now - _recent.get(key, -seconds) < seconds for key in keys):
        return True
    if len(_recent) > 4000:
        for key in list(_recent):
            if now - _recent[key] > 120:
                del _recent[key]
    for key in keys:
        _recent[key] = now
    return False


def _games(text):
    return logic.resolve_games([x for x in re.split(r'[,，\s]+', text.strip()) if x])


def _names(games):
    return '、'.join(logic.GAMES[x]['name'] for x in games) or '无'


def _subscription(bot_id, kind, target):
    return next((s for s in store.subscriptions() if (s['bot_id'], s['kind'], s['target_id']) == (bot_id, kind, target)), None)


async def _plain(bot, kind, target, text, valid=lambda: True):
    if not valid() or not logic.allowed(store.settings(), kind, target):
        raise DeliveryBlocked('会话已被策略暂停')
    message = Message(MessageSegment.text(str(text)))
    call = bot.send_group_msg(group_id=int(target), message=message) if kind == 'group' else bot.send_private_msg(user_id=int(target), message=message)
    try:
        await asyncio.wait_for(call, 20)
    except NetworkError as error:
        raise TimeoutError('投递回执未知') from error


async def send_events(*, bot_id, kind, target_id, events, title, mode, settings, valid):
    bot = get_bots().get(str(bot_id))
    if bot is None or _builder is None:
        raise DeliveryBlocked('指定机器人不在线')
    built = await _builder.build(events, title=title, mode=mode, settings=settings)
    def message(parts):
        return Message([MessageSegment(type=part['type'], data=part['data']) for part in parts])

    async def deliver(message, forward=False):
        if not valid() or not logic.allowed(store.settings(), kind, target_id):
            raise DeliveryBlocked('会话或订阅已变化')
        if forward:
            args = {'group_id' if kind == 'group' else 'user_id': int(target_id), 'messages': message}
            call = bot.send_group_forward_msg(**args) if kind == 'group' else bot.send_private_forward_msg(**args)
        else:
            args = {'group_id' if kind == 'group' else 'user_id': int(target_id), 'message': message}
            call = bot.send_group_msg(**args) if kind == 'group' else bot.send_private_msg(**args)
        try:
            await asyncio.wait_for(call, 20)
        except NetworkError as error:
            raise TimeoutError('投递回执未知') from error

    try:
        if settings.get('use_forward') and built.get('nodes'):
            content = [[{'type': 'text', 'data': {'text': title}}], *built['nodes']]
            nodes = [{'type': 'node', 'data': {'name': '游戏活动', 'uin': str(bot_id), 'content': message(parts)}} for parts in content]
            await deliver(nodes, forward=True)
        else:
            await deliver(message(built['parts']))
    except (ActionFailed, ApiNotAvailable):
        # Only an explicit rejection permits one same-round fallback.
        await deliver(Message(MessageSegment.text(built['fallback'])))


async def _rule(event: Event):
    return isinstance(event, (GroupMessageEvent, PrivateMessageEvent)) and logic.parse_command(event.get_plaintext()) is not None


matcher = on_message(rule=_rule, priority=3, block=True)


class CommandDenied(Exception):
    pass


async def _dm(bot, event, rest, sub):
    bot_id, user = str(bot.self_id), str(event.get_user_id())
    kind, target = session(event)
    tokens = rest.split(maxsplit=1)
    command = tokens[0] if tokens else '列表'
    args = tokens[1] if len(tokens) > 1 else ''
    mentions = [str(x.data.get('qq', '')) for x in event.message if x.type == 'at' and str(x.data.get('qq', '')) != bot_id]
    selected = mentions[0] if mentions else user
    if mentions and not is_admin(event):
        raise CommandDenied
    if mentions and (len(mentions) != 1 or not selected.isdecimal()):
        raise ValueError('请指定一个有效的私聊目标')
    if command == '移除' and not is_admin(event):
        raise CommandDenied
    old = store.urgent_target(bot_id, selected)
    if command in ('停止', '移除'):
        if old:
            if command == '移除':
                store.delete_urgent(bot_id, selected, old['revision'])
            else:
                store.save_urgent({'bot_id': bot_id, 'user_id': selected, 'enabled': False}, old['revision'])
        return '已停止该目标的活动私人提醒。'
    if command == '测试':
        if mentions:
            raise ValueError('测试只向执行者本人发送')
        if _throttle([('dmtest', bot_id, user)], 60):
            raise ValueError('私人提醒测试每60秒一次')
        await _plain(bot, 'private', user, '游戏活动私人提醒测试：投递成功。',
                     valid=lambda: logic.allowed(store.settings(), kind, target))
        return '已向你发送私人提醒测试。'
    if command == '列表':
        if mentions:
            raise ValueError('其他目标的详细配置请在管理面板查看')
        info = f"私人提醒：{'开启' if old and old['enabled'] else '关闭'}\n游戏：{_names(old['games']) if old else '无'}"
        await _plain(bot, 'private', user, info, valid=lambda: logic.allowed(store.settings(), kind, target))
        return '已私聊发送你的提醒设置。' if kind == 'group' else None
    if command not in ('自己', '开启', '添加') and not mentions:
        raise ValueError('用法：.活动私人提醒 自己 [游戏…] / 停止 / 测试 / 列表')
    game_text = args if command in ('自己', '开启', '添加') else rest
    games = _games(game_text) if game_text else (sub or {}).get('games', [])
    if mentions and not game_text:
        raise ValueError('指定他人时必须明确游戏范围')
    if not games:
        raise ValueError('请明确游戏范围，例如：.活动私人提醒 自己 鸣潮')
    saved = store.save_urgent({'bot_id': bot_id, 'user_id': selected, 'games': games, 'enabled': True,
                              'origin_session': f'{kind}:{target}',
                              'include_keywords': (sub or {}).get('include_keywords', []),
                              'exclude_keywords': (sub or {}).get('exclude_keywords', [])}, old['revision'] if old else None)
    suffix = '全局紧急提醒尚未开启，请由主人在管理面板开启。' if not store.settings()['urgent_enabled'] else '将按静默时间、间隔和每日限额发送。'
    return f"私人提醒目标：{selected}\n游戏：{_names(saved['games'])}\n{suffix}\n群推送开关不影响独立目标；来源群被名单禁用时暂停。"


@matcher.handle()
async def handle(bot: Bot, event: Event):
    parsed = logic.parse_command(event.get_plaintext())
    if not parsed or not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
        return
    action, rest = parsed
    kind, target = session(event)
    bot_id, user = str(bot.self_id), str(event.get_user_id())
    permitted = logic.allowed(store.settings(), kind, target)
    try:
        # An access restriction must not trap a user in an existing reminder.
        if action == 'ack':
            names = store.ack_urgent(bot_id, user, rest, time.time())
            answer = f'已确认{len(names)}项活动。' if names else '没有匹配的待确认活动。'
        elif action == 'dm' and rest == '停止' and not any(x.type == 'at' and str(x.data.get('qq')) != bot_id for x in event.message):
            old = store.urgent_target(bot_id, user)
            if old:
                store.save_urgent({'bot_id': bot_id, 'user_id': user, 'enabled': False}, old['revision'])
            answer = '已停止你的活动私人提醒。'
        elif not permitted:
            return
        else:
            sub = _subscription(bot_id, kind, target)
            if action in ('status', 'refresh') and not is_admin(event):
                return
            if action in ('subscribe', 'unsubscribe', 'filter', 'cover', 'toggle', 'threshold') and not can_edit(event):
                return
            if action == 'games':
                answer = '支持游戏：\n' + '\n'.join(f'{code} · {game["name"]}' for code, game in logic.GAMES.items())
            elif action == 'list':
                answer = '当前会话订阅：' + _names((sub or {}).get('games', []))
                if sub:
                    answer += f"\n推送：{'开启' if sub['enabled'] else '暂停'}；阈值：{sub['thresholds'] if sub['thresholds'] is not None else '继承全局'}"
            elif action == 'query':
                games = _games(rest) if rest else (sub or {}).get('games', [])
                if not games:
                    raise ValueError('请先订阅或指定游戏，例如：.活动查询 鸣潮')
                if _throttle([('query-user', bot_id, user), ('query-session', bot_id, kind, target)], 10):
                    raise ValueError('查询太频繁，请10秒后再试。')
                if _service is None:
                    raise ValueError('活动服务未启动，请在面板检查运行状态。')
                snapshots = await _service.query(games, sub)
                for snap in snapshots:
                    title = logic.GAMES[snap['game_id']]['name'] + ' · 活动查询'
                    if snap.get('stale') or snap.get('error'):
                        from datetime import datetime
                        checked = snap.get('checked_at', 0)
                        title += '\n缓存核验：' + (datetime.fromtimestamp(checked, logic.TZ).strftime('%Y-%m-%d %H:%M') if checked else '无')
                        title += '\n' + snap.get('error', '使用缓存')[:200]
                    activities = snap.get('activities', [])
                    if not activities:
                        await _plain(bot, kind, target, title + '\n当前可用数据中没有符合条件的活动。')
                    groups = list(batches(activities[:30]))
                    for i, batch in enumerate(groups):
                        heading = title + (f'\n另有{len(activities)-30}项，可在面板查看。' if len(activities) > 30 and i == len(groups)-1 else '')
                        policy = _service.policy(sub or {}, store.settings())
                        await send_events(bot_id=bot_id, kind=kind, target_id=target, events=batch, title=heading,
                                          mode=policy['cover_mode'], settings=policy,
                                          valid=lambda: logic.allowed(store.settings(), kind, target))
                return
            elif action in ('subscribe', 'unsubscribe'):
                games = [] if rest == '全部' and action == 'unsubscribe' else _games(rest)
                if not games and not (action == 'unsubscribe' and rest == '全部'):
                    raise ValueError('请指定游戏，退订全部使用：.活动退订 全部')
                current = (sub or {}).get('games', [])
                new = list(dict.fromkeys(current + games)) if action == 'subscribe' else ([] if rest == '全部' else [x for x in current if x not in games])
                if not sub and action == 'unsubscribe':
                    answer = '当前会话没有订阅。'
                else:
                    saved = store.save_subscription({'id': sub['id'], 'games': new} if sub else
                                                    {'bot_id': bot_id, 'kind': kind, 'target_id': target, 'games': new}, sub['revision'] if sub else None)
                    answer = '当前订阅：' + _names(saved['games']) + '\n新增游戏将由后台初始化；数据不可用时保留订阅等待重试。'
            elif action in ('filter', 'cover', 'toggle', 'threshold'):
                if not sub:
                    raise ValueError('请先使用 .活动订阅 <游戏>。')
                patch = {'id': sub['id']}
                if action == 'filter':
                    command, _, words = rest.partition(' ')
                    if command in ('', '查看'):
                        await _plain(bot, kind, target, f"包含：{', '.join(sub['include_keywords']) or '不限'}\n排除：{', '.join(sub['exclude_keywords']) or '无'}")
                        return
                    if command == '清空':
                        patch.update(include_keywords=[], exclude_keywords=[])
                    elif command in ('包含', '排除'):
                        patch['include_keywords' if command == '包含' else 'exclude_keywords'] = [x for x in re.split(r'[,，\s]+', words) if x]
                    else:
                        raise ValueError('用法：.活动过滤 查看 / 包含 <词…> / 排除 <词…> / 清空')
                elif action == 'cover':
                    values = {'图片': 'image', '链接': 'link', '关闭': 'off', '默认': None, 'image': 'image', 'link': 'link', 'off': 'off', 'default': None}
                    if rest not in values:
                        raise ValueError('封面可选：图片 / 链接 / 关闭 / 默认')
                    patch['cover_mode'] = values[rest]
                elif action == 'toggle':
                    if rest not in ('开', '关', '开启', '关闭', 'on', 'off'):
                        raise ValueError('用法：.活动开关 开 / 关')
                    patch['enabled'] = rest in ('开', '开启', 'on')
                else:
                    if not rest:
                        raise ValueError('用法：.活动阈值 7 3 1 / 默认 / 关闭')
                    patch['thresholds'] = logic.thresholds(rest)
                store.save_subscription(patch, sub['revision'])
                answer = '当前会话的活动设置已保存。'
            elif action == 'status':
                answer = '活动源状态：\n' + '\n'.join(f"{s['game_id']}：{'缓存/异常' if s.get('stale') else '成功'} {s.get('error', '')[:150]}" for s in store.snapshots())
                answer += f"\n订阅会话：{len(store.subscriptions())}；紧急目标：{len(store.urgent_targets())}"
            elif action == 'refresh':
                tokens = rest.split(maxsplit=1)
                mode = 'check' if tokens and tokens[0] == '推送' else 'data'
                if tokens and tokens[0] in ('推送', '数据'):
                    rest = tokens[1] if len(tokens) > 1 else ''
                games = _games(rest) if rest else list(logic.GAMES)
                job = store.request_refresh(mode=mode, games=games, bot_id=bot_id)
                answer = f"已排队：{'检查并推送' if job['mode'] == 'check' else '仅刷新数据'}；状态 {job['status']}。"
            elif action == 'dm':
                answer = await _dm(bot, event, rest, sub)
            else:
                answer = '请使用 .活动游戏 查看游戏，或 .help 查看命令。'
        if answer and permitted:
            await _plain(bot, kind, target, answer)
    except (DeliveryBlocked, CommandDenied):
        return
    except (ValueError, ConflictError) as error:
        if permitted:
            try:
                await _plain(bot, kind, target, str(error))
            except DeliveryBlocked:
                pass
    except Exception as error:
        logger.warning(f'[game-events] command failed: {type(error).__name__}')


async def worker():
    last_poll = float('-inf')
    next_urgent = next_init = next_prune = 0.0
    while True:
        try:
            now = time.monotonic()
            store.set_runtime(list(get_bots()), time.time())
            job = store.claim_job()
            if job:
                try:
                    result = await _service.cycle(mode=job['mode'], games=job['games'], bot_id=job['bot_id'], force=True)
                    store.finish_job(job['id'], result=result)
                except Exception as error:
                    store.finish_job(job['id'], error=type(error).__name__)
            if now - last_poll >= store.settings()['poll_minutes'] * 60:
                await _service.cycle()
                last_poll = now
                next_init = now + 60
            elif now >= next_init:
                for sub in store.subscriptions():
                    if any(store.game_state(sub['id'], game)['status'] == 'initializing' for game in sub['games']):
                        await _service.cycle(subscription_id=sub['id'])
                next_init = now + 60
            if now >= next_urgent:
                await _service.urgent_cycle()
                next_urgent = now + 60
            if now >= next_prune:
                store.prune(time.time())
                next_prune = now + 3600
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(f'[game-events] worker failed: {type(error).__name__}')
        await asyncio.sleep(5)


@get_driver().on_startup
async def _start():
    global _task, _instance_lock, _sources, _builder, _service
    if _task and not _task.done():
        return
    _instance_lock = InstanceLock(ROOT / 'data' / 'game_events' / 'worker.lock')
    if not _instance_lock.acquire():
        logger.error('[game-events] 已有实例持有调度锁，本进程不启用活动服务')
        return
    try:
        store.settings()  # Fail closed on corrupt/unsupported databases.
        _sources = SourceClient(ROOT / 'data' / 'game_events' / 'sources')
        _builder = MessageBuilder(ROOT / 'data' / 'game_events' / 'covers',
                                  dns_mode=getattr(get_driver().config, 'game_event_cover_dns', None))
        _service = GameEventService(store, _sources, send_events, online=lambda ident: ident in get_bots())
        _task = asyncio.create_task(worker())
    except Exception as error:
        await _stop()
        logger.error(f'[game-events] 服务初始化失败：{type(error).__name__}')


@get_driver().on_shutdown
async def _stop():
    global _task, _service, _sources, _builder, _instance_lock
    if _task:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
        _task = None
    for client in (_sources, _builder):
        if client:
            await client.close()
    _service = _sources = _builder = None
    if _instance_lock:
        _instance_lock.close()
        _instance_lock = None
