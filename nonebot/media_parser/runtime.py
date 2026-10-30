"""Lifecycle and dispatch around the upstream parser classes."""

import asyncio
import os
import re

from nonebot import logger

from parser_logic import find_match, message_text, session_key
from .core.arbiter import ArbiterContext, EmojiLikeArbiter
from .core.clean import CacheCleaner
from .core.config import PluginConfig
from .core.debounce import Debouncer
from .core.download import Downloader
from .core.parsers import BaseParser, BilibiliParser
from .core.render import Renderer
from .core.sender import MessageSender
from .core.utils import extract_json_url
from .transport import Transport


class ArbiterBot:
    def __init__(self, bot):
        self.bot = bot

    async def set_msg_emoji_like(self, message_id, emoji_id, set, **kwargs):
        return await asyncio.wait_for(self.bot.call_api('set_msg_emoji_like', message_id=message_id, emoji_id=str(emoji_id), set=set), timeout=5)

    async def fetch_emoji_like(self, message_id, emojiId, count, **kwargs):
        return await asyncio.wait_for(self.bot.call_api('fetch_emoji_like', message_id=message_id, emojiId=emojiId, count=count), timeout=5)


class ParserRuntime:
    def __init__(self):
        self.cfg = PluginConfig()
        paths = [str(p) for p in self.cfg.media_tools_paths if p]
        if paths:
            os.environ['PATH'] = os.pathsep.join([*paths, os.environ.get('PATH', '')])
        self.downloader = Downloader(self.cfg)
        self.renderer = Renderer(self.cfg)
        self.sender = MessageSender(self.cfg, self.renderer)
        self.debouncer = Debouncer(self.cfg)
        self.arbiter = EmojiLikeArbiter()
        self.cleaner = CacheCleaner(self.cfg)
        self.parsers = {}
        self.patterns = []
        self.tasks = set()
        for cls in BaseParser.get_all_subclass():
            if cls.platform.name not in self.cfg.parser.enabled_platforms():
                continue
            parser = cls(self.cfg, self.downloader)
            for keyword, pattern in cls._key_patterns:
                self.parsers[keyword] = parser
                self.patterns.append((keyword, re.compile(pattern) if isinstance(pattern, str) else pattern))
        self.patterns.sort(key=lambda item: -len(item[0]))

    async def start(self):
        await asyncio.to_thread(Renderer.load_resources)
        if not self.cfg.config_path.exists():
            self.cfg.save_config()
        logger.info(f'[parser] 已启用 {len(set(self.parsers.values()))} 个平台')

    def match(self, event):
        session = session_key(event)
        if self.cfg.whitelist and session not in self.cfg.whitelist:
            return None
        if session in self.cfg.blacklist or str(event.user_id) == str(event.self_id):
            return None
        text = message_text(event, extract_json_url, self.cfg.enable_reply_parse)
        return find_match(text, self.patterns) if text else None

    def dispatch(self, bot, event, match):
        task = asyncio.create_task(self.process(bot, event, match))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def process(self, bot, event, match):
        try:
            keyword, found = match
            session = session_key(event)
            if getattr(event, 'group_id', None) is not None:
                ctx = ArbiterContext(int(event.message_id), int(event.time), int(event.self_id))
                if not await self.arbiter.compete(ArbiterBot(bot), ctx):
                    return
            if self.debouncer.hit_link(session, found.group(0)):
                return
            try:
                result = await self.parsers[keyword].parse(keyword, found)
            except Exception as exc:
                platform = self.parsers[keyword].platform.name
                logger.warning(f'[parser] {platform} 解析失败：{type(exc).__name__}')
                message = ('小红书解析失败，链接可能失效，或需要配置小红书 Cookie。'
                           if platform == 'xhs' else '链接解析失败，请检查链接或稍后重试。')
                try:
                    await asyncio.wait_for(bot.send(event, message), timeout=15)
                except Exception:
                    logger.warning('[parser] 无法发送解析失败提示')
                return
            if self.debouncer.hit_resource(session, result.get_resource_id()):
                return
            await self.sender.send_parse_result(Transport(bot, event, self.cfg), result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('[parser] 解析或发送失败')

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.downloader.close()
        for parser in set(self.parsers.values()):
            await parser.close_session()
        await self.cleaner.stop()

    def bilibili(self):
        return next((p for p in self.parsers.values() if isinstance(p, BilibiliParser)), None)
