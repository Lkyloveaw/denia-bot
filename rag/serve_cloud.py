"""RAG HTTP API backed by a separately built SiliconFlow vector index."""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import core
from siliconflow import KEY_FILE, MODEL, MODEL_ID, CloudEmbeddingError, SiliconFlowEmbedder

core.INDEX_DIR = Path(os.environ.get('KB_CLOUD_INDEX', Path(__file__).resolve().parent / 'index-siliconflow'))
state = {'idx': None, 'emb': SiliconFlowEmbedder()}


@asynccontextmanager
async def lifespan(app):
    idx = core.Index.load()
    if not idx.ready or idx.meta.get('embed_model') != MODEL or idx.meta.get('embed_backend') != 'siliconflow':
        raise RuntimeError('cloud index missing or model mismatch')
    state['idx'] = idx
    yield


app = FastAPI(title='Denia RAG', docs_url=None, redoc_url=None, lifespan=lifespan)


class EmbedReq(BaseModel):
    text: str


class SearchReq(BaseModel):
    query: str
    top_k: int = 2
    margin: float | None = None
    gate: str | None = None
    category: str | None = None
    max_chars: int = 1100


@app.get('/health')
def health():
    idx = state['idx']
    return {'ready': bool(idx and idx.ready and KEY_FILE.is_file()), 'chunks': idx.size if idx else 0,
            'dim': idx.meta.get('dim') if idx else None, 'gate_mode': core.GATE_MODE,
            'gate_margin': idx.gate_margin if idx else None, 'gate_stats': idx.meta.get('gate_stats') if idx else None,
            'entries': len(idx.lexicon) if idx else 0, 'embed_backend': 'siliconflow', 'provider': 'SiliconFlow',
            'model': MODEL, 'vector_mem_mb': round(idx.vectors.nbytes / 1048576, 1) if idx else 0,
            'sources': idx.meta.get('sources', []) if idx else []}


@app.post('/embed')
def embed(req: EmbedReq):
    if not req.text.strip() or len(req.text) > 2000:
        raise HTTPException(400, 'text must be 1-2000 characters')
    try:
        return {'model': MODEL_ID, 'vector': state['emb'].encode([req.text])[0].tolist()}
    except CloudEmbeddingError as exc:
        raise HTTPException(503, str(exc)) from None


@app.post('/search')
def do_search(req: SearchReq):
    start = time.time()
    stats = {}
    try:
        hits = core.search(state['idx'], req.query, state['emb'], top_k=req.top_k, margin=req.margin,
                           gate=req.gate, category=req.category, stats=stats)
    except CloudEmbeddingError as exc:
        return {'hits': [], 'error': str(exc)}
    picked, budget = [], req.max_chars
    for h in hits:
        body = h.text[:budget]
        picked.append(h.as_dict() | {'text': body})
        budget -= len(body)
        if budget <= 0:
            break
    return {'query': req.query, 'hits': picked, **stats, 'took_ms': round((time.time()-start)*1000, 1),
            'injected_chars': sum(len(h['text']) for h in picked)}


@app.post('/reindex')
def reindex():
    raise HTTPException(409, 'Build and validate a new cloud index offline before activation')
