import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from media_parser.transport import File, Image, Node, Nodes, Plain, Transport


class ParserTransportTests(unittest.TestCase):
    def test_shared_cache_maps_to_container_for_media_and_files(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as root:
            path = Path(root) / 'sample video.mp4'
            bot = SimpleNamespace(self_id='10', call_api=AsyncMock(), send=AsyncMock())
            event = SimpleNamespace(user_id=42)
            config = SimpleNamespace(cache_dir=Path(root), container_cache_dir='/app/data/parser-cache')
            transport = Transport(bot, event, config)
            asyncio.run(transport.send([Image.fromFileSystem(str(path)), File('sample.mp4', path.as_uri())]))
            media = bot.send.await_args.args[1][0]
            self.assertEqual(media.data['file'], 'file:///app/data/parser-cache/sample%20video.mp4')
            self.assertEqual(bot.call_api.await_args.kwargs['file'], '/app/data/parser-cache/sample video.mp4')

    def test_forward_and_files_use_separate_onebot_actions(self):
        bot = SimpleNamespace(self_id='10', call_api=AsyncMock(), send=AsyncMock())
        event = SimpleNamespace(group_id=12, user_id=42)
        transport = Transport(bot, event)
        chain = [Nodes([Node('10', '解析器', [Plain('hello')]), Node('10', '解析器', [Image.fromFileSystem('D:/img.png')])]), File('a.mp3', 'file:///D:/a.mp3')]
        asyncio.run(transport.send(chain))
        self.assertEqual([x.args[0] for x in bot.call_api.await_args_list], ['send_group_forward_msg', 'upload_group_file'])
        self.assertEqual(bot.call_api.await_args_list[0].kwargs['group_id'], 12)
        self.assertEqual(len(bot.call_api.await_args_list[0].kwargs['messages']), 2)

    def test_private_text(self):
        bot = SimpleNamespace(self_id='10', call_api=AsyncMock(), send=AsyncMock())
        event = SimpleNamespace(user_id=42)
        asyncio.run(Transport(bot, event).send([Plain('hello')]))
        bot.send.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
