"""Shared local Playwright transport (original MIT implementation).

Synchronous callers run in existing worker threads; async callers share the same
bounded admission gate. No Pillow layout or fallback browser is hidden here.
"""
from __future__ import annotations
import asyncio
import atexit
import base64
import json
import os
import threading
import time
from typing import Any
import httpx
import render_settings

MAX_BODY = 2 * 1024 * 1024
MAX_RESPONSE = 32 * 1024 * 1024
MAX_PIXELS = 20_000_000
_slots = threading.BoundedSemaphore(2)
_pending = threading.BoundedSemaphore(10)
_pool = httpx.Client(trust_env=False, limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))
atexit.register(_pool.close)

class RenderError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, path: str = '/render'):
        super().__init__(message)
        self.message, self.status, self.path = message, status, path

def base_url(value: str | None = None) -> str:
    # The legacy argument remains accepted while every card uses the shared setting.
    try:
        return render_settings.load()['url']
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise RenderError('invalid shared renderer configuration') from exc

def payload_bytes(html: str, width: int, height: int, timeout_ms: int, *, whole_card: bool = False) -> bytes:
    if not isinstance(html, str) or not html.strip():
        raise RenderError('empty html')
    max_width = MAX_PIXELS // 200 if whole_card else 1600
    if (not 320 <= int(width) <= max_width or not 200 <= int(height) <= 2400
            or int(width) * int(height) > MAX_PIXELS):
        raise RenderError('render dimensions out of range')
    if not 1000 <= int(timeout_ms) <= 60000:
        raise RenderError('render timeout out of range')
    body = json.dumps(dict(html=html, width=int(width), height=int(height), timeout_ms=int(timeout_ms)),
                      ensure_ascii=False, separators=(',', ':')).encode('utf8')
    if len(body) > MAX_BODY:
        raise RenderError('render request exceeds 2 MiB', status=413)
    return body

def png_bytes(data: bytes, *, whole_card: bool = False) -> bytes:
    if len(data) < 24 or not data.startswith(b'\x89PNG\r\n\x1a\n') or data[12:16] != b'IHDR':
        raise RenderError('renderer did not return png')
    width, height = int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    if (width < 1 or height < 1 or width * height > MAX_PIXELS
            or (not whole_card and (width > 1600 or height > 2400))):
        raise RenderError('invalid PNG dimensions')
    return data

def decode_response(response: httpx.Response, pages: bool, *, whole_card: bool = False) -> list[bytes]:
    if response.status_code != 200:
        raise RenderError(f'renderer http {response.status_code}', status=response.status_code)
    if len(response.content) > MAX_RESPONSE:
        raise RenderError('renderer response too large')
    content_type = response.headers.get('content-type', '').split(';')[0].strip()
    if not pages:
        if content_type != 'image/png':
            raise RenderError('renderer content-type is not image/png')
        return [png_bytes(response.content, whole_card=whole_card)]
    if content_type != 'application/json':
        raise RenderError('renderer content-type is not application/json')
    try:
        rows = response.json()['pages']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
            raise ValueError('invalid page count')
        return [png_bytes(base64.b64decode(row, validate=True)) for row in rows]
    except (ValueError, KeyError, TypeError) as exc:
        raise RenderError('invalid renderer pages') from exc

def _headers(source: str, token: str = '') -> dict[str, str]:
    headers = {'Content-Type': 'application/json', 'X-Render-Source': source}
    token = os.environ.get('QQBOT_RENDER_TOKEN') or token
    if token:
        headers['X-Render-Token'] = token
    return headers

def render_document(html: str, *, width: int = 900, source: str = 'card',
                    url: str | None = None, timeout: float = 25,
                    transport: httpx.BaseTransport | None = None) -> list[bytes]:
    return _render_sync(html, width=width, height=2400, timeout=timeout, source=source,
                        pages=True, transport=transport)


def render_card(html: str, *, width: int = 900, source: str = 'card',
                timeout: float = 25, transport: httpx.BaseTransport | None = None) -> bytes:
    """Capture the complete data-card root, within the shared 20M pixel budget."""
    return _render_sync(html, width=width, height=200, timeout=timeout, source=source,
                        pages=False, whole_card=True, transport=transport)[0]


def _render_sync(html, *, width, height, timeout, source, pages, token='', transport=None,
                 whole_card=False):
    payload_bytes(html, width, height, min(60000, max(1000, int((timeout-2)*1000))), whole_card=whole_card)
    if not _pending.acquire(blocking=False):
        raise RenderError('render queue full', status=429)
    started = time.monotonic()
    acquired = False
    try:
        acquired = _slots.acquire(timeout=min(5, timeout))
        if not acquired:
            raise RenderError('render queue timeout', status=429)
        remaining = timeout - (time.monotonic()-started)
        if remaining <= 2:
            raise RenderError('render queue deadline exceeded', status=429)
        body = payload_bytes(html, width, height, min(60000, max(1000, int((remaining-2)*1000))), whole_card=whole_card)
        client = httpx.Client(transport=transport, trust_env=False) if transport else _pool
        try:
            path = '/render-card' if whole_card else '/render-pages' if pages else '/render'
            with client.stream('POST', base_url()+path, content=body,
                               headers=_headers(source, token), timeout=remaining) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise RenderError('renderer response too large')
                    if time.monotonic()-started > timeout:
                        raise RenderError('render deadline exceeded', status=504)
                    chunks.append(chunk)
                result = httpx.Response(response.status_code, headers=response.headers, content=b''.join(chunks))
            return decode_response(result, pages, whole_card=whole_card)
        finally:
            if transport:
                client.close()
    except httpx.HTTPError as exc:
        raise RenderError('renderer unavailable') from exc
    finally:
        if acquired:
            _slots.release()
        _pending.release()

async def render_document_async(html: str, **kwargs: Any) -> list[bytes]:
    # Transport and admission are shared with the worker-thread card callers.
    return await asyncio.to_thread(render_document, html, **kwargs)

async def render_html(html: str, *, width: int = 900, height: int = 1200,
                      timeout_ms: int = 20000, url: str | None = None,
                      source: str = 'legacy', token: str = '',
                      client: httpx.AsyncClient | None = None) -> bytes:
    if client is None:
        return (await asyncio.to_thread(_render_sync, html, width=width, height=height,
                timeout=timeout_ms/1000+3, source=source, pages=False, token=token))[0]
    payload_bytes(html, width, height, timeout_ms)
    if not _pending.acquire(blocking=False):
        raise RenderError('render queue full', status=429)
    started = time.monotonic()
    acquired = False
    queued = False
    try:
        async with asyncio.timeout(timeout_ms/1000+3):
            while not acquired:
                acquired = _slots.acquire(blocking=False)
                if not acquired:
                    queued = True
                    if time.monotonic()-started >= min(5, timeout_ms/1000):
                        raise RenderError('render queue timeout', status=429)
                    await asyncio.sleep(.025)
            remaining_ms = max(1000, timeout_ms-int((time.monotonic()-started)*1000)) if queued else timeout_ms
            # Preserve the requested timeout when there was no queue delay.
            body = payload_bytes(html, width, height, remaining_ms)
            async with client.stream('POST', base_url()+'/render', content=body,
                                     headers=_headers(source, token), timeout=remaining_ms/1000+2) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise RenderError('renderer response too large')
                    chunks.append(chunk)
                result = httpx.Response(response.status_code, headers=response.headers, content=b''.join(chunks))
            return decode_response(result, False)[0]
    except TimeoutError as exc:
        raise RenderError('render deadline exceeded', status=504) from exc
    except httpx.HTTPError as exc:
        raise RenderError('renderer unavailable') from exc
    finally:
        if acquired:
            _slots.release()
        _pending.release()

def single_page(pages: list[bytes]) -> bytes:
    if len(pages) != 1:
        raise RenderError('multiple pages; use the paginated interface')
    return pages[0]
