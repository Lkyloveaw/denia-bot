"""Pure gateway endpoint/session helpers shared by the bot and the dashboard."""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit


def endpoint(value):
    """Return (scheme, host, port, path) or None for an unusable URL."""
    try:
        parsed = urlsplit(str(value))
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = (parsed.scheme or '').lower()
    host = (parsed.hostname or '').lower()
    if not scheme or not host:
        return None
    if port is None:
        port = {'http': 80, 'https': 443}.get(scheme)
    return (
        scheme,
        host,
        port,
        (parsed.path or '').rstrip('/'),
    )


def same_endpoint(a, b) -> bool:
    left, right = endpoint(a), endpoint(b)
    return left is not None and left == right


def anonymous_session() -> str:
    """A panel-only routing id; it never encodes a QQ identity."""
    return 'qqbot-' + secrets.token_hex(12)


__all__ = ['anonymous_session', 'endpoint', 'same_endpoint']
