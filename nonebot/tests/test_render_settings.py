import asyncio
from unittest.mock import patch
import httpx
import pytest
import render_client
import render_settings


def test_all_clients_use_the_same_live_setting(tmp_path, monkeypatch):
    monkeypatch.delenv('QQBOT_RENDER_URL', raising=False)
    monkeypatch.setattr(render_settings, 'CONFIG_PATH', tmp_path/'renderer.json')
    render_settings.save('http://127.0.0.1:3101')
    assert render_client.base_url('http://127.0.0.1:3000') == 'http://127.0.0.1:3101'
    render_settings.save('http://127.0.0.1:3100')
    assert render_client.base_url() == 'http://127.0.0.1:3100'


@pytest.mark.parametrize('url', ['https://example.com','http://192.168.1.1:3100',
                                'http://user:pass@localhost','http://localhost/path'])
def test_only_same_machine_renderer_addresses_are_accepted(url):
    with pytest.raises(ValueError):
        render_settings.validate_url(url)


def test_async_cancellation_releases_shared_admission():
    async def run():
        async def handle(request):
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            task = asyncio.create_task(render_client.render_html('test', client=client))
            await asyncio.sleep(.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(run())
    assert render_client._slots.acquire(blocking=False)
    assert render_client._slots.acquire(blocking=False)
    render_client._slots.release()
    render_client._slots.release()


def test_streamed_response_budget_is_checked():
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b'x'*40
            yield b'y'*40
    transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=Body()))
    with patch.object(render_client, 'MAX_RESPONSE', 60):
        with pytest.raises(render_client.RenderError, match='response too large'):
            render_client.render_document('test', transport=transport)
