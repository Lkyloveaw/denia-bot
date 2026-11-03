#!/usr/bin/env python3
"""PROTOTYPE ONLY — 用远程 Playwright /render 协议给四类卡片出图。"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
BASE = HERE / 'disaster-cards.html'
IMAGES = HERE / 'images'
ENDPOINT = 'http://127.0.0.1:3100/render'
SNAPSHOT_STYLE = """
<style id="prototype-snapshot">
html,body{margin:0!important;padding:0!important;background:#0b1218!important}
.proto-head,.proto-bar{display:none!important}
.stage{padding:0!important;margin:0!important;max-width:none!important}
body.snapshot .type{display:none!important}
body.snapshot.type-weather .type-weather,
body.snapshot.type-typhoon .type-typhoon,
body.snapshot.type-tsunami .type-tsunami,
body.snapshot.type-earthquake .type-earthquake{display:block!important}
body.snapshot .variant{display:none!important}
body.snapshot.variant-a .variant-a,
body.snapshot.variant-b .variant-b,
body.snapshot.variant-c .variant-c{display:flex!important}
.card{width:900px!important;height:1200px!important;max-width:none!important;margin:0!important;border-radius:0!important;box-shadow:none!important}
</style>
"""

TARGETS = [
    ('weather', 'A'), ('typhoon', 'A'), ('tsunami', 'A'), ('earthquake', 'A'),
    ('weather', 'B'), ('weather', 'C'),
]


def snapshot_html(base: str, kind: str, variant: str) -> str:
    body = (
        f'<body class="snapshot type-{kind} variant-{variant.lower()}" '
        f'data-type="{kind}" data-variant="{variant}">'
    )
    html = re.sub(r'<body[^>]*>', body, base, count=1)
    return html.replace('</head>', SNAPSHOT_STYLE + '</head>', 1)


async def render_all() -> None:
    base = BASE.read_text(encoding='utf-8')
    IMAGES.mkdir(parents=True, exist_ok=True)
    metrics = []
    async with httpx.AsyncClient(timeout=60) as client:
        for kind, variant in TARGETS:
            html = snapshot_html(base, kind, variant)
            response = await client.post(ENDPOINT, json={
                'html': html, 'width': 900, 'height': 1200, 'timeout_ms': 30000,
            })
            if response.status_code != 200:
                raise RuntimeError(f'{kind}-{variant}: HTTP {response.status_code} {response.text[:200]}')
            if not response.headers.get('content-type', '').startswith('image/'):
                raise RuntimeError(f'{kind}-{variant}: not an image')
            path = IMAGES / f'{kind}-{variant}.png'
            path.write_bytes(response.content)
            item = {
                'type': kind,
                'variant': variant,
                'file': path.name,
                'bytes': len(response.content),
                'render_ms': int(response.headers.get('x-render-ms') or 0),
                'html_chars': len(html),
            }
            metrics.append(item)
            print(json.dumps(item, ensure_ascii=False))
    (HERE / 'render-metrics.json').write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8'
    )

    # 2x2 联系表：四类主图，方便一眼比较。
    from PIL import Image, ImageDraw
    names = [('weather', 'A'), ('typhoon', 'A'), ('tsunami', 'A'), ('earthquake', 'A')]
    sheet = Image.new('RGB', (900, 1200), '#0b1218')
    for index, (kind, variant) in enumerate(names):
        tile = Image.open(IMAGES / f'{kind}-{variant}.png').convert('RGB').resize((450, 600))
        sheet.paste(tile, ((index % 2) * 450, (index // 2) * 600))
    draw = ImageDraw.Draw(sheet)
    for index, label in enumerate(('WEATHER / Poster', 'TYPHOON / Poster', 'TSUNAMI / Poster', 'EARTHQUAKE / Poster')):
        x = (index % 2) * 450 + 12
        y = (index // 2) * 600 + 12
        draw.rectangle((x - 4, y - 3, x + 8 * len(label) + 12, y + 18), fill='#000000')
        draw.text((x, y), label, fill='#f0c15a')
    sheet.save(IMAGES / 'contact-sheet.png')
    print('WROTE', IMAGES / 'contact-sheet.png')


if __name__ == '__main__':
    try:
        asyncio.run(render_all())
    except Exception as exc:
        print('RENDER_FAILED', exc)
        sys.exit(1)
