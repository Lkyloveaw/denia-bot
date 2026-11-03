import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'rag'))
sys.path.insert(0, str(ROOT / 'nonebot'))
import siliconflow
import embedding_panel
import serve_cloud


def test_cloud_client_batches_and_normalizes(tmp_path):
    key_file = tmp_path / 'key.json'
    key_file.write_text(json.dumps({'api_key': 'sk-test'}))
    sizes = []

    def respond(request, timeout):
        payload = json.loads(request.data)
        sizes.append(len(payload['input']))
        vectors = [{'index': i, 'embedding': [2.0] + [0.0] * 1023} for i in reversed(range(sizes[-1]))]
        return io.BytesIO(json.dumps({'data': vectors}).encode())

    with patch.object(siliconflow, 'KEY_FILE', key_file), patch('urllib.request.urlopen', respond):
        result = siliconflow.SiliconFlowEmbedder().encode(['x'] * 17)
    assert sizes == [16, 1]
    assert result.shape == (17, 1024)
    assert np.allclose(np.linalg.norm(result, axis=1), 1)


def test_cloud_client_rejects_bad_vectors(tmp_path):
    key_file = tmp_path / 'key.json'
    key_file.write_text(json.dumps({'api_key': 'sk-test'}))
    response = io.BytesIO(json.dumps({'data': [{'index': 0, 'embedding': [0.0]}]}).encode())
    with patch.object(siliconflow, 'KEY_FILE', key_file), patch('urllib.request.urlopen', return_value=response):
        with pytest.raises(siliconflow.CloudEmbeddingError, match='invalid embedding response'):
            siliconflow.SiliconFlowEmbedder().encode(['x'])


def test_dashboard_key_requires_auth_and_never_returns_secret(tmp_path):
    app = FastAPI()
    def authorized(req):
        if req.headers.get('authorization') != 'test':
            raise HTTPException(401)
    embedding_panel.register(app, authorized=authorized, mutate=authorized, root=tmp_path)
    client = TestClient(app)
    assert client.get('/api/embedding').status_code == 401
    assert client.post('/api/embedding', json={'api_key': 'sk-secret'}).status_code == 401
    assert client.post('/api/embedding', headers={'authorization': 'test'}, json={'api_key': 'sk-secret'}).status_code == 200
    response = client.get('/api/embedding', headers={'authorization': 'test'})
    assert response.json()['configured'] is True
    assert 'sk-secret' not in response.text


def test_cloud_embed_interface_uses_distinct_model_tag():
    class FakeEmbedder:
        def encode(self, texts):
            return np.ones((len(texts), 1024), dtype=np.float32)

    with patch.dict(serve_cloud.state, {'emb': FakeEmbedder()}):
        data = serve_cloud.embed(serve_cloud.EmbedReq(text='hello'))
    assert data['model'] == 'siliconflow-bge-m3-v1'
    assert len(data['vector']) == 1024
