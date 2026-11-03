"""Calendar messages and bounded cover downloads, independent of NoneBot.

The optional client is a trusted transport injection (primarily for offline tests).
The owned client disables proxies and connection reuse: an IP-pinned TLS request
must verify the original hostname even when another hostname shares that IP.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import math
import os
import re
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, ImageOps

from game_event_logic import GAMES, format_event

_MIB = 1024 * 1024
_DOWNLOAD_BYTES = 8 * _MIB
_PIXELS = 20_000_000
_CACHE_BYTES = 200 * _MIB
_CACHE_AGE = 7 * 86400
_TEXT_LIMIT = 3500
_CACHE_NAME = re.compile(r'[a-f0-9]{64}\.jpg\Z')


def _public_ip(host):
    address = ipaddress.ip_address(host)
    return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)


def safe_url(url):
    """Validate URL syntax without DNS; downloads separately validate every answer."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url) or '\\' in url:
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme not in ('http', 'https') or not host or parsed.username is not None or parsed.password is not None:
            return False
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return False
        if '%' in host:
            return False
        try:
            return _public_ip(host)
        except ValueError:
            host = host.encode('idna').decode('ascii').lower().rstrip('.')
            if '.' not in host or host.endswith(('.localhost', '.local', '.internal', '.home', '.lan')):
                return False
            labels = host.split('.')
            return (len(host) <= 253 and not labels[-1].isdigit()
                    and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels))
    except (ValueError, UnicodeError):
        return False


def _clip(text, limit):
    text = str(text or '')
    return text if len(text) <= limit else text[:max(0, limit - 1)] + ('…' if limit else '')


def _text_part(text):
    # Never pass external text through a CQ-code parser.
    return {'type': 'text', 'data': {'text': text}}


def _encode_image(raw, width=720):
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > _PIXELS:
            raise ValueError('cover exceeds pixel limit')
        image.seek(0)
        image = ImageOps.exif_transpose(image)
        image.thumbnail((width, _PIXELS), Image.Resampling.LANCZOS)
        if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
            rgba = image.convert('RGBA')
            rgb = Image.new('RGB', rgba.size, 'white')
            rgb.paste(rgba, mask=rgba.getchannel('A'))
            image = rgb
        else:
            image = image.convert('RGB')
        output = io.BytesIO()
        image.save(output, format='JPEG', quality=85, optimize=True)
        return output.getvalue()


class MessageBuilder:
    def __init__(self, cache_dir, client=None, clock=time.time, dns_mode=None):
        self.cache_dir = Path(cache_dir)
        self.clock = clock
        self.dns_mode = (dns_mode if dns_mode is not None else os.getenv('GAME_EVENT_COVER_DNS', 'system')).strip().lower()
        if self.dns_mode not in ('system', 'google'):
            raise ValueError('GAME_EVENT_COVER_DNS must be system or google')
        self._owned_client = client is None
        self.client = client if client is not None else httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=10,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=0))

    async def close(self):
        if self._owned_client:
            await self.client.aclose()

    async def _resolve(self, host, port):
        if self.dns_mode == 'system':
            answers = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
            return [answer[4][0] for answer in answers]
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            pass
        # Opt-in for networks using synthetic Fake-IP DNS. Only the fixed public
        # resolver is contacted; its answers still pass the same public-IP gate.
        async with self.client.stream('GET', 'https://dns.google/resolve',
                params={'name': host, 'type': 'A'}, headers={'Accept-Encoding': 'identity'},
                follow_redirects=False, timeout=5) as response:
            response.raise_for_status()
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise ValueError('compressed DNS response')
            raw = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                if len(raw) + len(chunk) > 64 * 1024:
                    raise ValueError('DNS response exceeds size limit')
                raw.extend(chunk)
        body = json.loads(raw)
        if not isinstance(body, dict) or body.get('Status') != 0 or not isinstance(body.get('Answer'), list):
            raise ValueError('public DNS lookup failed')
        return [str(ipaddress.IPv4Address(row['data'])) for row in body['Answer']
                if isinstance(row, dict) and row.get('type') == 1]

    async def _download(self, url):
        current = url
        for hop in range(4):
            if not safe_url(current):
                raise ValueError('unsafe cover URL')
            original = httpx.URL(current)
            host = original.raw_host.decode('ascii')
            port = original.port or (443 if original.scheme == 'https' else 80)
            addresses = await self._resolve(host, port)
            if not addresses or not all(_public_ip(address) for address in addresses):
                raise ValueError('cover DNS answer is not public')
            # Connecting to the numeric address closes the DNS rebinding window.
            # Host controls HTTP routing; sni_hostname controls TLS SNI AND its
            # certificate hostname verification in httpcore's TLS backend.
            pinned = original.copy_with(host=addresses[0], fragment=None)
            async with self.client.stream(
                'GET', pinned, headers={'Host': original.netloc.decode('ascii'), 'Accept-Encoding': 'identity'},
                extensions={'sni_hostname': host}, follow_redirects=False, timeout=10,
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if hop == 3 or not response.headers.get('location'):
                        raise ValueError('cover redirect limit exceeded')
                    current = urljoin(current, response.headers['location'])
                    continue
                response.raise_for_status()
                # Refuse transfer compression rather than permit an unbounded
                # decompressor allocation before the streaming size check.
                if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise ValueError('compressed HTTP cover response')
                if int(response.headers.get('content-length', '0')) > _DOWNLOAD_BYTES:
                    raise ValueError('cover exceeds download limit')
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(raw) + len(chunk) > _DOWNLOAD_BYTES:
                        raise ValueError('cover exceeds download limit')
                    raw.extend(chunk)
                return bytes(raw)
        raise ValueError('cover redirect limit exceeded')

    def _prune_cache(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        now = self.clock()
        entries = []
        for path in self.cache_dir.iterdir():
            if not _CACHE_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                continue
            try:
                stat = path.stat()
                if now - stat.st_mtime >= _CACHE_AGE or stat.st_size > _DOWNLOAD_BYTES:
                    path.unlink(missing_ok=True)
                else:
                    entries.append((stat.st_atime, stat.st_size, path))
            except OSError:
                continue
        total = sum(size for _, size, _ in entries)
        for _, size, path in sorted(entries):
            if total <= _CACHE_BYTES:
                break
            try:
                path.unlink(missing_ok=True)
                total -= size
            except OSError:
                continue

    def _cached(self, path, width=720):
        self._prune_cache()
        if path.is_symlink() or not path.is_file():
            return None
        try:
            raw = path.read_bytes()
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'JPEG' or image.width > width or image.width * image.height > _PIXELS:
                    raise ValueError('invalid cached cover')
                image.verify()
            os.utime(path, (self.clock(), path.stat().st_mtime))
            return raw
        except (OSError, ValueError, Image.DecompressionBombError):
            path.unlink(missing_ok=True)
            return None

    def _save(self, path, raw):
        # Cache failure must not discard a successfully downloaded image.
        temporary = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.cache_dir, suffix='.tmp', delete=False) as output:
                temporary = output.name
                output.write(raw)
            os.replace(temporary, path)
            os.utime(path, (self.clock(), self.clock()))
            self._prune_cache()
        except OSError:
            pass
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    async def _image(self, url, width=720):
        path = self.cache_dir / (hashlib.sha256(f'{width}:{url}'.encode()).hexdigest() + '.jpg')
        try:
            cached = await asyncio.to_thread(self._cached, path, width)
        except OSError:
            cached = None
        if cached is not None:
            return cached
        raw = await asyncio.wait_for(self._download(url), timeout=20)
        encoded = await asyncio.to_thread(_encode_image, raw, width)
        await asyncio.to_thread(self._save, path, encoded)
        return encoded

    @staticmethod
    def _event_text(event, reasons, now, settings):
        display = dict(event)
        display['name'] = _clip(event.get('name'), 160)
        display['tags'] = [_clip(' / '.join(str(tag) for tag in event.get('tags', [])), 80)] if event.get('tags') else []
        display['extra_times'] = [dict(item, label=_clip(item.get('label', '时间'), 40))
                                  for item in event.get('extra_times', [])[:4]]
        game = GAMES.get(event.get('game_id'), {})
        lines = [_clip(game.get('name', event.get('game_id', '')), 60)]
        reason_text = []
        for reason in reasons:
            if reason == 'new':
                reason_text.append('新活动')
            elif re.fullmatch(r'd\d{1,3}', str(reason)):
                reason_text.append('结束前' + str(reason)[1:] + '天提醒')
        if reason_text:
            lines.append('提醒原因：' + '、'.join(reason_text))
        lines.append(format_event(display, now, description=settings.get('include_description', settings.get('show_description', False))))
        source = event.get('source') or game.get('source')
        if source:
            lines.append('来源：' + _clip(source, 80))
        return '\n'.join(filter(None, lines))

    def _texts(self, events, heading, mode, settings):
        bodies, links, omitted = [], [[] for _ in events], [False for _ in events]
        for item in events:
            bodies.append(self._event_text(item.get('event', item), item.get('reasons', []), self.clock(), settings))
        note = '…（完整链接见面板）'
        # Links share the whole message allowance. Reserve a short body and an
        # omission marker per event before selecting complete URLs, detail first.
        link_budget = max(0, _TEXT_LIMIT - len(heading) - sum(1 + min(len(body), 48) + len(note) + 1 for body in bodies))
        for label, key in [('详情', 'url'), ('封面', 'cover')]:
            if key == 'cover' and mode == 'off':
                continue
            for index, item in enumerate(events):
                url = item.get('event', item).get(key, '')
                if not safe_url(url):
                    continue
                line = label + '：' + url
                cost = len(line) + 1
                if cost <= link_budget:
                    links[index].append(line)
                    link_budget -= cost
                else:
                    omitted[index] = True
        suffixes = []
        for index, lines in enumerate(links):
            if omitted[index]:
                lines.append(note)
            suffixes.append(('\n' + '\n'.join(lines)) if lines else '')
        available = max(0, _TEXT_LIMIT - len(heading) - sum(1 + len(suffix) for suffix in suffixes))
        limits = [0] * len(events)
        pending = list(range(len(events)))
        # Redistribute space unused by short bodies to the remaining bodies.
        while available and pending:
            share = max(1, available // len(pending))
            for index in pending[:]:
                take = min(share, len(bodies[index]) - limits[index], available)
                limits[index] += take
                available -= take
                if limits[index] == len(bodies[index]):
                    pending.remove(index)
        return ['\n' + _clip(body, limit) + suffix for body, limit, suffix in zip(bodies, limits, suffixes)]

    async def build(self, events, *, title, mode='image', settings=None):
        settings = settings or {}
        mode = settings.get('cover_mode', 'image') if mode == 'default' else mode
        if mode not in ('image', 'link', 'off'):
            mode = 'off'
        try:
            limit = float(settings.get('cover_max_total_mb', 5))
            budget = int(max(0, min(5, limit)) * _MIB) if math.isfinite(limit) else 5 * _MIB
        except (TypeError, ValueError, OverflowError):
            budget = 5 * _MIB
        try:
            width = max(64, min(720, int(settings.get('cover_max_width', 720))))
        except (TypeError, ValueError, OverflowError):
            width = 720
        events = list(events)
        heading = _clip(title, 200) + '\n'
        parts, nodes, fallback = [_text_part(heading)], [], [heading]
        texts = self._texts(events, heading, mode, settings)
        for item, text in zip(events, texts):
            event = item.get('event', item)
            cover = event.get('cover', '')
            node = [_text_part(text)]
            image = None
            if mode == 'image' and budget > 0 and safe_url(cover):
                try:
                    image = await self._image(cover, width)
                    if len(image) > budget:
                        image = None
                except Exception:
                    # Images are optional untrusted input. Parser/plugin-specific
                    # failures also degrade to text; task cancellation still escapes.
                    image = None
            if image is not None:
                budget -= len(image)
                node.append({'type': 'image', 'data': {'file': 'base64://' + base64.b64encode(image).decode('ascii')}})
            parts.extend(node)
            nodes.append(node)
            fallback.append(text)
        return {'parts': parts, 'fallback': ''.join(fallback), 'nodes': nodes}
