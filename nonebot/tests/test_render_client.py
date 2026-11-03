import base64
from io import BytesIO
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from unittest.mock import patch
import httpx
from PIL import Image
import pytest
import render_client as client

def png(size=(760,300)):
    output=BytesIO(); Image.new('RGB',size,'white').save(output,'PNG'); return output.getvalue()


def test_whole_card_can_exceed_normal_width_and_page_height():
    image=png((3000,2800)); requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(200,content=image,headers={'content-type':'image/png'})
    got=client.render_card('complete help',width=3000,source='help',transport=httpx.MockTransport(handle))
    assert got==image
    assert requests[0].url.path=='/render-card'
    assert json.loads(requests[0].content)['width']==3000
    with pytest.raises(client.RenderError,match='dimensions'):
        client.render_document('ordinary card',width=3000)
    with pytest.raises(client.RenderError,match='dimensions'):
        client.png_bytes(image)


def test_whole_card_still_rejects_excessive_pixel_dimensions():
    image=bytearray(png())
    image[16:20]=(5000).to_bytes(4,'big')
    image[20:24]=(5000).to_bytes(4,'big')
    with pytest.raises(client.RenderError,match='dimensions'):
        client.render_card('help',width=3000,transport=httpx.MockTransport(
            lambda request:httpx.Response(200,content=bytes(image),headers={'content-type':'image/png'})))

def test_document_contract_uses_one_endpoint_and_validates_every_page():
    image=png(); requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(200,json={'pages':[base64.b64encode(image).decode()]*2})
    with patch.dict(os.environ,{'QQBOT_RENDER_URL':'http://127.0.0.1:3101'}):
        pages=client.render_document('<html>中文</html>',width=760,source='weather',transport=httpx.MockTransport(handle))
    assert pages==[image,image]
    assert str(requests[0].url)=='http://127.0.0.1:3101/render-pages'
    assert requests[0].headers['x-render-source']=='weather'
    assert json.loads(requests[0].content)['width']==760

@pytest.mark.parametrize('status',[401,413,422,429,500,503])
def test_errors_are_explicit_and_release_admission(status):
    with pytest.raises(client.RenderError) as caught:
        client.render_document('<html>x</html>',transport=httpx.MockTransport(lambda request:httpx.Response(status)))
    assert caught.value.status==status
    assert client._pending.acquire(blocking=False)
    client._pending.release()

def test_encoded_json_budget_is_checked_before_network():
    calls=[]
    # Quotes fit the HTML budget but double in JSON.
    with pytest.raises(client.RenderError) as caught:
        client.render_document('"'*(client.MAX_BODY//2+10),transport=httpx.MockTransport(lambda request:calls.append(request)))
    assert caught.value.status==413 and calls==[]

@pytest.mark.parametrize('data',[{}, {'pages':[]}, {'pages':['@@']}, {'pages':[base64.b64encode(b'not png').decode()]}])
def test_malformed_pages_are_rejected(data):
    with pytest.raises(client.RenderError):
        client.render_document('hello',transport=httpx.MockTransport(lambda request:httpx.Response(200,json=data)))

def test_renderer_failure_has_no_local_drawing_fallback():
    def fail(request): raise httpx.ConnectError('offline',request=request)
    with pytest.raises(client.RenderError,match='unavailable'):
        client.render_document('hello',transport=httpx.MockTransport(fail))

def test_queue_saturation_fails_immediately():
    acquired=[]
    try:
        while client._pending.acquire(blocking=False): acquired.append(True)
        with pytest.raises(client.RenderError,match='queue full'):
            client.render_document('hello')
    finally:
        for _ in acquired: client._pending.release()

def test_templates_escape_untrusted_text_and_preserve_redaction():
    import weather_image,status_image
    payload='<img src="http://bad">'
    html=weather_image.build_html('北京\n'+payload)
    assert payload not in html and '&lt;img' in html
    from test_status_image import view
    import status_logic
    data=view(); status_logic.apply_redaction(data,['hostname','process','network','qq_stats'])
    html=status_image.build_status_html(data)
    assert 'denia-prod' not in html and 'eth0' not in html and 'proc0' not in html
