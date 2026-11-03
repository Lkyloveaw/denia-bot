# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""集成用例：把「告警 → 事件 → 推送」「监测 → 抑制」「定时 → 记账」「报表 → 发送」
这些只有单元用例覆盖的链路跑一遍。

单元用例验的是「判定对不对」，这里验的是「接线有没有把判定接到该接的地方」。
所有外部依赖（OneBot、psutil、时钟之外的一切）都打桩，用例可离线、可重复。
"""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

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

from plugins import statuscard as plugin


def low_snapshot():
    return {'percent': 10.0, 'cores': 8, 'threads': 16, 'brand': 'Test', 'freq_mhz': 3000,
            'used': 1, 'total': 10}


def high_snapshot():
    return {'percent': 97.0, 'cores': 8, 'threads': 16, 'brand': 'Test', 'freq_mhz': 3000,
            'used': 9, 'total': 10}


class FakeBot:
    def __init__(self, online=True, raises=False):
        self.online = online
        self.raises = raises
        self.self_id = 100

    async def call_api(self, action, **params):
        if self.raises:
            raise RuntimeError('connection reset')
        if action == 'get_status':
            return {'online': self.online}
        return {}


def make_runtime(folder, **overrides):
    """按真实路径搭一个运行时：先把配置写到文件，再让 Runtime 自己加载。

    直接改 `runtime.config` 不会重建聚合器等派生对象，测出来的行为会和线上
    「改文件 → 热加载」不一致。
    """
    import status_config

    base = Path(folder)
    config = status_config.load_config(base / 'statuscard.json')
    for section, values in overrides.items():
        config.setdefault(section, {}).update(values)
    status_config.save_config(config, base / 'statuscard.json')
    runtime = plugin.Runtime(base=base, lazy=False)
    runtime.prepare()
    return runtime


class OneBotCallTrapTest(unittest.TestCase):
    """NoneBot 的 `Bot.__getattr__` 会把任意未知属性当成 API 名。

    `bot.call_action("get_status")` 因此变成 `call_api("call_action", "get_status")`
    ——3 个位置参数，直接 TypeError。线上表现就是「所有 OneBot 查询都失败」，
    而且错误信息完全指向 call_api，很难一眼看出是自己写错了方法名。
    """

    def test_unknown_attribute_becomes_an_api_call(self):
        import functools

        from nonebot import get_driver
        from nonebot.adapters.onebot.v11 import Adapter, Bot

        bot = Bot(Adapter(get_driver()), '1')
        self.assertIsInstance(bot.call_action, functools.partial)
        with self.assertRaises(TypeError):
            asyncio.run(bot.call_action('get_status'))

    def test_plugin_source_uses_call_api(self):
        source = (ROOT / 'plugins' / 'statuscard.py').read_text(encoding='utf-8')
        self.assertNotIn('bot.call_action(', source, '必须用 bot.call_api(...)，不能写成 call_action 调用')


class WebsiteDelayTest(unittest.TestCase):
    def test_failed_probe_keeps_unknown_delay(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = make_runtime(folder, display={'website_targets': [
                {'name': '站点', 'url': 'https://example.com'}]})

            async def failed_probe(item):
                return {'ok': False, 'detail': '探测超时'}

            with patch.object(plugin.monitor, 'probe', failed_probe):
                sites = asyncio.run(plugin.probe_websites(runtime, force=True))
            self.assertEqual(len(sites), 1)
            self.assertIsNone(sites[0]['delay_ms'])
            self.assertEqual(plugin.logic.build_view({'websites': sites})['websites'][0]['delay'], '--')


class ResourceAlertIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.runtime = make_runtime(
            self.folder.name,
            display={'disk_paths': ['/data']},
            alerts={'enabled': True, 'targets': ['123456'], 'sustain_samples': 1,
                    'cooldown_seconds': 0, 'aggregate_seconds': 0},
        )
        self.pushes = []

        async def fake_push(runtime, targets, message, label='推送'):
            self.pushes.append((label, message))
            return True

        self.patches = [
            patch.object(plugin, 'push', fake_push),
            patch.object(plugin, '_pick_bot', lambda: None),
            patch.object(plugin.collect, 'cpu_snapshot', high_snapshot),
            patch.object(plugin.collect, 'memory_snapshot', high_snapshot),
            patch.object(plugin.collect, 'swap_snapshot', low_snapshot),
            patch.object(plugin.collect, 'disk_snapshot', lambda paths=None: [
                {'path': '/data', 'used': 90, 'total': 100, 'percent': 90.0}]),
            patch.object(plugin.collect, 'load_per_core', lambda: None),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.folder.cleanup()

    def test_high_usage_alerts_once_and_recovers(self):
        asyncio.run(plugin.evaluate_resources(self.runtime, now=1_700_000_000))
        labels = [label for label, _ in self.pushes]
        self.assertIn('资源告警', labels)
        self.assertEqual(len(self.pushes), 3, 'CPU / 内存 / 磁盘三条阈值，各自一条告警')
        self.assertTrue(any('CPU 使用率' in str(message) for _, message in self.pushes))
        self.assertFalse(any('【异常】【异常】' in str(message) for _, message in self.pushes),
                         '告警文案不该被套两层')

        # 事件落库且未恢复
        events = self.runtime.sample_store.list_events(0)
        titles = {row['title'] for row in events}
        self.assertIn('CPU 使用率', titles)
        self.assertTrue(all(row['resolved_ts'] == 0 for row in events))

        # 冷却期内继续超限不重复发
        self.pushes.clear()
        asyncio.run(plugin.evaluate_resources(self.runtime, now=1_700_000_060))
        self.assertEqual(self.pushes, [], '告警中不该重复推送')

        # 回落 → 恢复通知 + 事件标记 resolved
        self.runtime.config['alerts']['cooldown_seconds'] = 0
        with patch.object(plugin.collect, 'cpu_snapshot', low_snapshot), \
             patch.object(plugin.collect, 'memory_snapshot', low_snapshot), \
             patch.object(plugin.collect, 'disk_snapshot', lambda paths=None: [
                 {'path': '/data', 'used': 10, 'total': 100, 'percent': 10.0}]):
            asyncio.run(plugin.evaluate_resources(self.runtime, now=1_700_000_120))
        self.assertEqual(len(self.pushes), 3, '三条告警都应收到恢复通知')
        self.assertTrue(all(label == '恢复通知' for label, _ in self.pushes))
        events = self.runtime.sample_store.list_events(0)
        self.assertTrue(any(row['kind'] == 'recovery' for row in events))
        self.assertTrue(all(row['resolved_ts'] > 0 for row in events if row['kind'] == 'resource'))

    def test_alert_not_sent_when_disabled_but_event_recorded(self):
        self.runtime.config['alerts']['enabled'] = False
        asyncio.run(plugin.evaluate_resources(self.runtime, now=1_700_000_000))
        self.assertEqual(self.pushes, [], '关掉告警就不该发消息')
        events = self.runtime.sample_store.list_events(0)
        self.assertTrue(events, '但事件要照记，日报里还要用')


class OfflineIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.runtime = make_runtime(self.folder.name, alerts={
            'enabled': True, 'targets': ['123456'], 'offline_notify': True})
        self.pushes = []

        async def fake_push(runtime, targets, message, label='推送'):
            self.pushes.append((label, str(message)))
            return True

        self.patches = [patch.object(plugin, 'push', fake_push)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.folder.cleanup()

    def test_connected_but_unqueryable_bot_stays_online(self):
        """协议端查不到状态但连接还在 —— 算在线，不报警（SnowLuma 就是这样）。"""
        with patch.object(plugin, '_pick_bot', lambda: FakeBot(raises=True)):
            for step in range(3):
                asyncio.run(plugin.check_offline(self.runtime, now=1_700_000_000 + step))
        self.assertEqual(self.pushes, [], '查询失败不等于掉线，不该发消息')
        self.assertEqual(self.runtime.state.section('offline')['probe_failures'], 0)
        self.assertFalse(self.runtime.state.section('offline')['offline'])
        self.assertIn('RuntimeError', self.runtime.state.section('offline')['last_error'],
                      '查不通的原因要留痕，诊断命令能看到')

    def test_explicit_offline_then_recovery(self):
        with patch.object(plugin, '_pick_bot', lambda: FakeBot(online=False)):
            asyncio.run(plugin.check_offline(self.runtime, now=1_700_000_100))
            asyncio.run(plugin.check_offline(self.runtime, now=1_700_000_200))
        labels = [label for label, _ in self.pushes]
        self.assertEqual(labels.count('掉线通知'), 1, '一次掉线只报一次')

        with patch.object(plugin, '_pick_bot', lambda: FakeBot(online=True)):
            asyncio.run(plugin.check_offline(self.runtime, now=1_700_000_700))
        self.assertEqual(self.pushes[-1][0], '恢复通知')
        self.assertIn('10 分', self.pushes[-1][1], '离线时长要出现在恢复通知里')


class OfflineProbeIntegrationTest(unittest.TestCase):
    """在线判定：没有 Bot ≠ 探测失败；接口缺失也不等于掉线。"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.runtime = make_runtime(self.folder.name, alerts={
            'enabled': True, 'targets': ['123456'], 'offline_notify': True})
        self.pushes = []

        async def fake_push(runtime, targets, message, label='推送'):
            self.pushes.append((label, str(message)))
            return True

        self.patches = [patch.object(plugin, 'push', fake_push)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.folder.cleanup()

    def test_missing_bot_needs_two_samples_then_recovers(self):
        with patch.object(plugin, '_pick_bot', lambda: None):
            asyncio.run(plugin.check_offline(self.runtime, now=1_700_000_000))
            self.assertEqual(self.pushes, [], '第一次当抖动，不急着报')
            asyncio.run(plugin.check_offline(self.runtime, now=1_700_000_060))
        self.assertEqual([label for label, _ in self.pushes], ['掉线通知'])

        with patch.object(plugin, '_pick_bot', lambda: FakeBot(online=True)):
            asyncio.run(plugin.check_offline(self.runtime, now=1_700_000_300))
        self.assertEqual(self.pushes[-1][0], '恢复通知')

    def test_get_status_unsupported_is_not_offline(self):
        class NoStatusBot:
            async def call_api(self, action, **params):
                if action == 'get_status':
                    raise RuntimeError('not implemented')
                return {'user_id': 100, 'nickname': 'test'}

        with patch.object(plugin, '_pick_bot', lambda: NoStatusBot()):
            for step in range(3):
                asyncio.run(plugin.check_offline(self.runtime, now=1_700_000_000 + step * 60))
        self.assertEqual(self.pushes, [], '接口缺失不该报掉线')
        self.assertEqual(self.runtime.state.section('offline')['probe_failures'], 0,
                         '用登录接口探活成功，就不算探测失败')


class ProviderWatchIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.runtime = make_runtime(self.folder.name, alerts={
            'enabled': True, 'targets': ['123456'],
            'provider_watch': {'enabled': True, 'threshold': 3, 'cooldown_seconds': 0},
        })
        self.pushes = []

        async def fake_push(runtime, targets, message, label='推送'):
            self.pushes.append((label, str(message)))
            return True

        self.patches = [patch.object(plugin, 'push', fake_push)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.folder.cleanup()

    def test_streak_triggers_and_recovers(self):
        self.runtime.state.update_section('llm', streak=5)
        asyncio.run(plugin.check_provider(self.runtime, now=1_700_000_000))
        self.assertEqual(self.pushes[0][0], 'LLM 告警')

        # 仍在失败中不重复发
        asyncio.run(plugin.check_provider(self.runtime, now=1_700_000_060))
        self.assertEqual(len(self.pushes), 1)

        self.runtime.state.update_section('llm', streak=0)
        asyncio.run(plugin.check_provider(self.runtime, now=1_700_000_120))
        self.assertEqual(self.pushes[-1][0], 'LLM 恢复')


class ReportIntegrationTest(unittest.TestCase):
    def setUp(self):
        render_patch = patch.object(plugin.image, 'render_report_pages', return_value=[b'png'])
        render_patch.start()
        self.addCleanup(render_patch.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.runtime = make_runtime(self.folder.name, report={
            'sections': ['resource', 'qq', 'token'], 'with_image': True})
        self.pushes = []

        async def fake_push(runtime, targets, message, label='推送'):
            self.pushes.append((label, message))
            return True

        self.patches = [patch.object(plugin, 'push', fake_push)]
        for item in self.patches:
            item.start()
        now = int(time.time())
        self.runtime.sample_store.add_resource(now - 60, cpu=30, mem=50, swap=1, disk=20, net_up=1, net_down=2)
        self.runtime.sample_store.record_snapshot(
            (date.today() - timedelta(days=1)).isoformat(),
            friends=1, groups=2, members=3, recv_total=10, sent_total=5)

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.folder.cleanup()

    def test_daily_report_renders_image_then_falls_back_to_text(self):
        day = date.today().isoformat()
        ok = asyncio.run(plugin.send_report(self.runtime, kind='daily', day=day,
                                            targets=[{'kind': 'group', 'id': 1}]))
        self.assertTrue(ok)
        self.assertEqual(len(self.pushes), 1)
        self.assertEqual(self.pushes[0][1].type, 'image')
        run = self.runtime.sample_store.latest_report_run(day)
        self.assertEqual(run['detail'], 'image')

        # 出图失败 → 文字摘要，且回退记录为 text
        self.pushes.clear()
        with patch.object(plugin.image, 'render_report_pages', side_effect=plugin.image.RenderError('boom')):
            ok = asyncio.run(plugin.send_report(self.runtime, kind='daily', day=day,
                                                targets=[{'kind': 'group', 'id': 1}]))
        self.assertTrue(ok)
        self.assertIsInstance(self.pushes[0][1], str)
        self.assertIn('服务器日报', self.pushes[0][1])
        self.assertEqual(self.runtime.sample_store.latest_report_run(day)['detail'], 'text')

    def test_preview_sends_only_the_report(self):
        """预览日报/周报只回一条，不再先发「生成中」之类的前置提示。"""

        class FakeEvent:
            group_id = 555

            def get_user_id(self):
                return '1000'

        with patch.object(plugin, 'runtime', self.runtime):
            asyncio.run(plugin.preview_report(FakeEvent(), kind='daily'))
        self.assertEqual([label for label, _ in self.pushes], ['daily 报表'])

    def test_report_view_contains_selected_sections(self):
        day = (date.today() - timedelta(days=1)).isoformat()
        view = plugin.build_report_view(self.runtime, kind='weekly', day=day)
        self.assertEqual(view['title'], '服务器周报')
        self.assertEqual([section['key'] for section in view['sections']], ['resource', 'qq', 'token'])


class ScheduleIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.runtime = make_runtime(
            self.folder.name,
            alerts={'enabled': True, 'targets': ['123456'],
                    'daily_status': {'enabled': True, 'time': '00:00', 'with_image': True}},
            report={'enabled': True, 'time': '00:00', 'weekly_enabled': False},
        )
        self.runtime.state.set('schedule', {'daily_status': yesterday, 'daily_report': yesterday})
        self.runtime.state.set('first_seen', '2020-01-01')
        self.runtime.state.save()
        self.calls = []

    def tearDown(self):
        self.folder.cleanup()

    def test_due_tasks_run_once_and_mark_today(self):
        async def fake_daily(runtime, *, image_on):
            self.calls.append('daily')
            return True

        async def fake_report(runtime, *, kind, day, targets):
            self.calls.append(f'report:{kind}')
            return True

        with patch.object(plugin, '_send_daily_status', fake_daily), \
             patch.object(plugin, 'send_report', fake_report):
            asyncio.run(plugin._run_schedule(self.runtime))
            asyncio.run(plugin._run_schedule(self.runtime))

        self.assertEqual(self.calls.count('daily'), 1, '同一天不该重复发')
        self.assertEqual(self.calls.count('report:daily'), 1)
        markers = self.runtime.state.section('schedule')
        self.assertEqual(markers['daily_status'], date.today().isoformat())
        self.assertEqual(markers['daily_report'], date.today().isoformat())

    def test_failed_send_still_marks_today_without_retry_storm(self):
        self.runtime.state.set('schedule', {
            'daily_status': (date.today() - timedelta(days=1)).isoformat(),
            'daily_report': date.today().isoformat(),
        })
        self.runtime.state.save()
        attempts = []

        async def failing(runtime, *, image_on):
            attempts.append(1)
            return False

        with patch.object(plugin, '_send_daily_status', failing):
            asyncio.run(plugin._run_schedule(self.runtime))
            asyncio.run(plugin._run_schedule(self.runtime))
        self.assertEqual(len(attempts), 1, '失败也只尝试一次，不能让 30 秒循环刷屏')


class PanelHttpTest(unittest.TestCase):
    """面板走真实 FastAPI 路由：登录 → CSRF → 读取 → 保存。"""

    def setUp(self):
        from fastapi.testclient import TestClient

        import dashboard as d

        self.d = d
        self.TestClient = TestClient
        self.folder = tempfile.TemporaryDirectory()
        base = Path(self.folder.name)
        d._status_cache.clear()
        self.old_auth = d.AUTH_FILE
        d.AUTH_FILE = base / 'auth.json'
        d.set_password('a-strong-test-password')
        self.patches = [
            patch.object(d, 'status_paths',
                         lambda: (base / 'statuscard.json', base / 'statuscard-state.json', base / 'statuscard.sqlite3')),
            patch.object(d.status_live, 'panel_snapshot', lambda **kw: {
                'cpu': {'percent': 12.0, 'cores': 8, 'threads': 16, 'brand': 'Test', 'freq_mhz': 3000},
                'memory': {'percent': 40.0, 'used': 1, 'total': 2},
                'swap': {'percent': 1.0, 'used': 0, 'total': 1},
                'disks': [{'path': '/', 'used': 1, 'total': 2, 'percent': 50.0}],
                'process': {'total': 1, 'running': 1, 'top': []},
                'load': '0.1 / 8 核', 'uptime': 100.0, 'collected_at': time.time(),
            }),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.d.AUTH_FILE = self.old_auth
        self.d._status_cache.clear()
        self.folder.cleanup()

    def test_requires_login_then_read_and_write(self):
        with self.TestClient(self.d.app) as client:
            self.assertEqual(client.get('/api/status/overview').status_code, 401)
            login = client.post('/api/login', json={'password': 'a-strong-test-password'})
            self.assertEqual(login.status_code, 200)

            overview = client.get('/api/status/overview?days=7')
            self.assertEqual(overview.status_code, 200)
            data = overview.json()
            self.assertEqual(data['range_days'], 7)
            self.assertEqual(data['live']['cpu']['percent'], 12.0)
            self.assertIn('options', data['config'])

            # 没有 CSRF 头 → 拒绝
            self.assertEqual(client.post('/api/status/config', json={'alerts': {'enabled': True}}).status_code, 403)

            csrf = login.json()['csrf']
            headers = {'Origin': 'http://testserver', 'X-CSRF-Token': csrf}
            saved = client.post('/api/status/config', headers=headers, json={
                'alerts': {'enabled': True, 'targets': ['123456']},
                'display': {'redact': ['network']},
                'report': {'enabled': True, 'time': '20:15'},
            })
            self.assertEqual(saved.status_code, 200)
            self.assertTrue(saved.json()['config']['alerts']['enabled'])

            reloaded = client.get('/api/status/overview').json()['config']
            self.assertEqual(reloaded['report']['time'], '20:15')
            self.assertEqual(reloaded['display']['redact'], ['network'])


if __name__ == '__main__':
    unittest.main()
