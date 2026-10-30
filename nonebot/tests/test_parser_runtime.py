import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from media_parser.core.data import ParseResult, Platform
from media_parser.runtime import ArbiterBot, ParserRuntime


def seg(kind, **data):
    return SimpleNamespace(type=kind, data=data)


class ParserRuntimeTests(unittest.TestCase):
    def test_parse_error_replies_without_leaking_exception(self):
        async def run():
            runtime = ParserRuntime()
            try:
                event = SimpleNamespace(self_id=10, user_id=20, original_message=[seg('text', text='https://xhslink.com/a/example')], reply=None)
                match = runtime.match(event)
                self.assertIsNotNone(match)
                runtime.parsers[match[0]].parse = AsyncMock(side_effect=RuntimeError('private-secret'))
                bot = SimpleNamespace(send=AsyncMock())
                await runtime.process(bot, event, match)
                bot.send.assert_awaited_once()
                text = str(bot.send.await_args.args[1])
                self.assertIn('解析失败', text)
                self.assertNotIn('private-secret', text)
            finally:
                await runtime.close()
        asyncio.run(run())

    def test_arbiter_onebot_parameters(self):
        async def run():
            bot = SimpleNamespace(call_api=AsyncMock(return_value={}))
            adapter = ArbiterBot(bot)
            await adapter.set_msg_emoji_like(message_id=1, emoji_id=289, emoji_type='1', set=True)
            await adapter.fetch_emoji_like(message_id=1, emojiId='289', emoji_id='289', emojiType='1', count=20)
            bot.call_api.assert_any_await('set_msg_emoji_like', message_id=1, emoji_id='289', set=True)
            bot.call_api.assert_any_await('fetch_emoji_like', message_id=1, emojiId='289', count=20)
        asyncio.run(run())

    def test_runtime_dispatch_and_dedup(self):
        async def run():
            runtime = ParserRuntime()
            try:
                await runtime.start()
                event = SimpleNamespace(self_id=10, user_id=20, original_message=[seg('text', text='BV1xx411c7mD')], reply=None)
                match = runtime.match(event)
                self.assertIsNotNone(match)
                parser = runtime.parsers[match[0]]
                parser.parse = AsyncMock(return_value=ParseResult(platform=Platform('bilibili', 'B站'), title='test', url='https://example.com/a'))
                runtime.sender.send_parse_result = AsyncMock()
                await runtime.process(SimpleNamespace(), event, match)
                await runtime.process(SimpleNamespace(), event, match)
                self.assertEqual(parser.parse.await_count, 1)
                self.assertEqual(runtime.sender.send_parse_result.await_count, 1)
            finally:
                await runtime.close()
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
