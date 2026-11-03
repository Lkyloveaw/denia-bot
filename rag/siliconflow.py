"""SiliconFlow embedding client shared by the RAG service and index builder."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

MODEL = 'BAAI/bge-m3'
MODEL_ID = 'siliconflow-bge-m3-v1'
KEY_FILE = Path(os.environ.get('KB_EMBED_KEY_FILE', Path(__file__).resolve().parent.parent / 'nonebot' / 'data' / 'embedding_secrets.json'))


class CloudEmbeddingError(RuntimeError):
    pass


class SiliconFlowEmbedder:
    backend = 'siliconflow'
    active_provider = 'SiliconFlow'

    def encode(self, texts: list[str], batch: int = 16) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)
        try:
            key = json.loads(KEY_FILE.read_text(encoding='utf-8'))['api_key']
            if not isinstance(key, str) or not key.strip():
                raise ValueError('empty key')
        except (OSError, ValueError, KeyError, TypeError):
            raise CloudEmbeddingError('embedding API key is not configured') from None
        results = []
        for start in range(0, len(texts), min(batch, 16)):
            part = texts[start:start + min(batch, 16)]
            payload = json.dumps({'model': MODEL, 'input': part, 'encoding_format': 'float'}, ensure_ascii=False).encode()
            req = urllib.request.Request('https://api.siliconflow.cn/v1/embeddings', data=payload,
                    headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=4) as response:
                    data = json.load(response)['data']
            except urllib.error.HTTPError as exc:
                raise CloudEmbeddingError(f'embedding provider HTTP {exc.code}') from None
            except (OSError, ValueError, KeyError):
                raise CloudEmbeddingError('embedding provider unavailable') from None
            try:
                if len(data) != len(part) or sorted(r['index'] for r in data) != list(range(len(part))):
                    raise ValueError('bad index')
                vectors = np.asarray([r['embedding'] for r in sorted(data, key=lambda r: r['index'])], dtype=np.float32)
                if vectors.shape != (len(part), 1024) or not np.isfinite(vectors).all():
                    raise ValueError('bad vector')
                norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                if (norms == 0).any():
                    raise ValueError('zero vector')
                results.append(vectors / norms)
            except (KeyError, TypeError, ValueError, OverflowError):
                raise CloudEmbeddingError('invalid embedding response') from None
        return np.concatenate(results)
