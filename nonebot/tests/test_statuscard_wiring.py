# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""接线用例：命令规则、权限、配置合并、运行时初始化与定时判定。

只验「接线有没有接对」，不重复逻辑层的断言。需要 nonebot 初始化，所以先
`nonebot.init()` 再 import 插件——与仓库里其它 wiring 用例一致。
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import nonebot

# 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）。
try:
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot

ensure_nonebot(superusers={'1000'})

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from plugins import statuscard as plugin


def event(text, user_id=1000, group_id=555):
    message = Message([MessageSegment.text(text)])
    return GroupMessageEvent(
        time=1700000000, self_id=100, post_type='message', sub_type='normal',
        user_id=user_id, group_id=group_id, message_type='group', message_id=1,
        message=message, raw_message=str(message), font=0,
        sender={'nickname': 'test', 'role': 'owner'}, to_me=False,
    )


class CommandTest(unittest.TestCase):
    def test_command_strips_prefixes(self):
        self.assertEqual(plugin._command(event('查看状态')), '查看状态')
        self.assertEqual(plugin._command(event(' /查看状态 ')), '查看状态')
        self.assertEqual(plugin._command(event('.查看状态')), '查看状态')

    def test_rule_matches_only_known_commands(self):
        self.assertTrue(asyncio.run(plugin._rule(event('查看状态'))))
        self.assertTrue(asyncio.run(plugin._rule(event('状态卡诊断'))))
        self.assertFalse(asyncio.run(plugin._rule(event('查看状态 一下'))))
        self.assertFalse(asyncio.run(plugin._rule(event('今天天气'))))

    def test_admin_only(self):
        self.assertTrue(plugin._is_admin(event('查看状态', user_id=1000)))
        self.assertFalse(plugin._is_admin(event('查看状态', user_id=2000)))

    def test_matcher_is_high_priority_and_blocking(self):
        self.assertEqual(plugin.matcher.priority, 1)
        self.assertTrue(plugin.matcher.block)


class ConfigTest(unittest.TestCase):
    def test_defaults_fill_missing_sections(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'statuscard.json'
            path.write_text(json.dumps({'alerts': {'enabled': True}}), encoding='utf-8')
            config = plugin.load_config(path)
            self.assertTrue(config['alerts']['enabled'])
            self.assertEqual(config['display']['width'], 1170, '缺的字段要补默认值')
            self.assertIn('report', config)

    def test_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'statuscard.json'
            config = plugin.load_config(path)
            config['display']['width'] = 1440
            plugin.save_config(config, path)
            again = plugin.load_config(path)
            self.assertEqual(again['display']['width'], 1440)

    def test_loaded_config_does_not_share_defaults(self):
        """改一份读出来的配置，不能影响下一份读出来的默认值。"""
        with tempfile.TemporaryDirectory() as folder:
            first = plugin.load_config(Path(folder) / 'a.json')
            first['alerts']['enabled'] = True
            first['report']['sections'].append('llm')
            second = plugin.load_config(Path(folder) / 'b.json')
            self.assertFalse(second['alerts']['enabled'])
            self.assertNotIn('llm', second['report']['sections'])

    def test_corrupt_config_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'statuscard.json'
            path.write_text('{broken', encoding='utf-8')
            config = plugin.load_config(path)
            self.assertEqual(config['display']['width'], 1170)


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.base = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_lazy_does_not_create_database(self):
        runtime = plugin.Runtime(base=self.base, lazy=True)
        self.assertIsNone(runtime.samples)
        self.assertFalse((self.base / 'statuscard.sqlite3').exists())
        with self.assertRaises(RuntimeError):
            _ = runtime.sample_store

    def test_prepare_creates_store_and_loads_counters(self):
        seed = plugin.Runtime(base=self.base, lazy=False)
        seed.counters = {'recv': 3, 'sent': 5}
        seed.flush_counters()
        runtime = plugin.Runtime(base=self.base, lazy=True)
        runtime.prepare()
        self.assertIsNotNone(runtime.samples)
        self.assertEqual(runtime.counters, {'recv': 3, 'sent': 5})

    def test_targets_fall_back_to_alerts(self):
        runtime = plugin.Runtime(base=self.base, lazy=True)
        runtime.config['alerts']['targets'] = ['123456']
        runtime.config['monitors']['targets'] = []
        self.assertEqual(runtime.targets('monitor'), [{'kind': 'group', 'id': 123456}])
        runtime.config['monitors']['targets'] = ['private:777']
        self.assertEqual(runtime.targets('monitor'), [{'kind': 'private', 'id': 777}])

    def test_counters_bump_and_flush(self):
        runtime = plugin.Runtime(base=self.base, lazy=True)
        runtime.prepare()
        runtime.bump('recv')
        runtime.bump('sent', 2)
        runtime.flush_counters()
        again = plugin.Runtime(base=self.base, lazy=False)
        self.assertEqual(again.counters, {'recv': 1, 'sent': 2})


class ScheduleTest(unittest.TestCase):
    def test_due_respects_clock_marker_and_first_day(self):
        self.assertFalse(plugin._due('', '2026-10-30', '08:59', (9, 0), ''))
        self.assertTrue(plugin._due('', '2026-10-30', '09:00', (9, 0), ''))
        self.assertFalse(plugin._due('2026-10-30', '2026-10-30', '09:00', (9, 0), ''))
        self.assertTrue(plugin._due('2026-10-29', '2026-10-30', '09:00', (9, 0), ''))
        # 首次安装当天不补发
        self.assertFalse(plugin._due('2026-10-29', '2026-10-30', '09:00', (9, 0), '2026-10-30'))

    def test_report_targets_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            runtime.config['alerts']['targets'] = ['123']
            self.assertEqual(plugin._report_targets(runtime), [{'kind': 'group', 'id': 123}])


class LifecycleTest(unittest.TestCase):
    def test_startup_creates_tasks_and_shutdown_cancels(self):
        from unittest.mock import patch

        async def idle(*_args, **_kwargs):
            await asyncio.sleep(3600)

        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            old = plugin.runtime
            plugin.runtime = runtime
            try:
                async def scenario():
                    with patch.multiple(plugin, sample_loop=idle, alert_loop=idle,
                                        monitor_loop=idle, schedule_loop=idle),                          patch.object(plugin.collect, 'prime_cpu', lambda: None),                          patch.object(plugin.collect, 'prime_process_cpu', lambda: None):
                        await plugin._startup()
                        self.assertEqual(len(runtime.tasks), 4)
                        self.assertIsNotNone(runtime.samples, '启动钩子要建采样库')
                        await asyncio.sleep(0.05)
                        await plugin._shutdown()
                asyncio.run(scenario())
                self.assertEqual(runtime.tasks, [], '关闭钩子要把任务清空')
            finally:
                plugin.runtime = old


class MonitorWiringTest(unittest.TestCase):
    def test_suppress_when_down_marks_dependent_item(self):
        from unittest.mock import patch as mock_patch

        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=False)
            runtime.prepare()
            runtime.config['monitors'].update({
                'enabled': True,
                'fail_threshold': 1,
                'items': [
                    {'type': 'tcp', 'name': '网关', 'port': 1},
                    {'type': 'http', 'name': '接口', 'url': 'http://127.0.0.1:1/',
                     'suppress_when_down': '网关'},
                ],
            })

            async def fake_probe(item, max_response_bytes=0):
                return {'ok': False, 'detail': 'connection refused', 'config_error': False}

            with mock_patch.object(plugin.monitor, 'probe', fake_probe):
                results = asyncio.run(plugin.run_monitors(runtime, now=1700000000.0, notify=True))

        by_name = {item['name']: item for item in results}
        self.assertFalse(by_name['网关']['suppressed'], '没有依赖项的一项照常告警')
        self.assertTrue(by_name['接口']['suppressed'], '依赖项失败时要抑制本项')
        # 状态照样记录：抑制的是通知，不是事实
        self.assertTrue(runtime.state.section('monitor')['接口']['alerting'])


class SendCounterTest(unittest.TestCase):
    def test_only_successful_sends_are_counted(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            async def call_api(self, action, **params):
                if action == 'send_group_msg' and params.get('fail'):
                    raise RuntimeError('network')
                self.calls.append(action)
                return {}

        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            runtime.prepare()
            old = plugin.runtime
            plugin.runtime = runtime
            try:
                bot = FakeBot()
                plugin._install_send_counter(bot)
                asyncio.run(bot.call_api('send_group_msg', group_id=1, message='x'))
                asyncio.run(bot.call_api('get_status'))
                with self.assertRaises(RuntimeError):
                    asyncio.run(bot.call_api('send_group_msg', fail=True))
                self.assertEqual(runtime.counters['sent'], 1, '只数成功发出的消息')
                plugin._install_send_counter(bot)   # 幂等，不能重复包
                asyncio.run(bot.call_api('send_private_msg', user_id=1, message='y'))
                self.assertEqual(runtime.counters['sent'], 2)
            finally:
                plugin.runtime = old


class DiagnoseTest(unittest.TestCase):
    def test_diagnose_reports_state(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            runtime.prepare()
            runtime.config['monitors']['items'] = [{'type': 'tcp', 'name': '缺端口'}]
            old = plugin.runtime
            plugin.runtime = runtime
            try:
                text = plugin.diagnose()
            finally:
                plugin.runtime = old
            self.assertIn('状态卡诊断', text)
            self.assertIn('采样库', text)
            self.assertIn('监测', text)
            self.assertIn('缺端口', text, '不可用的监测项要在诊断里点名')

    def test_push_without_bot_is_false(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = plugin.Runtime(base=Path(folder), lazy=True)
            runtime.prepare()
            ok = asyncio.run(plugin.push(runtime, [{'kind': 'group', 'id': 1}], '测试', label='测试'))
            self.assertFalse(ok)
            self.assertFalse(runtime.last_push['ok'])


if __name__ == '__main__':
    unittest.main()
