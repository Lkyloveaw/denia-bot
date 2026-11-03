"""Render the selected D theme using the production Pillow renderer and fake data."""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / 'nonebot'))
from PIL import Image
import status_image
import status_logic


def sample_view():
    gib = 1024 ** 3
    snapshot = {
        'uptime': 30 * 3600,
        'cpu': {'percent': 37.4, 'cores': 8, 'threads': 16, 'brand': 'Sample CPU', 'freq_mhz': 3800},
        'memory': {'percent': 61.2, 'used': 9.8 * gib, 'total': 16 * gib},
        'swap': {'percent': 12.0, 'used': .48 * gib, 'total': 4 * gib},
        'disks': [
            {'path': '/', 'percent': 80.5, 'used': 412 * gib, 'total': 512 * gib},
            {'path': '/data', 'percent': 17.6, 'used': 180 * gib, 'total': 1024 * gib},
        ],
        'network': {
            'up_rate': 1.2 * 1024 ** 2, 'down_rate': 8.4 * 1024 ** 2,
            'sent_total': 240 * gib, 'recv_total': 1024 * gib,
            'month_up': 88 * gib, 'month_down': 420 * gib,
            'month_label': '10月', 'interface': 'eth0',
            'history': [{'ts': i * 60, 'up': (1 + (i % 5) / 10) * 1024 ** 2,
                         'down': (4 + (i * 7 % 13) / 2) * 1024 ** 2} for i in range(30)],
        },
        'disk_io': {'read_rate': 12 * 1024 ** 2, 'write_rate': 3 * 1024 ** 2},
        'process': {'total': 312, 'running': 41, 'top': [
            {'name': name, 'cpu': cpu, 'mem': mem}
            for name, cpu, mem in [('python3', 18.2, 9.4), ('node', 8.6, 4.1),
                                   ('SnowLuma', 5.3, 3.2), ('redis-server', 1.1, .8),
                                   ('nginx', .8, .5), ('containerd', .5, .3),
                                   ('systemd', .2, .1), ('sshd', .1, .1)]
        ]},
        'system': [{'label': label, 'value': value} for label, value in [
            ('主机名', 'sample-host'), ('系统', 'Ubuntu 24.04 LTS'), ('内核', '6.8.0'),
            ('Python', '3.12.3'), ('开机', '2026-10-30 12:30'), ('负载', '0.82 / 0.64 / 0.51')]],
        'bot': {'nick': '达妮娅', 'self_id': '1000000000', 'online': True,
                'protocol': 'SnowLuma', 'protocol_version': '1.3.2',
                'friends': 128, 'groups': 12, 'members': 4381, 'recv': 27633, 'sent': 18420},
        'footer': 'D 方案实际渲染 · 全部数值均为模拟数据',
    }
    now = datetime(2026, 10, 31, 18, 30, tzinfo=timezone(timedelta(hours=8))).timestamp()
    return status_logic.build_view(snapshot, redact=['hostname', 'process'], now=now)


def main():
    regular = sample_view()
    compact = copy.deepcopy(regular)
    compact['compact'] = True
    stress = copy.deepcopy(regular)
    stress['bot']['nick'] = '达妮娅的一个非常长非常长的机器人昵称'
    stress['bot']['protocol'] = 'Long protocol name ' * 6
    stress['bot']['online'] = False
    stress['resources'][0].update(percent=97.4, level='bad')
    stress['resources'][1].update(percent=81.0, level='warn')
    stress['system'] = [{'label': '很长的系统字段名称', 'value': 'A very long system value ' * 5}] * 12
    stress['process']['top'] = [{'name': 'Long process name ' * 9, 'cpu': '99.9%', 'mem': '88.8%'}] * 10
    output = HERE / 'selected'
    output.mkdir(exist_ok=True)
    metrics = []
    for name, view, width in [('statuscard-D', regular, 1170), ('compact-D', compact, 1170),
                              ('narrow-D', regular, 760), ('stress-D', stress, 1170)]:
        payload = status_image.render_status_card(view, width=width)
        (output / f'{name}.png').write_bytes(payload)
        with Image.open(BytesIO(payload)) as im:
            metrics.append({'file': f'{name}.png', 'size': list(im.size), 'bytes': len(payload)})
    report = {
        'title': '服务器日报', 'subtitle': '2026-10-30', 'host': '已隐藏',
        'sections': [
            {'title': '资源趋势', 'rows': [('CPU 均值 / 峰值', '36.2% / 59.0%'),
                                          ('内存均值 / 峰值', '58.3% / 67.4%')],
             'chart': {'points': [(1, 10), (2, 23), (3, 18), (4, 35)], 'unit': '%', 'label': 'CPU'}},
            {'title': 'QQ 统计', 'rows': [('好友', '128'), ('昨日接收', '633')]},
        ], 'footer': '浅色主题日报样张 · 模拟数据',
    }
    (output / 'report-D.png').write_bytes(status_image.render_report(report))
    (output / 'render-metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    print(json.dumps(metrics), flush=True)


if __name__ == '__main__':
    main()
