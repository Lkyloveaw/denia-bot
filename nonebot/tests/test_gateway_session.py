import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests._bootstrap import ensure_nonebot
ensure_nonebot()
from muicebot.plugin.context import event_context
from plugins import gateway_session


class Event:
    self_id = '123456'
    def __init__(self, session): self.session = session
    def get_session_id(self): return self.session


def test_gateway_chat_requests_include_isolated_session_headers():
    async def run():
        seen = []
        def send(request):
            seen.append((json.loads(request.content)['messages'][0]['content'], request.headers.get('x-opencode-session')))
            return httpx.Response(200, json={'id':'chat-test', 'object':'chat.completion', 'created':0,
                'model':'test', 'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'OK'}}]})
        transport = httpx.MockTransport(send)
        async with httpx.AsyncClient(transport=transport) as http:
            async with AsyncOpenAI(api_key='test', base_url='http://gateway.example/v1', http_client=http) as client:
                provider = SimpleNamespace(client=client)
                assert gateway_session.install(provider, 'http://gateway.example/v1')
                assert gateway_session.install(provider, 'http://gateway.example/v1')
                async def request(session, headers=None):
                    token = event_context.set(Event(session))
                    try:
                        await client.chat.completions.create(model='test', messages=[{'role':'user','content':session}], extra_headers=headers)
                    finally:
                        event_context.reset(token)
                await asyncio.gather(request('private_111'), request('private_222'))
                await request('private_111')
                await request('private_111', {'x-opencode-session':'explicit'})
        first = next(header for body, header in seen[:2] if body == 'private_111')
        second = next(header for body, header in seen[:2] if body == 'private_222')
        assert len(seen) == 4 and first == seen[2][1] and first != second
        assert all(re.fullmatch(r'qqbot-[0-9a-f]{24}', value) for _, value in seen[:3])
        assert seen[3][1] == 'explicit'
    asyncio.run(run())


def test_other_gateway_is_not_modified():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as http:
            async with AsyncOpenAI(api_key='test', base_url='https://other.example/v1', http_client=http) as client:
                assert not gateway_session.install(SimpleNamespace(client=client), 'http://gateway.example/v1')
                assert not http.event_hooks['request']
    asyncio.run(run())


def test_completion_hook_attaches_to_each_new_relay_model():
    async def run():
        async with httpx.AsyncClient() as http:
            async with AsyncOpenAI(api_key='test', base_url='http://gateway.example/v1', http_client=http) as client:
                instance = SimpleNamespace(model=SimpleNamespace(client=client))
                with patch.object(gateway_session, 'get_model_config', return_value=SimpleNamespace(api_host='http://gateway.example/v1')), \
                     patch.object(gateway_session.Muice, 'get_instance', return_value=instance):
                    await gateway_session.attach_gateway_session(Event('private_111'))
                    await gateway_session.attach_gateway_session(Event('private_111'))
                assert len(http.event_hooks['request']) == 1
    asyncio.run(run())
