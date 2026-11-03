# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""渲染用例：PNG 产出、尺寸、脱敏与退化路径。

不断言像素内容（那种用例一改配色就红），只断言「出一张能看的图」需要的
不变量：格式、宽度、高度随内容变化、缺字段不崩、字体能找到。
"""

import os
import sys
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image

import status_image as image
import status_logic as logic


def view(**overrides):
    snapshot = {
        'uptime': 3600 * 30,
        'cpu': {'percent': 37.4, 'cores': 8, 'threads': 16, 'brand': 'Test CPU', 'freq_mhz': 3800},
        'memory': {'percent': 61.2, 'used': 9 * 1024 ** 3, 'total': 16 * 1024 ** 3},
        'swap': {'percent': 12.0, 'used': 512 * 1024 ** 2, 'total': 4 * 1024 ** 3},
        'disks': [
            {'path': '/', 'used': 412 * 1024 ** 3, 'total': 512 * 1024 ** 3, 'percent': 80.5},
            {'path': '/data', 'used': 180 * 1024 ** 3, 'total': 1024 ** 4, 'percent': 17.6},
        ],
        'disk_io': {'read_rate': 12 * 1024 ** 2, 'write_rate': 3 * 1024 ** 2},
        'network': {
            'up_rate': 1.2 * 1024 ** 2, 'down_rate': 8.4 * 1024 ** 2,
            'sent_total': 240 * 1024 ** 3, 'recv_total': 1024 ** 4,
            'month_up': 88 * 1024 ** 3, 'month_down': 420 * 1024 ** 3,
            'month_label': '10月', 'interface': 'eth0',
            'history': [{'ts': float(index), 'up': index * 1000.0, 'down': index * 4000.0} for index in range(30)],
        },
        'process': {
            'total': 312, 'running': 41,
            'top': [{'name': f'proc{index}', 'pid': index, 'cpu': 40 - index, 'mem': 10 - index} for index in range(8)],
        },
        'system': [
            {'label': '主机名', 'value': 'denia-prod'},
            {'label': '系统', 'value': 'Ubuntu 24.04 LTS'},
            {'label': '内核', 'value': '6.8.0'},
            {'label': 'Python', 'value': '3.12.3'},
            {'label': '开机', 'value': '2026-10-27 16:05'},
            {'label': '负载', 'value': '0.82 / 0.64 / 0.51'},
        ],
        'bot': {
            'nick': '达妮娅', 'self_id': '10000001', 'online': True,
            'protocol': 'SnowLuma', 'protocol_version': '1.3.2',
            'friends': 128, 'groups': 12, 'members': 4381, 'sent': 18420, 'recv': 27633,
        },
        'websites': [{'name': '博客', 'status': 'HTTP 200', 'delay_ms': 84, 'ok': True}],
    }
    snapshot.update(overrides)
    return logic.build_view(snapshot, now=1700000000)


def open_png(payload: bytes) -> Image.Image:
    return Image.open(BytesIO(payload))


class StatusCardTest(unittest.TestCase):
    def test_website_readings_are_visible_and_have_space(self):
        current = view()
        baseline = image.render_status_card(current)
        without_sites = image.render_status_card(view(websites=[]))
        self.assertGreater(open_png(baseline).height, open_png(without_sites).height)
        for field, changed in [('name', 'API'), ('status', 'HTTP 503'),
                               ('delay', '920ms'), ('ok', False)]:
            with self.subTest(field=field):
                changed_view = view()
                changed_view['websites'][0][field] = changed
                self.assertNotEqual(image.render_status_card(changed_view), baseline)

    def test_renders_png_with_requested_width(self):
        png = image.render_status_card(view(), width=1170)
        self.assertTrue(png.startswith(b'\x89PNG'))
        rendered = open_png(png)
        self.assertEqual(rendered.format, 'PNG')
        self.assertEqual(rendered.width, 1170)
        self.assertGreater(rendered.height, 900)

    def test_width_is_clamped(self):
        narrow = open_png(image.render_status_card(view(), width=100))
        self.assertEqual(narrow.width, 760, '过窄的宽度会被夹到下限')
        wide = open_png(image.render_status_card(view(), width=99999))
        self.assertEqual(wide.width, 2880, '过宽的宽度会被夹到上限')

    def test_compact_is_shorter(self):
        normal = open_png(image.render_status_card(view(), width=1170))
        compact = open_png(image.render_status_card(view(compact=True), width=1170))
        self.assertLess(compact.height, normal.height)

    def test_redaction_cannot_leak_hostname(self):
        redacted = view()
        logic.apply_redaction(redacted, ['hostname', 'process'])
        png = image.render_status_card(redacted)
        self.assertTrue(png.startswith(b'\x89PNG'))
        # 渲染层拿到的就是脱敏后的视图：这里直接断言数据，不靠图像识别
        self.assertEqual(redacted['system'][0]['value'], logic.HIDDEN)
        self.assertEqual(redacted['process']['top'][0]['name'], logic.HIDDEN)

    def test_missing_optional_sections_do_not_crash(self):
        bare = view(
            disks=[],
            websites=[],
            process={'total': None, 'running': None, 'top': []},
            system=[],
            network={'up_rate': 0, 'down_rate': 0, 'history': []},
        )
        png = image.render_status_card(bare)
        self.assertGreater(open_png(png).height, 400)

    def test_no_history_renders_note(self):
        without_chart = view(network={
            'up_rate': 0, 'down_rate': 0, 'month_label': '10月', 'history': [],
        })
        png = image.render_status_card(without_chart)
        self.assertTrue(png.startswith(b'\x89PNG'))

    def test_broken_avatar_bytes_fall_back_to_placeholder(self):
        broken = view()
        broken['bot']['avatar'] = b'not an image'
        self.assertTrue(image.render_status_card(broken).startswith(b'\x89PNG'))

    def test_bundled_background_changes_header_without_changing_data_panels(self):
        rendered = open_png(image.render_status_card(view()))
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            with patch.object(image, '_BACKGROUND_FILE', Path(tmp) / 'missing.jpg', create=True):
                fallback = open_png(image.render_status_card(view()))
        self.assertEqual(rendered.size, fallback.size)
        self.assertTrue(rendered.crop((0, 0, 1170, 500)).tobytes() !=
                        fallback.crop((0, 0, 1170, 500)).tobytes(), '背景应出现在顶部')
        self.assertEqual(rendered.crop((0, 1000, 1170, rendered.height)).tobytes(),
                         fallback.crop((0, 1000, 1170, fallback.height)).tobytes())

    def test_missing_and_corrupt_backgrounds_both_render_the_plain_fallback(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            target = Path(tmp) / 'background.jpg'
            with patch.object(image, '_BACKGROUND_FILE', target, create=True):
                missing = image.render_status_card(view())
                target.write_bytes(b'not a jpeg')
                corrupt = image.render_status_card(view())
        self.assertTrue(missing.startswith(b'\x89PNG'))
        self.assertEqual(missing, corrupt)

    def test_background_loads_independently_of_working_directory(self):
        expected = image.render_status_card(view())
        previous = Path.cwd()
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            try:
                os.chdir(tmp)
                actual = image.render_status_card(view())
            finally:
                os.chdir(previous)
        self.assertEqual(expected, actual)

    def test_requested_width_scales_the_whole_layout(self):
        standard = open_png(image.render_status_card(view()))
        for width in (760, 2880):
            scaled = open_png(image.render_status_card(view(), width=width))
            self.assertEqual(scaled.width, width)
            self.assertAlmostEqual(scaled.height / standard.height, width / 1170, delta=.001)


class ReportImageTest(unittest.TestCase):
    def report(self, **overrides):
        payload = {
            'kind': 'daily',
            'title': '服务器日报',
            'subtitle': '2026-10-30 · 昨日',
            'host': 'denia-prod',
            'sections': [
                {'key': 'resource', 'title': '资源趋势', 'rows': [('CPU 均值 / 峰值', '36.2% / 59.0%')],
                 'chart': {'points': [(1, 10.0), (2, 20.0), (3, 15.0)], 'unit': '%', 'label': 'CPU'}},
                {'key': 'qq', 'title': 'QQ 统计', 'rows': [('好友', '128'), ('昨日接收', '633')]},
                {'key': 'website', 'title': '网站延迟',
                 'table': {'headers': ('站点', '可用率'), 'rows': [('博客', '100.0%'), ('网盘', '0.0%')]}},
            ],
            'footer': '本地 Pillow 渲染',
        }
        payload.update(overrides)
        return payload

    def test_renders_png(self):
        png = image.render_report(self.report())
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertEqual(open_png(png).width, 1170)

    def test_more_sections_are_taller(self):
        short = image.render_report_pages(self.report())
        tall = image.render_report_pages(self.report(sections=self.report()['sections'] * 3))
        self.assertGreater(sum(open_png(page).height for page in tall), sum(open_png(page).height for page in short))

    def test_empty_sections_still_renders(self):
        png = image.render_report(self.report(sections=[]))
        self.assertTrue(png.startswith(b'\x89PNG'))


if __name__ == '__main__':
    unittest.main()
