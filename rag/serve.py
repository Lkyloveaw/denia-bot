"""RAG 检索服务：常驻加载 bge-m3 + 索引，只监听回环地址。

    .venv\\Scripts\\python.exe serve.py [--port 8091]

接口：
    GET  /health    状态
    POST /search    {"query": "...", "top_k": 2, "gate": "lexicon", "margin": null, "category": null}
    POST /reindex   语料更新后热重建索引
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import SOURCES, Embedder, Index, search  # noqa: E402
from core import GATE_MODE  # noqa: E402

_state: dict = {"idx": None, "emb": None, "built_at": 0.0}


class SearchReq(BaseModel):
    query: str
    top_k: int = 2
    margin: float | None = None
    gate: str | None = None          # lexicon | margin，不给就用 core.GATE_MODE
    category: str | None = None
    max_chars: int = 1100


def _boot() -> tuple[Index, Embedder]:
    if _state["idx"] is None:
        t0 = time.time()
        idx = Index.load()
        if not idx.ready:
            raise RuntimeError("索引不存在，请先运行 build_index.py")
        _state["idx"] = idx
        # 查询向量和库内向量必须出自同一个模型，后端跟着索引元数据走，不给手配错的机会
        backend = os.environ.get("KB_EMBED_BACKEND") or idx.meta.get("embed_backend", "cpu")
        try:
            _state["emb"] = Embedder(threads=2, backend=backend)
        except Exception as e:  # noqa: BLE001 - GPU 环境坏了也别把检索打死
            print(f"[rag] 后端 {backend} 起不来（{e}），降级 CPU fp32；精度略降，尽快修回来", flush=True)
            _state["emb"] = Embedder(backend="cpu")
            backend = "cpu(fallback)"
        _state["built_at"] = time.time()
        print(
            f"[rag] 索引载入 {idx.size} 块，后端 {backend}"
            f"（provider={_state['emb'].active_provider}），用时 {time.time()-t0:.1f}s",
            flush=True,
        )
    return _state["idx"], _state["emb"]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        _boot()
    except Exception as e:  # noqa: BLE001 - 索引缺失不挡启动，/health 会报出来
        print(f"[rag] 启动预热失败: {e}", flush=True)
    yield


app = FastAPI(title="Denia RAG", docs_url=None, redoc_url=None, lifespan=lifespan)

class EmbedReq(BaseModel):
    text: str


@app.post('/embed')
def embed(req: EmbedReq) -> dict:
    if not req.text.strip() or len(req.text) > 2000:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail='text must be 1-2000 characters')
    _, emb = _boot()
    return {'model': 'bge-m3-fp16-v1', 'vector': emb.encode([req.text])[0].tolist()}



@app.get("/health")
def health() -> dict:
    idx = _state["idx"]
    return {
        "ready": bool(idx and idx.ready),
        "chunks": idx.size if idx else 0,
        "dim": (idx.meta.get("dim") if idx else None),
        "gate_mode": GATE_MODE,
        "gate_margin": (idx.gate_margin if idx else None),
        "gate_stats": (idx.meta.get("gate_stats") if idx else None),
        "entries": (len(idx.lexicon) if idx else 0),
        "embed_backend": (_state["emb"].backend if _state["emb"] else idx.meta.get("embed_backend") if idx else None),
        "provider": (_state["emb"].active_provider if _state["emb"] else None),
        "vector_mem_mb": round((idx.vectors.nbytes / 1048576) if (idx and idx.vectors is not None) else 0, 1),
        "sources": (idx.meta.get("sources") if idx else None) or [str(p) for p in SOURCES],
    }


@app.post("/search")
def do_search(req: SearchReq) -> dict:
    try:
        idx, emb = _boot()
    except Exception as e:  # noqa: BLE001
        return {"hits": [], "error": str(e)}

    t0 = time.time()
    stats: dict = {}
    hits = search(idx, req.query, emb, top_k=req.top_k, margin=req.margin, gate=req.gate,
                  category=req.category, stats=stats)

    # 控制注入量：人格提示词优先，检索内容只是补充
    picked: list[dict] = []
    budget = req.max_chars
    for h in hits:
        body = h.text
        if len(body) > budget:
            body = body[:budget]
        picked.append(h.as_dict() | {"text": body})
        budget -= len(body)
        if budget <= 0:
            break

    return {
        "query": req.query,
        "hits": picked,
        **stats,
        "took_ms": round((time.time() - t0) * 1000, 1),
        "injected_chars": sum(len(h["text"]) for h in picked),
    }


@app.post("/reindex")
def reindex() -> dict:
    global _state
    t0 = time.time()
    emb = _state["emb"] or Embedder()
    files = [Path(p).resolve() for p in _state["idx"].meta["sources"]] if (_state["idx"] and _state["idx"].meta.get("sources")) else [Path(p).resolve() for p in SOURCES]
    files = [f for f in files if f.exists()]
    idx = Index.build(files, emb)
    idx.meta["sources"] = [str(f) for f in files]
    idx.save()
    _state = {"idx": idx, "emb": emb, "built_at": time.time()}
    return {"ok": True, "chunks": idx.size, "took_s": round(time.time() - t0, 1)}


def main() -> int:
    import uvicorn

    port = 8091
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"[rag] http://127.0.0.1:{port}  (chunks 载入后 /health 会显示 ready)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
