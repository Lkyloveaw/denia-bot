# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。
"""Epic 喜加一：查询命令、换期推送、超管预览与面板配置。

只做接线：判定/格式化在 `epic_free_logic`，抓取/状态在 `epic_free_service`，
配置与跨进程触发在 `epic_free_config`，卡片在 `epic_free_image`。
"""

from __future__ import annotations

import asyncio
import re
import time

from nonebot import get_bots, get_driver, logger, on_message
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from muicebot.plugin import PluginMetadata

import epic_free_config as config_store
import epic_free_logic as logic
import epic_free_service as service
import gate_logic as gl

__plugin_meta__ = PluginMetadata(
    name='epic_free',
    description='Epic 喜加一提醒',
    usage='喜加一 / Epic 测试',
)

gl.register_help_rows([
    ('喜加一', '本周 Epic 免费游戏（仅主人/群主/群管理员）'),
    ('Epic 测试', '超管：立即抓取并预览一次（仅主人）'),
])

_FREE = re.compile(r'^\s*[./]?\s*喜加一\s*$')
_TEST = re.compile(r'^\s*[./]?\s*epic\s+测试\s*$', re.I)
_recent: dict[str, float] = {}


def parse(text):
    text = str(text or '').strip()
    if _FREE.fullmatch(text):
        return 'free'
    if _TEST.fullmatch(text):
        return 'test'
    return None


async def _rule(event: Event) -> bool:
    return parse(event.get_plaintext()) is not None


matcher = on_message(rule=_rule, priority=3, block=True)


def _is_admin(event: Event) -> bool:
    supers = {str(user) for user in (getattr(get_driver().config, 'superusers', None) or ())}
    return str(event.get_user_id()) in supers


def _sender_role(event: Event) -> str:
    sender = getattr(event, 'sender', None)
    return str(getattr(sender, 'role', '') or '')


def _is_group_staff(event: Event) -> bool:
    return isinstance(event, GroupMessageEvent) and _sender_role(event) in ('owner', 'admin')


def _can_query(event: Event) -> bool:
    """主动查询：超管，或群主/群管理员；普通成员与非超管私聊拒绝。"""
    return _is_admin(event) or _is_group_staff(event)


def _pick_bot() -> Bot | None:
    bots = get_bots()
    return next(iter(bots.values()), None)


def _throttled(key: str) -> bool:
    now = time.monotonic()
    if now - _recent.get(key, 0) < 10:
        return True
    _recent[key] = now
    if len(_recent) > 1000:
        _recent.clear()
    return False


async def _fetch(config):
    return await service.fetch_view(
        country=config.get('country') or 'CN',
        locale=config.get('locale') or 'zh-CN',
    )


async def _message(view, config):
    free = view.get('free') or []
    upcoming = (view.get('upcoming') or []) if config.get('include_upcoming', True) else []
    if config.get('card_enabled', True):
        try:
            import epic_free_image
            from card_delivery import image_message
            pages = await asyncio.to_thread(epic_free_image.render_pages, free, upcoming)
            return image_message(pages)
        except Exception as error:
            logger.warning(f'[epic] 卡片渲染失败，回退文本：{error}')
    return logic.format_text(free, upcoming)


def _links(view, config):
    return logic.format_links(
        view.get('free') or [],
        view.get('upcoming') or [],
        bool(config.get('include_upcoming', True)),
    )


async def _outgoing(view, config):
    """每次投递固定两条：卡片/文字 + 一条全部链接。"""
    messages = [await _message(view, config)]
    links = _links(view, config)
    if links:
        messages.append(links)
    return messages


async def _deliver(view, config):
    messages = await _outgoing(view, config)
    for message in messages[:-1]:
        await matcher.send(message)
    await matcher.finish(messages[-1])


async def _send(bot: Bot, target, message) -> None:
    if target['kind'] == 'group':
        await bot.send_group_msg(group_id=int(target['id']), message=message)
    else:
        await bot.send_private_msg(user_id=int(target['id']), message=message)


async def _push_round() -> None:
    """一轮定时推送：同一轮只抓一次；首装只记录；纯下架不推。"""
    config = config_store.read()
    if not config.get('push_enabled', True) or not config.get('targets'):
        return
    try:
        view = await _fetch(config)
    except service.EpicError as error:
        logger.warning(f'[epic] 抓取失败，本轮跳过：{error}')
        return
    if not view.get('free'):
        logger.warning('[epic] 本轮没有免费游戏，按空数据跳过')
        return
    bot = _pick_bot()
    if bot is None:
        return
    messages = await _outgoing(view, config)
    store = service.StateStore()
    state = store.load()
    for target in config['targets']:
        key = f"{target['kind']}:{target['id']}"
        if not store.last_games(state, key):
            # 首装只记录，不发送；下一轮才按集合变化判断。
            store.mark(state, key, view.get('key', ''), view.get('free'))
            store.save(state)
            continue
        if not store.should_push(state, key, view.get('free')):
            continue
        # 先占位再发：发送失败也只记日志，不重试、不补发。
        store.claim(state, key, view.get('key', ''), view.get('free'))
        for message in messages:
            try:
                await _send(bot, target, message)
            except Exception as error:
                logger.warning(f'[epic] 推送到 {key} 失败：{error}')


async def _handle_trigger() -> None:
    """面板「立即推送」：消费触发文件，向所有目标发一次当前视图。"""
    if not config_store.has_test_request():
        return
    config = config_store.read()
    if not config.get('targets'):
        return
    bot = _pick_bot()
    if bot is None:
        return
    try:
        view = await _fetch(config)
    except service.EpicError as error:
        logger.warning(f'[epic] 立即推送抓取失败：{error}')
        return
    config_store.take_test_request()
    messages = await _outgoing(view, config)
    store = service.StateStore()
    state = store.load()
    for target in config['targets']:
        key = f"{target['kind']}:{target['id']}"
        if view.get('free'):
            # 有免费名单时把这次手动推送记入状态，避免下一轮再推一遍；
            # 空名单不写状态，免得把「首装只记录」误判成已推过。
            store.claim(state, key, view.get('key', ''), view.get('free'))
        for message in messages:
            try:
                await _send(bot, target, message)
            except Exception as error:
                logger.warning(f'[epic] 立即推送到 {key} 失败：{error}')


_task = None


async def worker() -> None:
    next_poll = 0.0
    while True:
        try:
            if time.monotonic() >= next_poll:
                await _push_round()
                config = config_store.read()
                next_poll = time.monotonic() + max(5, int(config.get('poll_minutes', 30))) * 60
            await _handle_trigger()
        except Exception as error:
            logger.warning(f'[epic] 定时任务异常：{error}')
        await asyncio.sleep(30)


@get_driver().on_startup
async def _start():
    global _task
    _task = asyncio.create_task(worker())


@get_driver().on_shutdown
async def _stop():
    if _task:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)


@matcher.handle()
async def handle(event: Event) -> None:
    action = parse(event.get_plaintext())
    if action == 'test':
        if not _is_admin(event):
            await matcher.finish()
        try:
            config = config_store.read()
            view = await _fetch(config)
        except Exception as error:
            logger.warning(f'[epic] 超管预览失败：{error}')
            await matcher.finish('Epic 查询失败，请稍后再试。')
        await _deliver(view, config)

    if not _can_query(event):
        await matcher.finish()
    if _throttled(str(event.get_user_id())):
        await matcher.finish('查询太频繁，请稍后再试。')
    try:
        config = config_store.read()
        view = await _fetch(config)
    except Exception as error:
        logger.warning(f'[epic] 查询失败：{error}')
        await matcher.finish('Epic 查询失败，请稍后再试。')
    await _deliver(view, config)
