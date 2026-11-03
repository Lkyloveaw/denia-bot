import asyncio
import base64
import importlib
import io
import socket
import ssl
import sys
from pathlib import Path

import httpx
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def module():
    return importlib.import_module('game_event_message')


def event(**changes):
    return {'uid': 'one', 'game_id': 'sr', 'name': '测试活动', 'start': 100,
            'end': 86400, 'description': '活动描述', 'cover': '',
            'url': 'https://example.com/detail', 'tags': [], 'extra_times': [], **changes}


def run(coro):
    return asyncio.run(coro)


def jpeg(width=1440, height=800):
    output = io.BytesIO()
    Image.new('RGB', (width, height), 'blue').save(output, 'PNG')
    return output.getvalue()


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **kwargs:
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))])


@pytest.mark.parametrize('url', [
    'file:///etc/passwd', 'ftp://example.com/a', 'https://localhost/a',
    'https://api.localhost/a', 'https://127.0.0.1/a', 'https://10.0.0.1/a',
    'https://[::1]/a', 'https://[fe80::1%25eth0]/a', 'https://192.0.2.1/a',
    'https://user:pass@example.com/a', 'https://example.com\\@127.0.0.1/a',
    'https://example.com/a\n', 'https://example.com:99999/a', 'https://2130706433/a',
    'https://224.0.0.1/a', 'https://100.64.0.1/a', 'javascript:alert(1)',
])
def test_unsafe_link_is_rejected_without_network(url, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: pytest.fail('pure syntax check resolved DNS'))
    assert module().safe_url(url) is False


@pytest.mark.parametrize('url', ['https://example.com/a?x=1', 'http://8.8.8.8/a', 'https://[2606:4700:4700::1111]/a'])
def test_public_http_links_pass_syntax_validation(url):
    assert module().safe_url(url)


def test_text_nodes_are_literal_and_plan_reasons_are_chinese(tmp_path):
    builder = module().MessageBuilder(tmp_path, clock=lambda: 200)
    result = run(builder.build([{'event': event(name='[CQ:at,qq=all]', description='[CQ:image,file=evil]'),
                                 'reasons': ['new', 'd1']}], title='活动概览', mode='off',
                               settings={'include_description': True}))
    assert all(p['type'] == 'text' for p in result['parts'])
    assert '[CQ:at,qq=all]' in result['fallback']
    assert '[CQ:image,file=evil]' in result['fallback']
    assert '新活动' in result['fallback'] and '1天' in result['fallback']
    assert 'https://example.com/detail' in result['fallback']
    assert '活动概览' in result['parts'][0]['data']['text']
    assert len(result['nodes']) == 1
    run(builder.close())


def test_link_mode_never_downloads_and_unsafe_urls_are_omitted(tmp_path):
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail('download in link mode'))) as client:
            builder = module().MessageBuilder(tmp_path, client=client, clock=lambda: 200)
            result = await builder.build([event(cover='https://example.com/cover', url='file:///secret')], title='活动', mode='link')
            assert 'https://example.com/cover' in result['fallback']
            assert 'file:' not in result['fallback']
            assert all(p['type'] == 'text' for p in result['parts'])
            await builder.close()
            assert not client.is_closed
    run(check())


def test_image_is_pinned_compressed_cached_and_reused(tmp_path, public_dns):
    async def check():
        requests = []
        def respond(request):
            requests.append(request)
            assert request.url.host == '93.184.216.34'
            assert request.headers['host'] == 'example.com'
            assert request.extensions['sni_hostname'] == 'example.com'
            return httpx.Response(200, content=jpeg())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            builder = module().MessageBuilder(tmp_path, client=client, clock=lambda: 200)
            for _ in range(2):
                result = await builder.build([event(cover='https://example.com/image')], title='活动')
                part = next(p for p in result['parts'] if p['type'] == 'image')
                raw = base64.b64decode(part['data']['file'].removeprefix('base64://'))
                with Image.open(io.BytesIO(raw)) as image:
                    assert image.format == 'JPEG' and image.width == 720
                assert 'https://example.com/image' in result['fallback']
                assert result['nodes'][0][-1] == part
            assert len(requests) == 1
            assert len(list(tmp_path.glob('*.jpg'))) == 1
    run(check())


def test_private_dns_answer_prevents_any_download(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **kw:
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))])
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail('private IP fetched'))) as client:
            result = await module().MessageBuilder(tmp_path, client=client).build(
                [event(cover='https://example.com/image')], title='活动')
            assert not any(p['type'] == 'image' for p in result['parts'])
            assert 'https://example.com/image' in result['fallback']
    run(check())


def test_optional_public_dns_uses_public_ip_and_preserves_tls_host(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: pytest.fail('system Fake-IP DNS used'))
    async def check():
        calls = []
        def respond(request):
            calls.append(request.url.host)
            if request.url.host == 'dns.google':
                assert request.url.path == '/resolve'
                assert request.url.params['name'] == 'example.com'
                assert request.url.params['type'] == 'A'
                return httpx.Response(200, json={'Status': 0, 'Answer': [{'type': 5, 'data': 'cdn.example.com'}, {'type': 1, 'data': '93.184.216.34'}]})
            assert request.url.host == '93.184.216.34'
            assert request.headers['host'] == 'example.com'
            assert request.extensions['sni_hostname'] == 'example.com'
            return httpx.Response(200, content=jpeg())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            builder = module().MessageBuilder(tmp_path, client=client, dns_mode='google')
            message = await builder.build([event(cover='https://example.com/image')], title='活动')
            assert any(part['type'] == 'image' for part in message['parts'])
            assert calls == ['dns.google', '93.184.216.34']
    run(check())


@pytest.mark.parametrize('body', [
    {'Status': 2, 'Answer': [{'type': 1, 'data': '93.184.216.34'}]},
    {'Status': 0, 'Answer': []},
    {'Status': 0, 'Answer': [{'type': 1, 'data': '127.0.0.1'}]},
    {'Status': 0, 'Answer': [{'type': 1, 'data': '198.18.4.63'}]},
    {'Status': 0, 'Answer': [{'type': 1, 'data': '93.184.216.34'}, {'type': 1, 'data': '10.0.0.1'}]},
    {'Status': 0, 'Answer': [{'type': 1, 'data': 'not-an-address'}]},
    {'Status': 0, 'Answer': [{'type': 1, 'data': '93.184.216.34'}], 'padding': 'x' * 70000},
])
def test_public_dns_never_relaxes_address_validation(tmp_path, body):
    async def check():
        calls = []
        def respond(request):
            calls.append(request.url.host)
            assert request.url.host == 'dns.google', 'Unsafe image connection attempted'
            return httpx.Response(200, json=body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            builder = module().MessageBuilder(tmp_path, client=client, dns_mode='google')
            message = await builder.build([event(cover='https://example.com/image')], title='活动')
            assert all(part['type'] == 'text' for part in message['parts'])
            assert calls == ['dns.google']
    run(check())


def test_redirect_to_private_address_is_never_followed(tmp_path, public_dns):
    async def check():
        paths = []
        def respond(request):
            paths.append(str(request.url))
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/secret'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await module().MessageBuilder(tmp_path, client=client).build(
                [event(cover='https://example.com/image')], title='活动')
            assert len(paths) == 1
            assert not any(p['type'] == 'image' for p in result['parts'])
    run(check())


def test_redirect_loop_stops_after_three_hops(tmp_path, public_dns):
    async def check():
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(302, headers={'location': '/next'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await module().MessageBuilder(tmp_path, client=client).build(
                [event(cover='https://example.com/image')], title='活动')
            assert len(requests) == 4
            assert not any(p['type'] == 'image' for p in result['parts'])
    run(check())


class OversizedStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        for _ in range(9):
            yield b'x' * (1024 * 1024)


@pytest.mark.parametrize('response', [
    lambda: httpx.Response(200, stream=OversizedStream()),
    lambda: httpx.Response(200, content=b'not an image'),
    lambda: httpx.Response(200, content=jpeg(5000, 4001)),
    lambda: httpx.Response(500, content=b'error'),
])
def test_bad_images_fall_back_to_safe_link(tmp_path, public_dns, response):
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response())) as client:
            result = await module().MessageBuilder(tmp_path, client=client).build(
                [event(cover='https://example.com/image')], title='活动')
            assert not any(p['type'] == 'image' for p in result['parts'])
            assert 'https://example.com/image' in result['fallback']
    run(check())


def test_one_budget_is_shared_by_all_events_and_nodes(tmp_path, public_dns):
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=jpeg()))) as client:
            builder = module().MessageBuilder(tmp_path, client=client)
            single = await builder.build([event(cover='https://example.com/a')], title='活动')
            size = len(base64.b64decode(next(p for p in single['parts'] if p['type'] == 'image')['data']['file'][9:]))
            result = await builder.build([event(cover='https://example.com/a'), event(uid='two', cover='https://example.com/b')],
                                         title='活动', settings={'cover_max_total_mb': size / (1024 * 1024)})
            assert sum(p['type'] == 'image' for p in result['parts']) == 1
            assert sum(p['type'] == 'image' for node in result['nodes'] for p in node) == 1
            assert 'https://example.com/b' in ''.join(p['data'].get('text', '') for p in result['parts'])
    run(check())


def test_expired_cache_is_refetched_and_unknown_files_are_preserved(tmp_path, public_dns):
    async def check():
        now, requests = [200], []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, content=jpeg())
        unknown = tmp_path / 'keep.txt'
        unknown.write_text('keep')
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            builder = module().MessageBuilder(tmp_path, client=client, clock=lambda: now[0])
            await builder.build([event(cover='https://example.com/a')], title='活动')
            now[0] += 7 * 86400 + 1
            await builder.build([event(cover='https://example.com/a')], title='活动')
            assert len(requests) == 2
            assert unknown.read_text() == 'keep'
    run(check())


def test_text_budget_and_description_default(tmp_path):
    builder = module().MessageBuilder(tmp_path, clock=lambda: 200)
    result = run(builder.build([event(name='标题' * 160, description='说明' * 200, tags=['标签' * 1000]) for _ in range(8)],
                               title='活动日历', mode='off'))
    assert sum(len(p['data']['text']) for p in result['parts']) <= 3500
    assert len(result['fallback']) <= 3500
    assert '说明' not in result['fallback']
    assert '…' in result['fallback']
    run(builder.close())


def test_unexpected_compression_failure_also_falls_back(tmp_path, public_dns, monkeypatch):
    def broken_encoder(raw, width=720):
        raise SyntaxError('broken image metadata')
    monkeypatch.setattr(module(), '_encode_image', broken_encoder)
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=jpeg()))) as client:
            result = await module().MessageBuilder(tmp_path, client=client).build(
                [event(cover='https://example.com/image')], title='活动')
            assert 'https://example.com/image' in result['fallback']
            assert all(p['type'] == 'text' for p in result['parts'])
    run(check())


def test_link_that_fits_text_budget_is_not_silently_dropped(tmp_path):
    cover = 'https://example.com/' + 'a' * 250
    builder = module().MessageBuilder(tmp_path, clock=lambda: 200)
    result = run(builder.build([event(url='', cover=cover) for _ in range(8)], title='活动', mode='link'))
    assert result['fallback'].count(cover) == 8
    assert len(result['fallback']) <= 3500
    run(builder.close())


def test_transport_connects_to_pinned_ip_and_verifies_original_tls_host(tmp_path, public_dns):
    import httpcore

    class Wire:
        def __init__(self):
            raw = jpeg()
            self.response = b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(raw)).encode() + b'\r\n\r\n' + raw
            self.writes = bytearray()
            self.tls = None

        async def read(self, max_bytes, timeout=None):
            raw, self.response = self.response[:max_bytes], self.response[max_bytes:]
            return raw

        async def write(self, buffer, timeout=None):
            self.writes.extend(buffer)

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            self.tls = (server_hostname, ssl_context.check_hostname, ssl_context.verify_mode)
            return self

        async def aclose(self):
            pass

        def get_extra_info(self, name):
            return None

    class Network:
        def __init__(self):
            self.wire = Wire()
            self.address = None

        async def connect_tcp(self, host, port, **kwargs):
            self.address = (host, port)
            return self.wire

    async def check():
        network = Network()
        transport = httpx.AsyncHTTPTransport(trust_env=False)
        await transport._pool.aclose()
        transport._pool = httpcore.AsyncConnectionPool(network_backend=network, max_keepalive_connections=0)
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            result = await module().MessageBuilder(tmp_path, client=client).build(
                [event(cover='https://example.com/image')], title='活动')
            assert any(p['type'] == 'image' for p in result['parts'])
            assert network.address == ('93.184.216.34', 443)
            assert network.wire.tls == ('example.com', True, ssl.CERT_REQUIRED)
            assert b'Host: example.com\r\n' in network.wire.writes
    run(check())


def test_cache_size_limit_evicts_least_recently_used(tmp_path, public_dns, monkeypatch):
    async def check():
        requests, now = [], [200]
        def respond(request):
            requests.append(request.url.path)
            return httpx.Response(200, content=jpeg())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            builder = module().MessageBuilder(tmp_path, client=client, clock=lambda: now[0])
            await builder.build([event(cover='https://example.com/a')], title='活动')
            size = next(tmp_path.glob('*.jpg')).stat().st_size
            monkeypatch.setattr(module(), '_CACHE_BYTES', size * 2)
            for path in ['b', 'a', 'c', 'b']:
                now[0] += 1
                await builder.build([event(cover='https://example.com/' + path)], title='活动')
            assert requests == ['/a', '/b', '/c', '/b']
            assert sum(path.stat().st_size for path in tmp_path.glob('*.jpg')) <= size * 2
    run(check())


def test_long_cover_fallback_uses_spare_text_space_from_short_events(tmp_path, public_dns):
    cover = 'https://example.com/' + 'a' * 1000
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
            builder = module().MessageBuilder(tmp_path, client=client, clock=lambda: 200)
            items = [event(uid=str(n), url='', cover=cover if n == 7 else '') for n in range(8)]
            result = await builder.build(items, title='活动', mode='image')
            assert cover in result['fallback']
            assert len(result['fallback']) <= 3500
            assert len(result['nodes']) == 8
    run(check())


def test_extreme_two_urls_keep_complete_detail_before_cover_and_mark_clipping(tmp_path):
    detail = 'https://example.com/' + 'd' * 2000
    cover = 'https://example.com/' + 'c' * 2000
    builder = module().MessageBuilder(tmp_path, clock=lambda: 200)
    result = run(builder.build([event(name='标题' * 1000, url=detail, cover=cover)], title='活动', mode='link'))
    assert detail in result['fallback']
    assert cover not in result['fallback']
    assert '面板' in result['fallback'] and '…' in result['fallback']
    assert len(result['fallback']) <= 3500
    run(builder.close())


def test_cover_width_is_encoded_and_cache_is_partitioned_by_width(tmp_path, public_dns):
    async def check():
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, content=jpeg())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            builder = module().MessageBuilder(tmp_path, client=client, clock=lambda: 200)
            for width in [720, 64, 320, 64, 720]:
                result = await builder.build([event(cover='https://example.com/a')], title='活动', settings={'cover_max_width': width})
                image = next(p for p in result['parts'] if p['type'] == 'image')
                raw = base64.b64decode(image['data']['file'][9:])
                with Image.open(io.BytesIO(raw)) as decoded:
                    assert decoded.width == width
            assert len(requests) == 3
            assert len(list(tmp_path.glob('*.jpg'))) == 3
    run(check())


def test_normalized_event_displays_its_registered_source(tmp_path):
    builder = module().MessageBuilder(tmp_path, clock=lambda: 200)
    result = run(builder.build([event()], title='活动', mode='off'))
    assert '来源：sra' in result['fallback']
    run(builder.close())


def test_eight_extreme_entries_still_keep_a_complete_detail_and_hard_text_limit(tmp_path):
    detail = 'https://example.com/' + 'd' * 2000
    cover = 'https://example.com/' + 'c' * 2000
    builder = module().MessageBuilder(tmp_path, clock=lambda: 200)
    items = [event(uid=str(n), name='标题' * 1000, description='说明' * 1000,
                   url=detail, cover=cover, tags=['标签' * 1000]) for n in range(8)]
    result = run(builder.build(items, title='活动', mode='link', settings={'include_description': True}))
    assert detail in result['fallback']
    assert len(result['fallback']) <= 3500
    assert sum(len(part['data']['text']) for part in result['parts']) <= 3500
    assert len(result['nodes']) == 8
    assert '面板' in result['fallback'] and '…' in result['fallback']
    run(builder.close())
