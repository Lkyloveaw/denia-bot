import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
import nonebot
try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot
ensure_nonebot()
from muicebot.llm import ModelRequest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from plugins import group_topics as plugin


def event(group, msgid, text):
    message = Message([MessageSegment.text(text)])
    return GroupMessageEvent(time=1700000000, self_id=100, post_type='message', sub_type='normal',
        user_id=200, group_id=group, message_type='group', message_id=msgid,
        message=message, raw_message=str(message), font=0,
        sender={'nickname':'test', 'role':'member'}, to_me=False)


class WiringTest(unittest.TestCase):
    def test_group_view_matches_only_own_group_command(self):
        self.assertTrue(asyncio.run(plugin._group_view_rule(event(101, 8, '群话题 查看'))))
        self.assertTrue(asyncio.run(plugin._group_view_rule(event(101, 8, '/群话题 查看'))))
        self.assertFalse(asyncio.run(plugin._group_view_rule(event(101, 8, '群话题 查看 102'))))
        self.assertFalse(asyncio.run(plugin._group_view_rule(event(101, 8, '群话题 清除'))))

    def test_picture_output_and_disabled_group(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            old = plugin.topics.DEFAULT_PATH
            plugin.topics.DEFAULT_PATH = Path(folder) / 'topics.sqlite3'
            try:
                self.assertIn('未开启', asyncio.run(plugin.topic_reply('101')))
                plugin.topics.set_enabled(plugin.topics.DEFAULT_PATH, '101', True)
                plugin.topics.record(plugin.topics.DEFAULT_PATH, '101', '1', '今汐', tokenizer=lambda _: ['今汐'])
                fake_renderer = SimpleNamespace(render_pages=lambda *a: [b'png'])
                with patch.dict(sys.modules, {'weather_image': fake_renderer}):
                    result = asyncio.run(plugin.topic_reply('101'))
                self.assertEqual(result.type, 'image')
                self.assertTrue(result.data['file'].startswith('base64://'))
                self.assertIn('未开启', asyncio.run(plugin.topic_reply('102')))
            finally:
                plugin.topics.DEFAULT_PATH = old

    def test_opt_in_passive_collection_and_scoped_injection(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            old = plugin.topics.DEFAULT_PATH
            plugin.topics.DEFAULT_PATH = Path(folder) / 'topics.sqlite3'
            try:
                async def scenario():
                    await plugin.start()
                    try:
                        self.assertFalse(await plugin._group_rule(event(101, 1, '今汐深塔'), {}))
                        plugin.topics.set_enabled(plugin.topics.DEFAULT_PATH, '101', True)
                        for number in (1, 2):
                            e, state = event(101, number, '今汐深塔'), {}
                            self.assertTrue(await plugin._group_rule(e, state))
                            await plugin.collect(e, state)
                        await asyncio.wait_for(plugin._queue.join(), timeout=5)
                        ranking = plugin.topics.speaker_top(plugin.topics.DEFAULT_PATH, '101')
                        self.assertEqual([(row['user_id'], row['count'], row['display_name']) for row in ranking], [('200', 2, 'test')])
                        request = ModelRequest(prompt='怎样打', system='人格')
                        await plugin.inject(request, event(101, 3, '怎样打'))
                        self.assertIn('今汐', request.system)
                        other = ModelRequest(prompt='怎样打', system='人格')
                        await plugin.inject(other, event(102, 4, '怎样打'))
                        self.assertEqual(other.system, '人格')
                        plugin.topics.set_enabled(plugin.topics.DEFAULT_PATH, '101', False)
                        off = ModelRequest(prompt='怎样打', system='人格')
                        await plugin.inject(off, event(101, 5, '怎样打'))
                        self.assertEqual(off.system, '人格')
                    finally:
                        await plugin.stop()
                with patch.object(plugin.topics, 'keywords', return_value=['今汐', '深塔']):
                    asyncio.run(scenario())
                self.assertFalse(plugin.collector.block)
            finally:
                plugin.topics.DEFAULT_PATH = old


if __name__ == '__main__':
    unittest.main()
