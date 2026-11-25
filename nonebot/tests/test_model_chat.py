import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_session_logic
import model_chat
import model_publication

MODEL_TEXT = """# provider config
relay:
  provider: openai
  default: true
  template: Denia
  model_name: test-model
  api_host: "https://api.example.test/v1/"
  api_key: sk-secret-value
# diagnostic entry
echo-test:
  provider: _echo
  default: false
  model_name: echo
"""

NO_KEY_TEXT = """relay:
  provider: openai
  default: true
  model_name: test-model
  api_host: "https://api.example.test/v1"
"""

BLANK_KEY_TEXT = """relay:
  provider: openai
  default: true
  model_name: test-model
  api_host: "https://api.example.test/v1"
  api_key: "  "
"""


def write_config(tmp_path, monkeypatch, text=MODEL_TEXT):
    path = tmp_path / 'models.yml'
    path.write_text(text, encoding='utf-8')
    model_publication.approve_existing(path, hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(model_chat, 'MODELS_PATH', path)
    return path


@pytest.fixture()
def models(tmp_path, monkeypatch):
    return write_config(tmp_path, monkeypatch)


def patch_transport(monkeypatch, handler):
    """Use a real AsyncClient with a MockTransport; record constructor kwargs."""
    calls = {}
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        calls.update(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(model_chat.httpx, 'AsyncClient', factory)
    return calls


def run_chat(messages, **kwargs):
    return asyncio.run(model_chat.chat(messages, **kwargs))


def test_success_posts_openai_payload_and_parses_reply(models, monkeypatch):
    seen = {}

    def handler(request):
        seen['method'] = request.method
        seen['url'] = str(request.url)
        seen['authorization'] = request.headers.get('Authorization')
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={
            'model': 'test-model-2026',
            'choices': [{'message': {'role': 'assistant', 'content': '你好'}}],
            'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5},
        })

    client_kwargs = patch_transport(monkeypatch, handler)
    result = run_chat([
        {'role': 'system', 'content': '你是助手', 'name': 'ignored'},
        {'role': 'user', 'content': '你好'},
    ])

    assert seen['method'] == 'POST'
    assert seen['url'] == 'https://api.example.test/v1/chat/completions'
    assert seen['authorization'] == 'Bearer sk-secret-value'
    assert seen['body'] == {
        'model': 'test-model',
        'messages': [
            {'role': 'system', 'content': '你是助手'},
            {'role': 'user', 'content': '你好'},
        ],
        'stream': False,
        'max_tokens': 512,
    }
    assert client_kwargs['timeout'] == 60.0
    assert client_kwargs['follow_redirects'] is False
    assert result['content'] == '你好'
    assert result['model'] == 'test-model-2026'
    assert result['usage'] == {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}
    assert isinstance(result['latency_ms'], int)
    assert result['latency_ms'] >= 0


def test_model_argument_overrides_only_the_model_id(models, monkeypatch):
    seen = {}

    def handler(request):
        seen['url'] = str(request.url)
        seen['authorization'] = request.headers.get('Authorization')
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    client_kwargs = patch_transport(monkeypatch, handler)
    result = run_chat([{'role': 'user', 'content': 'hi'}], model='other-model', timeout=5)

    assert client_kwargs['timeout'] == 5.0
    assert seen['body']['model'] == 'other-model'
    assert seen['url'] == 'https://api.example.test/v1/chat/completions'
    assert seen['authorization'] == 'Bearer sk-secret-value'
    assert result['model'] == 'other-model'
    assert result['usage'] is None


def test_http_error_raises_chat_error_without_leaking_key(models, monkeypatch):
    def handler(request):
        return httpx.Response(500, json={'error': {'message': 'upstream failed'}})

    patch_transport(monkeypatch, handler)
    with pytest.raises(model_chat.ChatError) as excinfo:
        run_chat([{'role': 'user', 'content': 'hi'}])

    assert str(excinfo.value) == '模型提供商返回 HTTP 500'
    assert 'sk-secret-value' not in str(excinfo.value)


def test_redirect_is_not_followed(models, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location': 'https://elsewhere.example/chat'})

    patch_transport(monkeypatch, handler)
    with pytest.raises(model_chat.ChatError) as excinfo:
        run_chat([{'role': 'user', 'content': 'hi'}])

    assert str(excinfo.value) == '模型提供商返回 HTTP 302'
    assert len(calls) == 1


def test_malformed_json_raises_chat_error(models, monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b'not-json')

    patch_transport(monkeypatch, handler)
    with pytest.raises(model_chat.ChatError) as excinfo:
        run_chat([{'role': 'user', 'content': 'hi'}])

    assert str(excinfo.value) == '模型提供商返回的不是 JSON'


@pytest.mark.parametrize('payload', [
    {'choices': [{'message': {'content': '   '}}]},
    {'choices': [{'message': {'content': None}}]},
    {'choices': []},
    {'choices': 'bad'},
])
def test_empty_or_malformed_reply_raises_chat_error(models, monkeypatch, payload):
    def handler(request):
        return httpx.Response(200, json=payload)

    patch_transport(monkeypatch, handler)
    with pytest.raises(model_chat.ChatError) as excinfo:
        run_chat([{'role': 'user', 'content': 'hi'}])

    assert '模型提供商' in str(excinfo.value)


@pytest.mark.parametrize('error', [httpx.ConnectError, httpx.ReadTimeout])
def test_connection_errors_raise_chat_error(models, monkeypatch, error):
    def handler(request):
        raise error('boom', request=request)

    patch_transport(monkeypatch, handler)
    with pytest.raises(model_chat.ChatError) as excinfo:
        run_chat([{'role': 'user', 'content': 'hi'}])

    assert str(excinfo.value) == '无法连接模型提供商'
    assert 'sk-secret-value' not in str(excinfo.value)


INVALID_MESSAGES = [
    pytest.param('not-a-list', id='not-list'),
    pytest.param([], id='empty'),
    pytest.param([{'role': 'user', 'content': 'x'}] * 21, id='too-many'),
    pytest.param([{'role': 'tool', 'content': 'x'}], id='bad-role'),
    pytest.param([{'role': ['user'], 'content': 'x'}], id='unhashable-role'),
    pytest.param([{'role': 'user', 'content': '   '}], id='blank-content'),
    pytest.param([{'role': 'user', 'content': 1}], id='non-string-content'),
    pytest.param(['hi'], id='not-a-mapping'),
    pytest.param([{'role': 'user', 'content': '字' * 12_001}], id='too-long'),
]


def test_gateway_request_gets_an_anonymous_session_header(models, monkeypatch):
    seen = {}

    def handler(request):
        seen['session'] = request.headers.get('x-opencode-session')
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    patch_transport(monkeypatch, handler)
    run_chat([{'role': 'user', 'content': 'hi'}])

    assert seen['session'].startswith('qqbot-')
    assert len(seen['session']) > len('qqbot-')


def test_direct_provider_has_no_session_header(tmp_path, monkeypatch):
    text = """direct:
  provider: openai
  default: true
  model_name: direct-model
  api_host: https://direct.example/v1
  api_key: sk-secret-value
"""
    write_config(tmp_path, monkeypatch, text)
    seen = {}

    def handler(request):
        seen['session'] = request.headers.get('x-opencode-session')
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    patch_transport(monkeypatch, handler)
    run_chat([{'role': 'user', 'content': 'hi'}])

    assert seen['session'] is None


def test_unpublished_config_is_rejected_before_any_request(tmp_path, monkeypatch):
    path = tmp_path / 'models.yml'
    path.write_text(MODEL_TEXT, encoding='utf-8')
    monkeypatch.setattr(model_chat, 'MODELS_PATH', path)

    def fail_factory(**kwargs):
        raise AssertionError('no HTTP client should be created')

    monkeypatch.setattr(model_chat.httpx, 'AsyncClient', fail_factory)
    with pytest.raises(model_chat.ChatError, match='模型配置尚未发布'):
        run_chat([{'role': 'user', 'content': 'hi'}])


@pytest.mark.parametrize('messages', INVALID_MESSAGES)
def test_invalid_messages_are_rejected_before_any_request(models, monkeypatch, messages):
    def fail_factory(**kwargs):
        raise AssertionError('no HTTP client should be created')

    monkeypatch.setattr(model_chat.httpx, 'AsyncClient', fail_factory)
    with pytest.raises(model_chat.ChatError) as excinfo:
        run_chat(messages)

    assert '消息' in str(excinfo.value)


@pytest.mark.parametrize('text', [NO_KEY_TEXT, BLANK_KEY_TEXT], ids=['missing', 'blank'])
def test_missing_api_key_is_rejected(tmp_path, monkeypatch, text):
    write_config(tmp_path, monkeypatch, text)

    def fail_factory(**kwargs):
        raise AssertionError('no HTTP client should be created')

    monkeypatch.setattr(model_chat.httpx, 'AsyncClient', fail_factory)
    with pytest.raises(model_chat.ChatError) as excinfo:
        run_chat([{'role': 'user', 'content': 'hi'}])

    assert str(excinfo.value) == '尚未配置 API Key'


@pytest.mark.parametrize('base', [
    'ftp://api.example.test/v1',
    'https://user:pass@api.example.test/v1',
    'https://api.example.test/v1?key=1',
    'https://api.example.test/v1#frag',
    'http://[::1',
    'https://api.example.test:bad/v1',
    'api.example.test/v1',
    '',
])
def test_invalid_base_url_is_rejected(tmp_path, monkeypatch, base):
    write_config(
        tmp_path, monkeypatch,
        MODEL_TEXT.replace('https://api.example.test/v1/', base),
    )

    def fail_factory(**kwargs):
        raise AssertionError('no HTTP client should be created')

    monkeypatch.setattr(model_chat.httpx, 'AsyncClient', fail_factory)
    with pytest.raises(model_chat.ChatError) as excinfo:
        run_chat([{'role': 'user', 'content': 'hi'}])

    assert 'base URL' in str(excinfo.value)


def test_gateway_endpoint_helpers_reject_unusable_urls():
    assert gateway_session_logic.endpoint(None) is None
    assert gateway_session_logic.endpoint('') is None
    assert not gateway_session_logic.same_endpoint(None, None)
    assert not gateway_session_logic.same_endpoint('', '')


def test_gateway_endpoint_normalizes_default_ports_and_accepts_url_objects():
    assert gateway_session_logic.same_endpoint('https://gw/v1', 'https://gw:443/v1/')
    assert gateway_session_logic.same_endpoint('http://gw/v1', 'http://gw:80/v1')
    assert gateway_session_logic.same_endpoint(httpx.URL('https://gw/v1/'), 'https://gw/v1')
    assert not gateway_session_logic.same_endpoint('https://gw:444/v1', 'https://gw/v1')
    assert not gateway_session_logic.same_endpoint('https://gw/v1', 'https://gw/v2')
    assert not gateway_session_logic.same_endpoint('http://gw/v1', 'https://gw/v1')


def test_default_entry_with_explicit_port_still_gets_gateway_session(tmp_path, monkeypatch):
    text = """direct:
  provider: openai
  default: true
  model_name: direct-model
  api_host: "https://api.example.test:443/v1/"
  api_key: sk-secret-value
relay:
  api_host: "https://api.example.test/v1"
"""
    write_config(tmp_path, monkeypatch, text)
    seen = {}

    def handler(request):
        seen['session'] = request.headers.get('x-opencode-session')
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    patch_transport(monkeypatch, handler)
    run_chat([{'role': 'user', 'content': 'hi'}])

    assert seen['session'].startswith('qqbot-')


def test_default_entry_with_different_path_has_no_gateway_session(tmp_path, monkeypatch):
    text = """direct:
  provider: openai
  default: true
  model_name: direct-model
  api_host: "https://api.example.test/v2"
  api_key: sk-secret-value
relay:
  api_host: "https://api.example.test/v1"
"""
    write_config(tmp_path, monkeypatch, text)
    seen = {}

    def handler(request):
        seen['session'] = request.headers.get('x-opencode-session')
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    patch_transport(monkeypatch, handler)
    run_chat([{'role': 'user', 'content': 'hi'}])

    assert seen['session'] is None
