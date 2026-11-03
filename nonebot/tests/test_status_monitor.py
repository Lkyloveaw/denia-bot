# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""服务监测用例：配置整理、判定状态机、探测实现。

探测部分用本机的临时 TCP 端口做真实连接，不起外网——用例要能离线跑。
HTTP 探测只验证「配置错误要标出来」和 URL 脱敏，不依赖任何外部站点。
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import status_monitor as monitor


class NormalizeTest(unittest.TestCase):
    def test_invalid_items_are_kept_with_reason(self):
        items = monitor.normalize_items([
            {'type': 'http', 'name': '缺网址'},
            {'type': 'tcp', 'name': '端口越界', 'port': 70000},
            {'type': 'docker', 'name': '缺容器'},
            {'type': 'nonsense', 'name': '未知类型'},
        ])
        self.assertEqual(len(items), 4, '不可用的条目也要保留，诊断命令要能列出来')
        for item in items:
            self.assertTrue(item['invalid'])

    def test_aliases_are_accepted(self):
        items = monitor.normalize_items([
            {'kind': 'systemd', 'name': 'nginx', 'service': 'nginx.service'},
            {'type': 'process', 'name': '机器人', 'match': 'python3'},
        ])
        self.assertEqual(items[0]['unit'], 'nginx.service')
        self.assertEqual(items[1]['pattern'], 'python3')
        self.assertFalse(items[0]['invalid'])
        self.assertFalse(items[1]['invalid'])

    def test_duplicate_names_collapse_to_one(self):
        items = monitor.normalize_items([
            {'type': 'tcp', 'name': '端口', 'port': 80},
            {'type': 'tcp', 'name': '端口', 'port': 443},
        ])
        self.assertEqual(len(items), 1, '同名项共用同一个状态键，必须去重')
        self.assertEqual(items[0].get('duplicates'), 1)
        self.assertFalse(items[0]['invalid'], '保留下来的那条本身是有效的')

    def test_disabled_items_are_marked(self):
        items = monitor.normalize_items([{'type': 'tcp', 'name': '停用', 'port': 80, 'enabled': False}])
        self.assertFalse(items[0]['enabled'])


class EvaluateTest(unittest.TestCase):
    def test_single_failure_is_not_enough(self):
        decision = monitor.evaluate_monitor(None, False, fail_threshold=2, now=100)
        self.assertFalse(decision['alert'])
        decision = monitor.evaluate_monitor(decision['state'], False, fail_threshold=2, now=160)
        self.assertTrue(decision['alert'])

    def test_recovery_duration(self):
        state = monitor.evaluate_monitor(None, False, fail_threshold=1, now=100)['state']
        decision = monitor.evaluate_monitor(state, True, fail_threshold=1, now=700)
        self.assertTrue(decision['recovery'])
        self.assertAlmostEqual(decision['duration'], 600)

    def test_success_resets_failure_count(self):
        state = monitor.evaluate_monitor(None, False, fail_threshold=3, now=0)['state']
        self.assertEqual(state['fails'], 1)
        state = monitor.evaluate_monitor(state, True, fail_threshold=3, now=1)['state']
        self.assertEqual(state['fails'], 0)

    def test_cooldown_prevents_immediate_realert(self):
        state = monitor.evaluate_monitor(None, False, fail_threshold=1, cooldown_seconds=600, now=100)['state']
        state = monitor.evaluate_monitor(state, True, fail_threshold=1, now=200)['state']
        decision = monitor.evaluate_monitor(state, False, fail_threshold=1, cooldown_seconds=600, now=300)
        self.assertFalse(decision['alert'])
        decision = monitor.evaluate_monitor(decision['state'], False, fail_threshold=1, cooldown_seconds=600, now=800)
        self.assertTrue(decision['alert'])


class SilenceTest(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertIsNone(monitor.parse_silence(''))
        self.assertIsNone(monitor.parse_silence('明天'))
        until = monitor.parse_silence('2026-10-30 22:00')
        self.assertIsNotNone(until)
        self.assertTrue(monitor.should_silence('2026-10-30 22:00', now=until.replace(hour=21)))
        self.assertFalse(monitor.should_silence('2026-10-30 22:00', now=until.replace(hour=23)))
        self.assertFalse(monitor.should_silence('', now=until))


class ProbeTest(unittest.TestCase):
    def test_tcp_success_and_failure(self):
        async def scenario():
            server = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
            port = server.sockets[0].getsockname()[1]
            try:
                ok = await monitor.probe({'kind': 'tcp', 'name': '本地', 'host': '127.0.0.1', 'port': port, 'timeout': 3})
                closed = await monitor.probe({'kind': 'tcp', 'name': '关闭的端口', 'host': '127.0.0.1', 'port': 1, 'timeout': 2})
                return ok, closed
            finally:
                server.close()
                await server.wait_closed()

        ok, closed = asyncio.run(scenario())
        self.assertTrue(ok['ok'])
        self.assertIn('TCP', ok['detail'])
        self.assertFalse(closed['ok'])

    def test_invalid_item_reports_config_error(self):
        result = asyncio.run(monitor.probe({'kind': 'http', 'name': '缺网址', 'invalid': '缺少网址'}))
        self.assertFalse(result['ok'])
        self.assertTrue(result['config_error'])
        self.assertIn(monitor.CONFIG_ERROR_PREFIX, result['detail'])

    def test_safe_target_strips_credentials(self):
        cleaned = monitor._safe_target('https://user:token@example.com/path?api_key=secret#frag')
        self.assertNotIn('token', cleaned)
        self.assertNotIn('api_key', cleaned)
        self.assertEqual(cleaned, 'https://example.com/path')


if __name__ == '__main__':
    unittest.main()
