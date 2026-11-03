"""One live renderer endpoint shared by the bot and dashboard (MIT)."""
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

CONFIG_PATH = Path(__file__).resolve().parent / 'data' / 'renderer.json'
DEFAULT_URL = 'http://127.0.0.1:3100'


def validate_url(value: str) -> str:
    value = str(value).strip().rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('渲染地址须为本机回环 HTTP 地址，例如 http://127.0.0.1:3100')
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('渲染端口无效')
    return value


def load() -> dict:
    configured = DEFAULT_URL
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding='utf8'))
        configured = validate_url(raw['url'])
    except FileNotFoundError:
        pass
    # Invalid saved configuration is an explicit error, never a silent endpoint change.
    override = os.environ.get('QQBOT_RENDER_URL')
    return {'url': validate_url(override or configured), 'source': 'environment' if override else 'shared',
            'editable': not bool(override)}


def save(url: str) -> dict:
    value = validate_url(url)
    if os.environ.get('QQBOT_RENDER_URL'):
        raise ValueError('当前地址由进程环境 QQBOT_RENDER_URL 管理')
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='renderer-', suffix='.json', dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            json.dump({'url': value}, stream)
        os.replace(temporary, CONFIG_PATH)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return load()
