"""Translate upstream sender components to OneBot v11 actions."""

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from nonebot.adapters.onebot.v11 import Message, MessageSegment


# 大图上传常超过默认 30 秒；仅延长解析器的发送回执等待时间。
SEND_TIMEOUT = 180


class BaseMessageComponent:
    pass


@dataclass
class Plain(BaseMessageComponent):
    text: str


@dataclass
class File(BaseMessageComponent):
    name: str
    file: str


@dataclass
class Media(BaseMessageComponent):
    file: str

    @classmethod
    def fromFileSystem(cls, path):
        return cls(str(path))


class Image(Media):
    pass


class Video(Media):
    pass


class Record(Media):
    pass


@dataclass
class Node(BaseMessageComponent):
    uin: str
    name: str
    content: list


@dataclass
class Nodes(BaseMessageComponent):
    nodes: list = field(default_factory=list)


class Transport:
    def __init__(self, bot, event, config=None):
        self.bot, self.event = bot, event
        self.config = config

    def get_self_id(self):
        return str(self.bot.self_id)

    def chain_result(self, segments):
        return segments

    def mapped_path(self, path):
        if self.config and self.config.container_cache_dir:
            try:
                relative = Path(path).resolve().relative_to(self.config.cache_dir.resolve())
                return PurePosixPath(self.config.container_cache_dir, *relative.parts)
            except ValueError:
                pass
        return None

    def segment(self, item):
        if isinstance(item, Plain):
            return MessageSegment.text(item.text)
        if isinstance(item, Image):
            path = self.mapped_path(item.file)
            return MessageSegment.image(path.as_uri() if path else Path(item.file))
        if isinstance(item, Video):
            path = self.mapped_path(item.file)
            return MessageSegment.video(path.as_uri() if path else Path(item.file))
        if isinstance(item, Record):
            path = self.mapped_path(item.file)
            return MessageSegment.record(path.as_uri() if path else Path(item.file))
        raise TypeError(f'unsupported parser component: {type(item).__name__}')

    async def upload(self, item):
        group = getattr(self.event, 'group_id', None)
        target = {'group_id': group} if group is not None else {'user_id': self.event.user_id}
        parsed = urlparse(item.file)
        filename = url2pathname(unquote(parsed.path)) if parsed.scheme == 'file' else item.file
        filename = str(self.mapped_path(filename) or filename)
        await self.bot.call_api('upload_group_file' if group is not None else 'upload_private_file', file=filename, name=item.name, _timeout=SEND_TIMEOUT, **target)

    async def send(self, segments):
        ordinary = Message()
        async def flush():
            nonlocal ordinary
            if ordinary:
                await self.bot.send(self.event, ordinary, _timeout=SEND_TIMEOUT)
                ordinary = Message()
        for item in segments:
            if isinstance(item, File):
                await flush()
                await self.upload(item)
            elif isinstance(item, Nodes):
                await flush()
                messages, files = [], []
                for node in item.nodes:
                    contents = [c for c in node.content if not isinstance(c, File)]
                    files.extend(c for c in node.content if isinstance(c, File))
                    if contents:
                        messages.append(MessageSegment.node_custom(int(node.uin), node.name, Message([self.segment(c) for c in contents])))
                if messages:
                    group = getattr(self.event, 'group_id', None)
                    target = {'group_id': group} if group is not None else {'user_id': self.event.user_id}
                    await self.bot.call_api('send_group_forward_msg' if group is not None else 'send_private_forward_msg', messages=messages, _timeout=SEND_TIMEOUT, **target)
                for file in files:
                    await self.upload(file)
            else:
                ordinary += self.segment(item)
        await flush()
