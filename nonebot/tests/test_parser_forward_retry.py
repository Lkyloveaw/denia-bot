import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11.exception import ActionFailed, NetworkError

from media_parser.transport import Image, Node, Nodes, Transport


def rejected_upload(command=1004):
    return ActionFailed(
        status='failed', retcode=100, data=None,
        wording=f'highway upload error_code=101 (cmdId={command} chunk=1048576/2693515 offset=1048576 segRetCode=0)',
    )


class ForwardRetryTests(unittest.IsolatedAsyncioTestCase):
    def nodes(self):
        return [Nodes([Node('10', '解析器', [Image('original.png')])])]

    async def test_explicit_upload_rejection_retries_forward_without_splitting_images(self):
        for group in (True, False):
            with self.subTest(group=group):
                bot = SimpleNamespace(self_id='10', send=AsyncMock(), call_api=AsyncMock(side_effect=[rejected_upload(1004 if group else 1003), {'message_id': 123}]))
                event = SimpleNamespace(user_id=42)
                if group:
                    event.group_id = 1076073471
                with patch('asyncio.sleep', new_callable=AsyncMock):
                    await Transport(bot, event).send(self.nodes())
                self.assertEqual(bot.call_api.await_count, 2)
                self.assertEqual(bot.call_api.await_args_list[0], bot.call_api.await_args_list[1])
                bot.send.assert_not_awaited()

    async def test_upload_retries_are_bounded(self):
        bot = SimpleNamespace(self_id='10', send=AsyncMock(), call_api=AsyncMock(side_effect=rejected_upload()))
        with patch('asyncio.sleep', new_callable=AsyncMock):
            with self.assertRaises(ActionFailed):
                await Transport(bot, SimpleNamespace(group_id=1076073471, user_id=42)).send(self.nodes())
        self.assertEqual(bot.call_api.await_count, 3)
        bot.send.assert_not_awaited()

    async def test_timeout_and_unrelated_failures_are_never_retried(self):
        for error in (
            NetworkError('WebSocket call api send_group_forward_msg timeout'),
            TimeoutError(),
            ActionFailed(status='failed', retcode=100, wording='message send failed'),
            ActionFailed(status='failed', retcode=100, wording='highway upload error_code=101 (cmdId=9999 chunk=1/2 offset=1 segRetCode=0)'),
        ):
            with self.subTest(error=str(error)):
                bot = SimpleNamespace(self_id='10', send=AsyncMock(), call_api=AsyncMock(side_effect=error))
                with self.assertRaises(type(error)):
                    await Transport(bot, SimpleNamespace(user_id=42)).send(self.nodes())
                bot.call_api.assert_awaited_once()
                bot.send.assert_not_awaited()

    async def test_timeout_after_upload_retry_stops_immediately(self):
        bot = SimpleNamespace(self_id='10', send=AsyncMock(), call_api=AsyncMock(side_effect=[rejected_upload(), NetworkError('timeout')]))
        with patch('asyncio.sleep', new_callable=AsyncMock):
            with self.assertRaises(NetworkError):
                await Transport(bot, SimpleNamespace(group_id=1076073471, user_id=42)).send(self.nodes())
        self.assertEqual(bot.call_api.await_count, 2)
        bot.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
