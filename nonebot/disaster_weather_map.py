"""Cached administrative outlines for weather cards, not meteorological footprints.

Boundary source: https://geo.datav.aliyun.com/areas_v3/bound/
Only numeric administrative codes are used; the renderer receives inline SVG.
"""
from __future__ import annotations

import asyncio
import html
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from disaster_china_logic import WeatherAlert

BASE_URL = 'https://geo.datav.aliyun.com/areas_v3/bound/'
CACHE_DIR = Path(__file__).resolve().parent / 'data' / 'disaster_maps'
CACHE_SECONDS = 30 * 86400
MAX_BYTES = 2_000_000
_retry_after: dict[tuple[str, str], float] = {}


def _code(value: Any) -> str:
    text = str(value or '').strip()
    return text if re.fullmatch(r'[1-9][0-9]{5}', text) else ''


def _alert_code(alert: WeatherAlert) -> str:
    explicit = _code(alert.adcode)
    if explicit:
        return explicit
    # Both county_timestamp and the observed 14-digit issuer IDs occur in Jian.
    match = re.fullmatch(r'([1-9][0-9]{5})(?:[0-9]{8})?_[0-9]{12,14}', alert.alert_id)
    return match.group(1) if match else ''


def _features(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or data.get('type') != 'FeatureCollection':
        raise ValueError('Invalid boundary collection')
    features = data.get('features')
    if not isinstance(features, list) or not 1 <= len(features) <= 1000:
        raise ValueError('Invalid boundary features')
    if any(not isinstance(f, dict) or not isinstance(f.get('properties'), dict) for f in features):
        raise ValueError('Invalid boundary metadata')
    return features


def _select(data: Any, code: str) -> dict[str, Any]:
    return next(f for f in _features(data) if _code(f['properties'].get('adcode')) == code)


def _rings(feature: dict[str, Any]):
    geometry = feature['geometry']
    if geometry['type'] == 'Polygon':
        polygons = [geometry['coordinates']]
    elif geometry['type'] == 'MultiPolygon':
        polygons = geometry['coordinates']
    else:
        raise ValueError('Unsupported boundary geometry')
    for polygon in polygons:
        for ring in polygon:
            if len(ring) < 4:
                raise ValueError('Invalid polygon ring')
            for point in ring:
                lon, lat = point[:2]
                if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -85 < lat < 85):
                    raise ValueError('Invalid boundary coordinates')
            yield ring


async def _geo(name: str, folder: Path, client: httpx.AsyncClient) -> dict[str, Any]:
    if not re.fullmatch(r'[1-9][0-9]{5}(?:_full)?\.json', name):
        raise ValueError('Invalid boundary key')
    path = folder / name
    cached = None
    try:
        if path.stat().st_size <= MAX_BYTES:
            cached = json.loads(path.read_text(encoding='utf-8'))
            _features(cached)
            if time.time() - path.stat().st_mtime < CACHE_SECONDS:
                return cached
    except (OSError, ValueError, TypeError):
        cached = None
    key = (str(folder), name)
    if time.monotonic() < _retry_after.get(key, 0):
        if cached is not None:
            return cached
        raise ValueError('Boundary source cooldown')
    try:
        async with client.stream('GET', BASE_URL + name, timeout=3.0, follow_redirects=False) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError('Boundary response too large')
        data = json.loads(content)
        _features(data)
    except (httpx.HTTPError, ValueError):
        if len(_retry_after) >= 512:
            _retry_after.clear()
        _retry_after[key] = time.monotonic() + 300
        if cached is not None:
            return cached
        raise
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # Synchronous writes are small and contain no await; readers reject partial JSON.
        path.write_bytes(content)
    except OSError:
        pass  # A read-only cache must not prevent an in-memory map.
    _retry_after.pop(key, None)
    return data


@dataclass(frozen=True)
class WeatherMap:
    code: str
    name: str
    context: str
    features: list[dict[str, Any]]

    def svg(self) -> str:
        selected = next(f for f in self.features if _code(f['properties'].get('adcode')) == self.code)
        points = [p for f in self.features for ring in _rings(f) for p in ring]
        if not points or len(points) > 150_000:
            raise ValueError('Invalid boundary size')
        cos_lat = math.cos(math.radians(sum(p[1] for p in points) / len(points)))
        min_x, max_x = min(p[0]*cos_lat for p in points), max(p[0]*cos_lat for p in points)
        min_y, max_y = min(-p[1] for p in points), max(-p[1] for p in points)
        if min_x == max_x or min_y == max_y:
            raise ValueError('Empty boundary extent')
        scale = min(276/(max_x-min_x), 155/(max_y-min_y))
        dx, dy = (320-(max_x-min_x)*scale)/2, 5+(155-(max_y-min_y)*scale)/2

        def xy(p):
            return dx+(p[0]*cos_lat-min_x)*scale, dy+(-p[1]-min_y)*scale

        def path_for(f):
            return ''.join('M'+'L'.join(f'{x:.2f},{y:.2f}' for x,y in map(xy,ring))+'Z' for ring in _rings(f))

        paths = []
        for f in sorted(self.features, key=lambda f: _code(f['properties'].get('adcode')) == self.code):
            is_target = f is selected
            attr = ' data-warning-area="true"' if is_target else ''
            fill, stroke, width = ('var(--dot)', 'var(--accent)', 1.6) if is_target else ('#dedfd5', '#fcfaf2', .65)
            paths.append(f'<path data-adcode="{_code(f["properties"].get("adcode"))}"{attr} d="{path_for(f)}" fill="{fill}" stroke="{stroke}" stroke-width="{width}" stroke-linejoin="round" fill-rule="evenodd"/>')
        center = selected['properties'].get('centroid')
        if not isinstance(center, list) or len(center) < 2 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in center[:2]):
            target_points = [p for ring in _rings(selected) for p in ring]
            center = [sum(p[i] for p in target_points)/len(target_points) for i in (0,1)]
        cx, cy = xy(center)
        label = self.name if len(self.name) <= 20 else self.name[:19]+'…'
        width = max(68, min(284, len(label)*13+16))
        label_x = max(width/2+6, min(cx, 314-width/2))
        name = html.escape(label, quote=True)
        callout = (
            f'<path d="M{cx:.2f},{cy:.2f}V171H{label_x:.2f}V177" fill="none" stroke="var(--accent)" stroke-width="1.1"/>'
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="2.7" fill="var(--accent)" stroke="#fffdf4" stroke-width="1"/>'
            f'<rect x="{label_x-width/2:.2f}" y="176" width="{width}" height="23" rx="5" fill="#fffdf5" stroke="var(--accent)" stroke-width=".8"/>'
            f'<text x="{label_x:.2f}" y="192" text-anchor="middle" font-size="13" font-weight="700" fill="var(--accent)">{name}</text>'
        )
        return f'<svg class="map-art" viewBox="0 0 320 204" role="img" aria-label="{name}预警行政区域示意"><title>{name}预警行政区域示意，不代表实际气象覆盖范围</title>{"".join(paths)}{callout}</svg>'


async def map_for_alert(alert: WeatherAlert, *, cache_dir: Path | None = None,
                        client: httpx.AsyncClient | None = None) -> WeatherMap | None:
    code = _alert_code(alert)
    if not code:
        return None
    folder = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    own = client is None
    client = client or httpx.AsyncClient(timeout=3.0)

    async def load():
        target = _select(await _geo(code+'.json', folder, client), code)
        name = str(target['properties'].get('name') or '').strip()
        if not name:
            return None
        parent_info = target['properties'].get('parent', {})
        if not isinstance(parent_info, dict):
            raise ValueError('Invalid boundary parent')
        parent = _code(parent_info.get('adcode'))
        context, features = name, [target]
        if parent and parent != '100000':
            context_feature = _select(await _geo(parent+'.json', folder, client), parent)
            context = str(context_feature['properties'].get('name') or '周边行政区')
            features = _features(await _geo(parent+'_full.json', folder, client))
            if parent == '460000' and code != '460300':
                features = [f for f in features if _code(f['properties'].get('adcode')) != '460300']
                context = '海南岛'
        result = WeatherMap(code, name, context, features)
        result.svg()  # Validate before returning a map to the card renderer.
        return result

    try:
        return await asyncio.wait_for(load(), timeout=5.0)
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError, StopIteration, RuntimeError, IndexError):
        return None
    finally:
        if own:
            await client.aclose()
