# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""地震源接线用例：命令规则、管理员、首轮静默、新事件推送、JMA 中间报抑制。"""

import asyncio
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import nonebot

try:
    from tests._bootstrap import ensure_nonebot
except ImportError:
    from _bootstrap import ensure_nonebot

ensure_nonebot(superusers={'1000'})

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

import disaster_config as config_mod
import disaster_logic as logic
from plugins import disaster_warning as plugin

CENC_EEW = {
    'ID': 'b4zex3fm5wwyy',
    'EventID': '202610300153.0001',
    'ReportTime': '2026-10-30 01:53:54',
    'ReportNum': 1,
    'OriginTime': '2026-10-30 01:53:54',
    'HypoCenter': '新疆博尔塔拉州精河县',
    'Latitude': 44.221,
    'Longitude': 82.66,
    'Magnitude': 4.2,
    'Depth': 5,
    'MaxIntensity': 5.4,
}

JMA_EEW = {
    'EventID': '20261031093302',
    'Serial': 4,
    'OriginTime': '2026/10/31 09:32:51',
    'Hypocenter': '八丈島東方沖',
    'Latitude': 33.5,
    'Longitude': 141.5,
    'Magunitude': 4.8,
    'Depth': 60,
    'MaxIntensity': '1',
    'isFinal': True,
    'isCancel': False,
    'isTraining': False,
    'isAssumption': False,
}


def event(text, user_id=1000):
    message = Message([MessageSegment.text(text)])
    return GroupMessageEvent(
        time=1700000000, self_id=100, post_type='message', sub_type='normal',
        user_id=user_id, group_id=555, message_type='group', message_id=1,
        message=message, raw_message=str(message), font=0,
        sender={'nickname': 'test', 'role': 'owner'}, to_me=False,
    )


class FakeSource:
    def __init__(self):
        self.cenc_eew_data = {}
        self.jma_eew_data = {}
        self.cenc_list_data = {}
        self.jma_list_data = {}
        self.closed = False

    async def cenc_eew(self):
        return self.cenc_eew_data

    async def jma_eew(self):
        return self.jma_eew_data

    async def cenc_list(self):
        return self.cenc_list_data

    async def jma_list(self):
        return self.jma_list_data

    async def close(self):
        self.closed = True


class WiringTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.base = Path(self.folder.name)
        config = config_mod.load_config(self.base / 'disaster_warning.json')
        config['push'] = {'enabled': True, 'group_id': '123456789', 'owner_id': '987654321'}
        config['filters']['earthquake_min_magnitude'] = 0.0
        config['filters']['earthquake_max_distance_km'] = 0
        config['local']['enabled'] = False
        config_mod.save_config(config, self.base / 'disaster_warning.json')

    def tearDown(self):
        self.folder.cleanup()

    def runtime(self, source, pushed, images=None, render=None):
        async def push(text, config, image=None):
            pushed.append(text)
            if images is not None:
                images.append(image)
            return True

        runtime = plugin.Runtime(base=self.base, source=source, push=push, render=render)
        runtime.prepare()
        return runtime

    def test_matcher_is_high_priority_and_blocking(self):
        self.assertEqual(plugin.matcher.priority, 1)
        self.assertTrue(plugin.matcher.block)

    def test_parse_command_and_list_args(self):
        self.assertEqual(plugin._parse_command(' /地震列表 jma 10 '), ('地震列表', 'jma 10'))
        self.assertIsNone(plugin._parse_command('今天天气'))
        self.assertEqual(plugin._parse_list_args(''), ('cenc', 5))
        self.assertEqual(plugin._parse_list_args('jma 10'), ('jma', 10))
        self.assertEqual(plugin._parse_list_args('20'), ('cenc', 20))
        self.assertEqual(plugin._parse_list_args('jma 999'), ('jma', 20))

    def test_admin_only(self):
        self.assertTrue(plugin._is_admin(event('地震源状态', user_id=1000)))
        self.assertFalse(plugin._is_admin(event('地震源状态', user_id=2000)))

    def test_first_poll_baselines_then_new_event_pushes(self):
        source = FakeSource()
        pushed = []
        runtime = self.runtime(source, pushed)

        async def scenario():
            source.cenc_eew_data = dict(CENC_EEW)
            await runtime.poll_eew()
            self.assertEqual(pushed, [], '首轮只做基线，不推历史事件')
            self.assertEqual(len(runtime.latest), 1, '缓存里仍可查询')

            source.cenc_eew_data = dict(
                CENC_EEW,
                EventID='202610310001.0002',
                OriginTime='2026-10-31 12:00:00',
                HypoCenter='新地点',
                Magnitude=5.0,
            )
            await runtime.poll_eew()
            self.assertEqual(len(pushed), 1)
            self.assertIn('新地点', pushed[0])
            self.assertIn('M5.0', pushed[0])

            await runtime.poll_eew()
            self.assertEqual(len(pushed), 1, '同一事件不重复推')

        asyncio.run(scenario())

    def test_jma_intermediate_reports_are_suppressed(self):
        source = FakeSource()
        pushed = []
        runtime = self.runtime(source, pushed)

        async def scenario():
            source.jma_eew_data = dict(JMA_EEW, Serial=2, isFinal=False)
            await runtime.poll_eew()  # 基线
            source.jma_eew_data = dict(JMA_EEW, Serial=3, isFinal=False)
            await runtime.poll_eew()
            self.assertEqual(pushed, [], 'JMA 中间报不推')
            source.jma_eew_data = dict(JMA_EEW, Serial=4, isFinal=True)
            await runtime.poll_eew()
            self.assertEqual(len(pushed), 1)
            self.assertIn('最终报', pushed[0])

        asyncio.run(scenario())

    def test_list_baseline_waits_for_a_populated_list(self):
        source = FakeSource()
        pushed = []
        runtime = self.runtime(source, pushed)

        def item(index, event_id=None):
            return {
                'type': 'reviewed', 'EventID': event_id or f'CD.{index}',
                'time': f'2026-10-31 00:{index:02d}:00', 'placeName': f'地点{index}',
                'magnitude': '4.0', 'depth': '10', 'latitude': '30.0',
                'longitude': '120.0', 'intensity': '4',
            }

        async def scenario():
            source.cenc_list_data = {}
            await runtime.poll_lists()
            self.assertFalse(runtime.state['initialized'].get('cenc_list'),
                             '空列表不能当基线')

            items = {f'No{index}': item(index) for index in range(1, 41)}
            source.cenc_list_data = items
            await runtime.poll_lists()
            self.assertTrue(runtime.state['initialized'].get('cenc_list'))
            self.assertEqual(runtime.state['baseline_at'].get('cenc_list'), '2026-10-31 00:40:00')
            self.assertEqual(pushed, [], '基线不推历史')

            items['No41'] = item(41, 'CD.NEW')
            source.cenc_list_data = items
            await runtime.poll_lists()
            self.assertEqual(len(pushed), 1)
            self.assertIn('地点41', pushed[0])

        asyncio.run(scenario())

    def test_render_image_is_attached(self):
        source = FakeSource()
        pushed = []
        images = []

        async def fake_render(event, config, tone):
            return b'PNG-BYTES'

        runtime = self.runtime(source, pushed, images, fake_render)

        async def scenario():
            source.cenc_eew_data = dict(CENC_EEW)
            await runtime.poll_eew()
            source.cenc_eew_data = dict(
                CENC_EEW,
                EventID='202610310002.0003',
                OriginTime='2026-10-31 12:10:00',
                HypoCenter='渲染测试地点',
                Magnitude=5.4,
            )
            await runtime.poll_eew()
            self.assertEqual(images, [b'PNG-BYTES'])
            self.assertEqual(len(pushed), 1)
            self.assertIn('渲染测试地点', pushed[0])

        asyncio.run(scenario())

    def test_runtime_start_is_idempotent_and_stop_cleans_up(self):
        source = FakeSource()
        runtime = self.runtime(source, [])

        async def scenario():
            await runtime.start()
            tasks = list(runtime.tasks)
            self.assertEqual(
                [task.get_name() for task in tasks],
                ['disaster-eew', 'disaster-list', 'disaster-jian', 'disaster-eqsc'],
            )
            await runtime.start()
            self.assertEqual(runtime.tasks, tasks, '重复 start 不应重复建任务')
            await runtime.stop()
            self.assertEqual(runtime.tasks, [])
            self.assertTrue(all(task.done() for task in tasks))
            self.assertFalse(source.closed, '注入源由调用方关闭，Runtime 不越权')
            self.assertTrue(runtime.state_path.exists(), 'stop 应落状态')

        asyncio.run(scenario())

    def test_stop_swallows_owned_source_close_error(self):
        runtime = plugin.Runtime(base=self.base)
        runtime.prepare()
        runtime.source.close = AsyncMock(side_effect=RuntimeError('boom'))

        asyncio.run(runtime.stop())

        self.assertEqual(runtime.source.close.await_count, 1)
        self.assertTrue(runtime.state_path.exists())

    def test_eqsc_loop_survives_config_error(self):
        runtime = self.runtime(FakeSource(), [])
        original = runtime.reload_config
        calls = {'count': 0}

        def flaky_reload():
            calls['count'] += 1
            if calls['count'] == 1:
                raise OSError('config unreadable')
            return original()

        runtime.reload_config = flaky_reload

        async def scenario():
            task = asyncio.create_task(runtime._eqsc_loop())
            await asyncio.sleep(0.05)
            self.assertFalse(task.done(), 'EQSC loop 不应被一次配置读取异常打死')
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
        self.assertEqual(calls['count'], 1)

    def test_dispatch_records_event_and_status(self):
        source = FakeSource()
        runtime = self.runtime(source, [])

        async def scenario():
            source.cenc_eew_data = dict(CENC_EEW)
            await runtime.poll_eew()
            source.cenc_eew_data = dict(
                CENC_EEW, EventID='202610310003.0001', HypoCenter='事件库地点',
            )
            await runtime.poll_eew()
            row = runtime.store.recent(1)[0]
            self.assertEqual(row['event_key'], 'cenc_eew:202610310003.0001:1')
            self.assertEqual(row['kind'], 'earthquake')
            self.assertEqual(row['status'], 'pushed')
            self.assertEqual(row['place'], '事件库地点')

        asyncio.run(scenario())

    def test_training_and_cancel_are_recorded_ignored(self):
        runtime = self.runtime(FakeSource(), [])
        training = replace(logic.sample_event(), is_training=True)
        cancel = replace(logic.sample_event(), event_id='CANCEL', is_cancel=True)

        async def scenario():
            self.assertFalse(await runtime._dispatch(training, runtime.config))
            self.assertFalse(await runtime._dispatch(cancel, runtime.config))

        asyncio.run(scenario())
        rows = runtime.store.recent(5)
        self.assertEqual({row['status'] for row in rows}, {'ignored'})
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['detail'] in ('训练报', '取消报') for row in rows))

    def test_filtered_event_is_recorded(self):
        runtime = self.runtime(FakeSource(), [])
        runtime.config['filters']['earthquake_min_magnitude'] = 4.5
        weak = replace(logic.sample_event(), magnitude=1.0)

        async def scenario():
            self.assertFalse(await runtime._dispatch(weak, runtime.config))

        asyncio.run(scenario())
        row = runtime.store.recent(1)[0]
        self.assertEqual(row['status'], 'filtered')

    def test_push_records_each_target(self):
        runtime = plugin.Runtime(base=self.base, source=FakeSource())
        runtime.prepare()
        runtime.store.record_event(event_key='eq:1', kind='earthquake')

        class FakeBot:
            async def send_group_msg(self, *, group_id, message):
                return None

            async def send_private_msg(self, *, user_id, message):
                raise RuntimeError('private blocked')

        async def scenario():
            with patch.object(plugin, 'get_bots', lambda: {'bot': FakeBot()}):
                sent = await runtime._push('文本', runtime.config, event_key='eq:1')
            self.assertTrue(sent, '群发送成功即视为已推送')

        asyncio.run(scenario())
        row = runtime.store.recent(1)[0]
        self.assertEqual((row['pushes'], row['push_ok'], row['push_fail']), (2, 1, 1))

    def test_targets_and_status(self):
        runtime = self.runtime(FakeSource(), [])
        self.assertEqual(runtime._targets(runtime.config), [
            ('group', '123456789'),
            ('private', '987654321'),
        ])
        status = runtime.status()
        self.assertTrue(status['enabled'])
        self.assertTrue(status['earthquake_free'])
        self.assertIn('last_poll', status)
        self.assertIn('CENC', plugin._format_status(runtime))


if __name__ == '__main__':
    unittest.main()
