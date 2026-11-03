# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。
import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from tests._bootstrap import ensure_nonebot
except ImportError:
    from _bootstrap import ensure_nonebot

ensure_nonebot(superusers={'1000'})

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent

import epic_free_service as service
import gate_logic as gl
from plugins import epic_free as plugin


def event(text, user_id=1000, group_id=555, role='owner'):
    message = Message([MessageSegment.text(text)])
    return GroupMessageEvent(
        time=1700000000, self_id=100, post_type='message', sub_type='normal',
        user_id=user_id, group_id=group_id, message_type='group', message_id=1,
        message=message, raw_message=str(message), font=0,
        sender={'nickname': 'test', 'role': role}, to_me=False,
    )


def private_event(text, user_id=2000):
    message = Message([MessageSegment.text(text)])
    return PrivateMessageEvent(
        time=1700000000, self_id=100, post_type='message', sub_type='friend',
        user_id=user_id, message_type='private', message_id=1,
        message=message, raw_message=str(message), font=0,
        sender={'nickname': 'test'}, to_me=False,
    )


def game(ident, start='2026-10-29T15:00:00+00:00'):
    return {'id': ident, 'start_iso': start}


def view(key, games):
    return {'free': games, 'upcoming': [], 'key': key}


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_group_msg(self, group_id, message):
        self.calls.append(('group', group_id, message))

    async def send_private_msg(self, user_id, message):
        self.calls.append(('private', user_id, message))


class EpicFreePluginTest(unittest.TestCase):
    def test_parse_and_rule(self):
        self.assertEqual(plugin.parse('喜加一'), 'free')
        self.assertEqual(plugin.parse(' /喜加一 '), 'free')
        self.assertEqual(plugin.parse('Epic 测试'), 'test')
        self.assertEqual(plugin.parse('/epic 测试'), 'test')
        self.assertIsNone(plugin.parse('喜加一 一下'))
        self.assertTrue(asyncio.run(plugin._rule(event('喜加一'))))
        self.assertFalse(asyncio.run(plugin._rule(event('天气 北京'))))

    def test_admin_only(self):
        self.assertTrue(plugin._is_admin(event('Epic 测试', user_id=1000)))
        self.assertFalse(plugin._is_admin(event('Epic 测试', user_id=2000)))

    def test_query_permissions(self):
        self.assertTrue(plugin._can_query(event('喜加一', user_id=1000, role='member')))
        self.assertTrue(plugin._can_query(event('喜加一', user_id=2000, role='owner')))
        self.assertTrue(plugin._can_query(event('喜加一', user_id=2000, role='admin')))
        self.assertFalse(plugin._can_query(event('喜加一', user_id=2000, role='member')))
        self.assertFalse(plugin._can_query(private_event('喜加一', user_id=2000)))
        self.assertTrue(plugin._can_query(private_event('喜加一', user_id=1000)))

    def test_member_command_rejected(self):
        from nonebot.exception import FinishedException

        with patch.object(plugin.matcher, 'finish', new=AsyncMock(side_effect=FinishedException)) as finish, \
                patch.object(plugin.service, 'fetch_view', new=AsyncMock()) as fetch:
            with self.assertRaises(FinishedException):
                asyncio.run(plugin.handle(event('喜加一', user_id=2000, role='member')))
            finish.assert_awaited_once_with()
            fetch.assert_not_awaited()

    def test_matcher_is_blocking(self):
        self.assertEqual(plugin.matcher.priority, 3)
        self.assertTrue(plugin.matcher.block)

    def test_help_rows_registered(self):
        self.assertIn('喜加一', [command for command, _ in gl.all_help_rows()])

    def test_no_targets_does_not_fetch(self):
        config = {'targets': [], 'push_enabled': True, 'country': 'CN', 'locale': 'zh-CN'}
        with patch.object(plugin.config_store, 'read', return_value=config), \
                patch.object(plugin.service, 'fetch_view', new=AsyncMock()) as fetch:
            asyncio.run(plugin._push_round())
            fetch.assert_not_awaited()

    def test_first_install_records_then_change_pushes_once(self):
        games_one = [game('g1')]
        games_two = [game('g2')]
        config = {
            'targets': [{'kind': 'group', 'id': '12345'}],
            'push_enabled': True,
            'country': 'CN',
            'locale': 'zh-CN',
            'include_upcoming': True,
            'card_enabled': False,
            'poll_minutes': 30,
        }
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as folder:
            store = service.StateStore(Path(folder) / 'state.json')
            with patch.object(plugin.config_store, 'read', return_value=config), \
                    patch.object(plugin.service, 'fetch_view', new=AsyncMock(side_effect=[
                        view('k1', games_one), view('k2', games_two), view('k2', games_two),
                    ])), \
                    patch.object(plugin.service, 'StateStore', return_value=store), \
                    patch.object(plugin, '_pick_bot', return_value=bot), \
                    patch.object(plugin, '_message', new=AsyncMock(return_value='msg')), \
                    patch.object(plugin, '_links', return_value='links'):
                asyncio.run(plugin._push_round())
                self.assertEqual(bot.calls, [], '首装只记录，不能立刻推送')
                self.assertEqual(store.load()['targets']['group:12345']['last_key'], 'k1')
                asyncio.run(plugin._push_round())
                self.assertEqual(bot.calls, [('group', 12345, 'msg'), ('group', 12345, 'links')], '换期后应推卡片+链接两条')
                asyncio.run(plugin._push_round())
                self.assertEqual(len(bot.calls), 2, '同一期不能重复推送')

    def test_message_falls_back_to_text_when_card_disabled(self):
        config = {'include_upcoming': True, 'card_enabled': False}
        now = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
        payload = {
            'free': [{
                'title': '《测试游戏》', 'description': '简介', 'original_price': '¥10.00',
                'end': now, 'url': 'https://store.epicgames.com/zh-CN/p/test',
            }],
            'upcoming': [],
        }
        text = asyncio.run(plugin._message(payload, config))
        self.assertIn('【EPIC 喜加一】', text)
        self.assertIn('《测试游戏》', text)
        self.assertIn('北京时间', text)

    def test_free_command_replies_card_then_links(self):
        config = {'targets': [], 'country': 'CN', 'locale': 'zh-CN',
                  'include_upcoming': True, 'card_enabled': False}
        payload = {'free': [], 'upcoming': [], 'key': ''}
        with patch.object(plugin.config_store, 'read', return_value=config), \
                patch.object(plugin.service, 'fetch_view', new=AsyncMock(return_value=payload)), \
                patch.object(plugin, '_message', new=AsyncMock(return_value='msg')), \
                patch.object(plugin, '_links', return_value='links'), \
                patch.object(plugin.matcher, 'send', new=AsyncMock()) as send, \
                patch.object(plugin.matcher, 'finish', new=AsyncMock()) as finish:
            asyncio.run(plugin.handle(event('喜加一', user_id=2000)))
            send.assert_awaited_once_with('msg')
            finish.assert_awaited_once_with('links')

    def test_test_command_rejects_non_admin(self):
        from nonebot.exception import FinishedException

        with patch.object(plugin.matcher, 'finish', new=AsyncMock(side_effect=FinishedException)) as finish:
            with self.assertRaises(FinishedException):
                asyncio.run(plugin.handle(event('Epic 测试', user_id=2000)))
            finish.assert_awaited_once_with()

    def test_trigger_sends_to_all_targets(self):
        config = {
            'targets': [{'kind': 'group', 'id': '12345'}],
            'country': 'CN', 'locale': 'zh-CN',
            'include_upcoming': True, 'card_enabled': False,
        }
        payload = {'free': [game('g1')], 'upcoming': [], 'key': 'k1'}
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as folder:
            store = service.StateStore(Path(folder) / 'state.json')
            with patch.object(plugin.config_store, 'has_test_request', return_value=True), \
                    patch.object(plugin.config_store, 'take_test_request', return_value={'nonce': 'x'}), \
                    patch.object(plugin.config_store, 'read', return_value=config), \
                    patch.object(plugin.service, 'fetch_view', new=AsyncMock(return_value=payload)), \
                    patch.object(plugin.service, 'StateStore', return_value=store), \
                    patch.object(plugin, '_pick_bot', return_value=bot), \
                    patch.object(plugin, '_message', new=AsyncMock(return_value='msg')), \
                    patch.object(plugin, '_links', return_value='links'):
                asyncio.run(plugin._handle_trigger())
            self.assertEqual(bot.calls, [('group', 12345, 'msg'), ('group', 12345, 'links')])
            self.assertEqual(store.load()['targets']['group:12345']['last_key'], 'k1')


if __name__ == '__main__':
    unittest.main()
