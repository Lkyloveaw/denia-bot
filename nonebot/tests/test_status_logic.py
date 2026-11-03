# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""状态卡纯逻辑层用例。

这一层不依赖 nonebot、psutil 或任何真实硬件，所以整份用例应当秒级跑完。
覆盖格式化、视图装配与脱敏、两个状态机、告警聚合、报表聚合和时间/目标解析。
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import status_logic as logic


def snapshot(**overrides):
    base = {
        'uptime': 3600 * 30,
        'cpu': {'percent': 37.44, 'cores': 8, 'threads': 16, 'brand': 'Test CPU', 'freq_mhz': 3800},
        'memory': {'percent': 61.2, 'used': 9 * 1024 ** 3, 'total': 16 * 1024 ** 3},
        'swap': {'percent': 12.0, 'used': 512 * 1024 ** 2, 'total': 4 * 1024 ** 3},
        'disks': [{'path': '/', 'used': 412 * 1024 ** 3, 'total': 512 * 1024 ** 3, 'percent': 80.5}],
        'disk_io': {'read_rate': 1024 ** 2, 'write_rate': 2048},
        'network': {
            'up_rate': 1.2 * 1024 ** 2,
            'down_rate': 8.4 * 1024 ** 2,
            'sent_total': 240 * 1024 ** 3,
            'recv_total': 1024 ** 4,
            'month_up': 88 * 1024 ** 3,
            'month_down': 420 * 1024 ** 3,
            'month_label': '10月',
            'interface': 'eth0',
            'history': [{'ts': 1.0, 'up': 10.0, 'down': 20.0}, (2.0, 30.0, 40.0)],
        },
        'process': {'total': 312, 'running': 41, 'top': [{'name': 'python3', 'pid': 1, 'cpu': 41.2, 'mem': 12.4}]},
        'system': [{'label': '主机名', 'value': 'denia-prod'}, {'label': '系统', 'value': 'Ubuntu'}],
        'bot': {
            'nick': '达妮娅', 'self_id': '10000001', 'online': True,
            'protocol': 'SnowLuma', 'protocol_version': '1.3.2',
            'friends': 128, 'groups': 12, 'members': 4381, 'sent': 18420, 'recv': 27633,
        },
        'websites': [{'name': '博客', 'status': 'HTTP 200', 'delay_ms': 84, 'ok': True}],
    }
    base.update(overrides)
    return base


class FormatTest(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(logic.format_bytes(0), '0 B')
        self.assertEqual(logic.format_bytes(1024), '1.00 KB')
        self.assertEqual(logic.format_bytes(1024 ** 3 * 1.5), '1.50 GB')
        self.assertEqual(logic.format_bytes(None), '--')
        self.assertEqual(logic.format_bytes(-1), '--')
        self.assertEqual(logic.format_bytes(float('nan')), '--')

    def test_speed_and_duration(self):
        self.assertEqual(logic.format_speed(1024), '1.00 KB/s')
        self.assertEqual(logic.format_speed(None), '--')
        self.assertEqual(logic.format_duration(45), '45 秒')
        self.assertEqual(logic.format_duration(90), '1 分 30 秒')
        self.assertEqual(logic.format_duration(3600 + 120), '1 小时 2 分')
        self.assertEqual(logic.format_duration(86400 * 3 + 7200), '3 天 2 小时')
        self.assertEqual(logic.format_duration(None), '--')

    def test_levels(self):
        self.assertEqual(logic.percent_level(10), 'good')
        self.assertEqual(logic.percent_level(70), 'warn')
        self.assertEqual(logic.percent_level(95), 'bad')
        self.assertEqual(logic.percent_level(None), 'good')
        self.assertEqual(logic.format_pct(None), '--')


class ViewTest(unittest.TestCase):
    def test_build_view_formats_and_orders(self):
        view = logic.build_view(snapshot(), now=1700000000)
        self.assertEqual([item['key'] for item in view['resources']], ['cpu', 'memory', 'swap'])
        self.assertEqual(view['resources'][0]['percent'], 37.4)
        self.assertIn('8 核', view['resources'][0]['detail'])
        self.assertEqual(view['disks'][0]['percent_text'], '80.5%')
        self.assertEqual(view['disks'][0]['level'], 'warn')
        self.assertEqual(view['network']['up'], '1.20 MB/s')
        self.assertEqual(view['bot']['online_text'], '在线')
        self.assertEqual(view['uptime'], '1 天 6 小时')
        self.assertEqual(len(view['network']['chart']), 2)

    def test_redaction_is_independent_and_display_only(self):
        view = logic.build_view(snapshot(), redact=['hostname', 'process'], now=1)
        self.assertEqual(view['system'][0]['value'], logic.HIDDEN)
        self.assertEqual(view['process']['top'][0]['name'], logic.HIDDEN)
        # 网络与 QQ 未勾选 → 必须照常显示
        self.assertEqual(view['network']['up'], '1.20 MB/s')
        self.assertEqual(view['bot']['friends'], 128)

        view = logic.build_view(snapshot(), redact=['network', 'qq_stats'], now=1)
        self.assertEqual(view['network']['up'], '--')
        self.assertEqual(view['network']['chart'], [])
        self.assertIsNone(view['bot']['friends'])
        # 主机名未勾选 → 仍然可见
        self.assertEqual(view['system'][0]['value'], 'denia-prod')

    def test_compact_keeps_system_panel_out(self):
        view = logic.build_view(snapshot(compact=True), now=1)
        self.assertTrue(view['compact'])

    def test_network_redaction_hides_boot_totals_without_erasing_samples(self):
        raw = snapshot()
        hidden = logic.build_view(raw, redact=['network'], now=1)
        self.assertEqual(hidden['network']['up_total'], '--')
        self.assertEqual(hidden['network']['down_total'], '--')
        self.assertEqual(hidden['websites'], [])
        visible = logic.build_view(raw, now=1)
        self.assertEqual(visible['network']['up_total'], '240.00 GB')
        self.assertEqual(visible['network']['down_total'], '1.00 TB')
        self.assertEqual(visible['websites'][0]['name'], '博客')

    def test_website_missing_delay_is_unknown_instead_of_zero(self):
        sites = [{'name': '超时站点', 'status': '探测超时', 'ok': False},
                 {'name': '故障站点', 'status': 'HTTP 503', 'delay_ms': None, 'ok': False},
                 {'name': '快速站点', 'status': 'HTTP 200', 'delay_ms': 0.0, 'ok': True}]
        current = logic.build_view(snapshot(websites=sites), now=1)
        self.assertEqual([site['delay'] for site in current['websites']], ['--', '--', '0ms'])

    def test_summary_text_states_scope(self):
        text = logic.summary_text(logic.build_view(snapshot(), now=1))
        self.assertIn('CPU', text)
        self.assertIn('磁盘', text)
        self.assertIn('运行', text)


class ResourceRuleTest(unittest.TestCase):
    def test_needs_sustained_samples(self):
        state = None
        for _ in range(2):
            decision = logic.evaluate_rule(state, 90, 85, sustain_samples=3, now=0)
            state = decision['state']
            self.assertFalse(decision['alert'])
        decision = logic.evaluate_rule(state, 90, 85, sustain_samples=3, now=1)
        self.assertTrue(decision['alert'])
        self.assertTrue(decision['state']['alerting'])

    def test_recovery_carries_duration(self):
        state = logic.evaluate_rule(None, 95, 85, sustain_samples=1, now=100)['state']
        decision = logic.evaluate_rule(state, 10, 85, sustain_samples=1, now=160)
        self.assertTrue(decision['recovery'])
        self.assertAlmostEqual(decision['duration'], 60)
        self.assertFalse(decision['state']['alerting'])

    def test_cooldown_suppresses_then_allows(self):
        # 用真实纪元量级的时间戳：last_sent=0 表示「从没发过」，不是「刚发过」
        state = logic.evaluate_rule(None, 95, 85, sustain_samples=1, cooldown_seconds=1800, now=1000)['state']
        self.assertTrue(state['alerting'])
        state = logic.evaluate_rule(state, 10, 85, sustain_samples=1, now=1010)['state']
        # 冷却窗口内再次超限：不报
        for step in range(3):
            decision = logic.evaluate_rule(state, 95, 85, sustain_samples=1, cooldown_seconds=1800, now=1020 + step)
            state = decision['state']
            self.assertFalse(decision['alert'])
        # 冷却到期后恢复上报
        decision = logic.evaluate_rule(state, 95, 85, sustain_samples=1, cooldown_seconds=1800, now=3000)
        self.assertTrue(decision['alert'])

    def test_missing_value_does_not_reset_counter(self):
        state = logic.evaluate_rule(None, 90, 85, sustain_samples=3, now=0)['state']
        decision = logic.evaluate_rule(state, None, 85, sustain_samples=3, now=1)
        self.assertEqual(decision['state']['count'], 1)
        self.assertFalse(decision['alert'])


class OfflineRuleTest(unittest.TestCase):
    def test_probe_failure_is_not_offline(self):
        state = {'offline': False, 'offline_since': 0.0, 'probe_failures': 0}
        for step in range(5):
            decision = logic.evaluate_offline(state, None, now=step)
            state = decision['state']
            self.assertFalse(decision['offline'])
        self.assertEqual(decision['probe_failures'], 5)
        self.assertTrue(decision['warn'])

    def test_probe_warning_is_not_repeated_every_round(self):
        state = {'offline': False, 'offline_since': 0.0, 'probe_failures': 0}
        warned = []
        for index in range(35):
            decision = logic.evaluate_offline(state, None, now=index, failure_warn_at=5)
            state = decision['state']
            if decision['warn']:
                warned.append(index + 1)
        self.assertEqual(warned, [5, 30], '只在越过阈值时提示，之后每 30 次提醒一次')

    def test_offline_then_recovery(self):
        state = None
        decision = logic.evaluate_offline(state, False, now=100)
        self.assertTrue(decision['offline'])
        state = decision['state']
        decision = logic.evaluate_offline(state, False, now=200)
        self.assertFalse(decision['offline'], '掉线只报一次')

        state = decision['state']
        decision = logic.evaluate_offline(state, True, now=400)
        self.assertTrue(decision['recovered'])
        self.assertAlmostEqual(decision['duration'], 300)


class AggregatorTest(unittest.TestCase):
    def test_first_immediate_then_merge(self):
        agg = logic.AlertAggregator(window_seconds=120)
        self.assertTrue(agg.add({'name': 'CPU'}, now=0))
        self.assertFalse(agg.add({'name': '内存'}, now=10))
        self.assertFalse(agg.add({'name': '磁盘'}, now=20))
        self.assertEqual(agg.take(now=60), [], '窗口没到不该吐出来')
        pending = agg.take(now=130)
        self.assertEqual([item['name'] for item in pending], ['内存', '磁盘'])
        self.assertEqual(agg.take(now=140), [])

    def test_window_zero_disables_merge(self):
        agg = logic.AlertAggregator(window_seconds=0)
        self.assertTrue(agg.add({'name': 'A'}, now=0))
        self.assertTrue(agg.add({'name': 'B'}, now=0))

    def test_summary_line(self):
        agg = logic.AlertAggregator()
        text = agg.summary([{'name': 'CPU'}, {'name': '内存'}])
        self.assertIn('2 项', text)


class ReportAggregationTest(unittest.TestCase):
    def test_resources_avg_and_peak(self):
        rows = [
            {'ts': 1, 'cpu': 10, 'mem': 50, 'swap': 0, 'disk': 40, 'net_up': 100, 'net_down': 200},
            {'ts': 2, 'cpu': 30, 'mem': 70, 'swap': 5, 'disk': 60, 'net_up': 300, 'net_down': 400},
        ]
        stat = logic.aggregate_resources(rows)
        self.assertAlmostEqual(stat['cpu_avg'], 20)
        self.assertEqual(stat['cpu_max'], 30)
        self.assertEqual(stat['disk_max'], 60)
        self.assertEqual(len(stat['points']), 2)

    def test_websites_uptime_ignores_failed_delay(self):
        rows = [
            {'name': '博客', 'ok': True, 'delay_ms': 90},
            {'name': '博客', 'ok': False, 'delay_ms': 0},
        ]
        sites = logic.aggregate_websites(rows)
        self.assertEqual(sites[0]['total'], 2)
        self.assertAlmostEqual(sites[0]['uptime'], 50.0)
        self.assertEqual(sites[0]['avg_ms'], 90)

    def test_processes_merge_by_name(self):
        rows = [
            {'kind': 'cpu', 'name': 'chrome', 'value': 20},
            {'kind': 'cpu', 'name': 'chrome', 'value': 35},
            {'kind': 'mem', 'name': 'postgres', 'value': 9},
        ]
        merged = logic.aggregate_processes(rows, top_n=5)
        self.assertEqual(merged['cpu'][0], {'name': 'chrome', 'value': 35})
        self.assertEqual(merged['mem'][0]['name'], 'postgres')

    def test_events_pair_recovery(self):
        rows = [
            {'ts': 100, 'kind': 'resource', 'title': 'CPU 使用率', 'resolved_ts': 0},
            {'ts': 160, 'kind': 'recovery', 'title': 'CPU 使用率', 'resolved_ts': 0},
        ]
        summary = logic.summarize_events(rows)
        self.assertEqual(summary['total'], 1)
        self.assertEqual(summary['unresolved'], 1)
        self.assertAlmostEqual(summary['downtime'], 60)

    def test_events_prefer_resolved_ts(self):
        rows = [{'ts': 100, 'kind': 'resource', 'title': '内存使用率', 'resolved_ts': 400}]
        summary = logic.summarize_events(rows)
        self.assertAlmostEqual(summary['downtime'], 300)
        self.assertEqual(summary['unresolved'], 0)

    def test_qq_trend_uses_snapshot_delta(self):
        rows = [
            {'day': '2026-10-29', 'friends': 127, 'groups': 12, 'members': 4380, 'recv_total': 27000, 'sent_total': 18000},
            {'day': '2026-10-30', 'friends': 128, 'groups': 12, 'members': 4381, 'recv_total': 27633, 'sent_total': 18420},
        ]
        trend = logic.qq_trend(rows)
        self.assertEqual(trend['friends'], 128)
        self.assertEqual(trend['recv'], 633)
        self.assertEqual(trend['sent'], 420)

    def test_qq_trend_ignores_negative_delta(self):
        rows = [
            {'day': '2026-10-29', 'recv_total': 1000, 'sent_total': 900},
            {'day': '2026-10-30', 'recv_total': 10, 'sent_total': 5},
        ]
        trend = logic.qq_trend(rows)
        self.assertIsNone(trend['recv'])
        self.assertIsNone(trend['sent'])

    def test_build_report_sections(self):
        report = logic.build_report(
            'daily',
            day='2026-10-30',
            days=1,
            host='denia-prod',
            sections=['resource', 'qq', 'process'],
            resources=[{'ts': 1, 'cpu': 12, 'mem': 50, 'swap': 0, 'disk': 10, 'net_up': 1, 'net_down': 2}],
            processes=[{'kind': 'cpu', 'name': 'python3', 'value': 40}],
            snapshots=[{'day': '2026-10-30', 'friends': 1, 'groups': 2, 'members': 3, 'recv_total': 10, 'sent_total': 5}],
        )
        keys = [section['key'] for section in report['sections']]
        self.assertEqual(keys, ['resource', 'qq', 'process'])
        self.assertEqual(report['title'], '服务器日报')
        process_section = report['sections'][2]
        self.assertEqual(process_section['table']['rows'][0][0], 'python3')
        self.assertEqual(process_section['table']['rows'][0][2], '--', '没有内存采样时该列显示 -- 而不是空串')


class MiscTest(unittest.TestCase):
    def test_parse_hhmm(self):
        self.assertEqual(logic.parse_hhmm('21:00'), (21, 0))
        self.assertEqual(logic.parse_hhmm('21：05'), (21, 5))
        self.assertIsNone(logic.parse_hhmm('24:00'))
        self.assertIsNone(logic.parse_hhmm('abc'))
        self.assertIsNone(logic.parse_hhmm(''))

    def test_seconds_until_rolls_to_tomorrow(self):
        now = datetime(2026, 10, 30, 22, 0)
        self.assertAlmostEqual(logic.seconds_until(now, 23, 0), 3600)
        self.assertAlmostEqual(logic.seconds_until(now, 21, 0), 23 * 3600)

    def test_daily_marker(self):
        self.assertFalse(logic.daily_marker_due('', '2026-10-30'))
        self.assertFalse(logic.daily_marker_due('2026-10-30', '2026-10-30'))
        self.assertTrue(logic.daily_marker_due('2026-10-29', '2026-10-30'))
        # 首次安装当天不补发
        self.assertFalse(logic.daily_marker_due('2026-10-29', '2026-10-30', first_day='2026-10-30'))

    def test_parse_targets(self):
        self.assertEqual(logic.parse_target('123456'), {'kind': 'group', 'id': 123456})
        self.assertEqual(logic.parse_target('GroupMessage:123'), {'kind': 'group', 'id': 123})
        self.assertEqual(logic.parse_target('private:456'), {'kind': 'private', 'id': 456})
        self.assertIsNone(logic.parse_target('nonsense'))
        parsed = logic.parse_targets(['123', 'group:123', 'private:9'])
        self.assertEqual(len(parsed), 2, '同目标去重')

    def test_month_traffic_resets_and_survives_reboot(self):
        state = logic.update_month_traffic(None, 1000, 2000, now=datetime(2026, 10, 30))
        self.assertEqual(state['month'], '2026-10')
        state = logic.update_month_traffic(state, 1500, 2600, now=datetime(2026, 10, 30))
        self.assertEqual(state['month_up'], 500)
        self.assertEqual(state['month_down'], 600)
        # 计数器回退（重启）不把负数加进去
        state = logic.update_month_traffic(state, 10, 20, now=datetime(2026, 10, 30))
        self.assertEqual(state['month_up'], 500)
        # 跨月归零
        state = logic.update_month_traffic(state, 10, 20, now=datetime(2026, 11, 1))
        self.assertEqual(state['month_up'], 0)

    def test_network_rates_clock_rollback(self):
        self.assertEqual(logic.network_rates({'sent': 100, 'recv': 200}, {'sent': 200, 'recv': 400}, 2), (50.0, 100.0))
        self.assertEqual(logic.network_rates({'sent': 100}, {'sent': 10}, 2), (0.0, 0.0), '时钟回拨不该算出负速率')
        self.assertEqual(logic.network_rates(None, {'sent': 1}, 1), (0.0, 0.0))

    def test_month_label(self):
        self.assertEqual(logic.month_label('2026-03'), '3月')
        self.assertEqual(logic.month_label('bad'), 'bad')


if __name__ == '__main__':
    unittest.main()
