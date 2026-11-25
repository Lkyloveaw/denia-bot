"""Provide a private per-conversation routing session to the relay gateway."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sys
from pathlib import Path

from nonebot import logger
from nonebot.adapters import Event
from muicebot.config import get_model_config
from muicebot.muice import Muice
from muicebot.plugin import PluginMetadata
from muicebot.plugin.context import get_event
from muicebot.plugin.hook import on_before_completion

try:
    import gateway_session_logic as gs
except ImportError:  # 不是从 nonebot\ 目录启动的，把工程根补进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import gateway_session_logic as gs

__plugin_meta__ = PluginMetadata(name='gateway_session', description='为模型网关请求提供会话路由标识', usage='自动生效')
_salt = secrets.token_bytes(32)


async def _add_session(request):
    if request.method != 'POST' or not request.url.path.rstrip('/').endswith('/chat/completions'):
        return
    if 'x-opencode-session' in request.headers:
        return
    try:
        event = get_event()
        identity = str(getattr(event, 'self_id', '')) + ':' + event.get_session_id()
        session = hmac.new(_salt, identity.encode('utf-8'), hashlib.sha256).hexdigest()[:24]
    except (LookupError, AttributeError, TypeError):
        session = secrets.token_hex(12)
    request.headers['x-opencode-session'] = 'qqbot-' + session


_add_session._qqbot_gateway_session = True


def install(provider, gateway_url):
    client = getattr(provider, 'client', None)
    http = getattr(client, '_client', None)
    hooks = getattr(http, 'event_hooks', None)
    try:
        if not gateway_url or not gs.same_endpoint(client.base_url, gateway_url):
            return False
        if not isinstance(hooks, dict) or 'request' not in hooks:
            return False
        if not any(getattr(hook, '_qqbot_gateway_session', False) for hook in hooks['request']):
            hooks['request'].append(_add_session)
        return True
    except (AttributeError, TypeError, ValueError):
        return False


@on_before_completion(priority=3)
async def attach_gateway_session(event: Event):
    try:
        relay = get_model_config('relay')
        install(Muice.get_instance().model, getattr(relay, 'api_host', None))
    except Exception as e:
        logger.warning(f'[gateway-session] 网关会话配置失败：{type(e).__name__}')
