import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11.exception import ActionFailed, NetworkError

from media_parser.core.data import ImageContent, ParseResult, Platform, SendGroup
from media_parser.core.sender import MessageSender
from media_parser.transport import Transport


class ParserSenderTests(unittest.IsolatedAsyncioTestCase):
    def make_sender(self, *, preview=False, threshold=3):
        config = SimpleNamespace(
            single_heavy_render_card=preview,
            forward_threshold=threshold,
            show_download_fail_tip=True,
            audio_to_file=False,
        )
        renderer = SimpleNamespace(render_cards=AsyncMock(return_value=[Path('preview.png')]))
        return MessageSender(config, renderer)

    def make_result(self):
        return ParseResult(
            platform=Platform('xhs', '小红书'),
            contents=[ImageContent(Path(f'photo-{i}.png')) for i in range(10)],
        )

    async def test_late_batch_delivery_does_not_resend_individual_images(self):
        # SnowLuma can accept the batch and finish sending after the caller times out.
        submitted, delivered, pending = [], [], []

        async def send(event, message, **kwargs):
            batch = list(message)
            submitted.append(batch)
            if len(batch) > 1:
                pending.append(batch)
                raise NetworkError('WebSocket call api send_msg timeout')
            delivered.append(batch)

        bot = SimpleNamespace(self_id='10', send=AsyncMock(side_effect=send))
        event = SimpleNamespace(group_id=1076073471, user_id=42)
        try:
            await self.make_sender(threshold=99).send_parse_result(Transport(bot, event), self.make_result())
        except NetworkError:
            pass
        delivered.extend(pending)

        self.assertEqual(len(submitted), 1, 'An unconfirmed batch must not trigger individual resends')
        self.assertEqual(len(delivered), 1)
        self.assertEqual(len(delivered[0]), 10)

    async def test_success_keeps_preview_separate_and_sends_media_once(self):
        bot = SimpleNamespace(self_id='10', send=AsyncMock(), call_api=AsyncMock())
        event = SimpleNamespace(group_id=1076073471, user_id=42)
        await self.make_sender(preview=True).send_parse_result(Transport(bot, event), self.make_result())
        bot.send.assert_awaited_once()
        self.assertEqual(len(bot.send.await_args.args[1]), 1)
        self.assertTrue(bot.send.await_args.args[1][0].data['file'].endswith('/preview.png'))
        self.assertEqual(bot.send.await_args.kwargs['_timeout'], 180)
        bot.call_api.assert_awaited_once()
        call = bot.call_api.await_args
        self.assertEqual(call.args[0], 'send_group_forward_msg')
        self.assertEqual(call.kwargs['group_id'], 1076073471)
        self.assertEqual(call.kwargs['_timeout'], 180)
        nodes = call.kwargs['messages']
        self.assertEqual(len(nodes), 10)
        for i, node in enumerate(nodes):
            self.assertEqual(node.type, 'node')
            self.assertEqual(node.data['user_id'], '10')
            content = node.data['content']
            self.assertEqual(len(content), 1)
            self.assertTrue(content[0].data['file'].endswith(f'/photo-{i}.png'))

    async def test_threshold_controls_group_and_private_forward(self):
        for group_id in (None, 1076073471):
            for count in (2, 3):
                with self.subTest(group_id=group_id, count=count):
                    bot = SimpleNamespace(self_id='10', send=AsyncMock(), call_api=AsyncMock())
                    event = SimpleNamespace(user_id=42)
                    if group_id is not None:
                        event.group_id = group_id
                    result = self.make_result()
                    result.contents = result.contents[:count]
                    await self.make_sender().send_parse_result(Transport(bot, event), result)
                    if count < 3:
                        bot.send.assert_awaited_once()
                        bot.call_api.assert_not_awaited()
                    else:
                        bot.send.assert_not_awaited()
                        bot.call_api.assert_awaited_once()
                        call = bot.call_api.await_args
                        self.assertEqual(call.args[0], 'send_group_forward_msg' if group_id else 'send_private_forward_msg')
                        self.assertEqual(call.kwargs['group_id' if group_id else 'user_id'], group_id or 42)
                        self.assertEqual(len(call.kwargs['messages']), count)

    async def test_paginated_preview_is_ordered_before_original_media(self):
        bot = SimpleNamespace(self_id='10', send=AsyncMock(), call_api=AsyncMock())
        sender = self.make_sender(preview=True)
        sender.renderer.render_cards.return_value = [Path('page1.png'), Path('page2.png')]
        await sender.send_parse_result(Transport(bot, SimpleNamespace(user_id=42)), self.make_result())
        message = bot.send.await_args.args[1]
        self.assertEqual(len(message), 2)
        self.assertTrue(message[0].data['file'].endswith('/page1.png'))
        self.assertTrue(message[1].data['file'].endswith('/page2.png'))
        bot.call_api.assert_awaited_once()

    async def test_renderer_failure_preserves_text_link_and_original_media(self):
        bot = SimpleNamespace(self_id='10', send=AsyncMock(), call_api=AsyncMock())
        sender = self.make_sender(preview=True)
        sender.renderer.render_cards.return_value = []
        result = self.make_result()
        result.title = '完整原帖'
        result.url = 'https://example.com/post'
        await sender.send_parse_result(Transport(bot, SimpleNamespace(user_id=42)), result)
        self.assertIn('完整原帖', str(bot.send.await_args.args[1]))
        self.assertIn(result.url, str(bot.send.await_args.args[1]))
        bot.call_api.assert_awaited_once()

    async def test_forward_timeout_does_not_resend_as_ordinary_images(self):
        bot = SimpleNamespace(
            self_id='10', send=AsyncMock(),
            call_api=AsyncMock(side_effect=NetworkError('WebSocket call api send_group_forward_msg timeout')),
        )
        event = SimpleNamespace(group_id=1076073471, user_id=42)
        with self.assertRaises(NetworkError):
            await self.make_sender().send_parse_result(Transport(bot, event), self.make_result())
        bot.call_api.assert_awaited_once()
        bot.send.assert_not_awaited()

    async def test_group_override_can_disable_or_force_forward(self):
        for forced, count in ((False, 10), (True, 1)):
            with self.subTest(forced=forced):
                bot = SimpleNamespace(self_id='10', send=AsyncMock(), call_api=AsyncMock())
                event = SimpleNamespace(group_id=1076073471, user_id=42)
                result = self.make_result()
                result.send_groups = [SendGroup(contents=result.contents[:count], force_merge=forced)]
                await self.make_sender().send_parse_result(Transport(bot, event), result)
                self.assertEqual(bot.call_api.await_count, int(forced))
                self.assertEqual(bot.send.await_count, int(not forced))

    async def test_send_failure_does_not_retry_or_send_text_fallback(self):
        for error in (NetworkError('connection lost'), TimeoutError(), ActionFailed(retcode=1200)):
            with self.subTest(error=type(error).__name__):
                bot = SimpleNamespace(self_id='10', send=AsyncMock(side_effect=error))
                event = SimpleNamespace(user_id=42)
                result = self.make_result()
                result.text = 'Post text must not be resent after an uncertain media send'
                with self.assertRaises(type(error)):
                    await self.make_sender(threshold=99).send_parse_result(Transport(bot, event), result)
                bot.send.assert_awaited_once()

    async def test_failed_group_does_not_replay_previous_group_or_continue(self):
        bot = SimpleNamespace(self_id='10', send=AsyncMock(side_effect=[None, NetworkError('timeout')]))
        event = SimpleNamespace(group_id=1076073471, user_id=42)
        result = self.make_result()
        result.send_groups = [SendGroup(contents=result.contents[i:i+2]) for i in (0, 2, 4)]
        with self.assertRaises(NetworkError):
            await self.make_sender().send_parse_result(Transport(bot, event), result)
        self.assertEqual(bot.send.await_count, 2)

    async def test_preview_timeout_does_not_start_media_send(self):
        bot = SimpleNamespace(self_id='10', send=AsyncMock(side_effect=NetworkError('timeout')))
        event = SimpleNamespace(user_id=42)
        with self.assertRaises(NetworkError):
            await self.make_sender(preview=True).send_parse_result(Transport(bot, event), self.make_result())
        bot.send.assert_awaited_once()
        self.assertEqual(len(bot.send.await_args.args[1]), 1)


if __name__ == '__main__':
    unittest.main()
