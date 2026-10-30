"""离线构建索引：读取 knowledge/*.md → 分块 → bge-m3 向量 + BM25 → 存 rag/index/

全量语料近 2 万块，CPU 上要跑几个小时，所以向量阶段支持断点续跑：
中途被打断（重启、杀进程）后重跑同一条命令会接着上次的进度走。

用法：
    .venv\\Scripts\\python.exe build_index.py                     # 用 core.SOURCES 默认语料
    .venv\\Scripts\\python.exe build_index.py --dir ../knowledge  # 索引整个目录
    .venv\\Scripts\\python.exe build_index.py --restart          # 丢掉断点重头跑
    .venv\\Scripts\\python.exe build_index.py --limit 300        # 只跑一小截，冒烟用
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jieba  # noqa: E402
import numpy as np  # noqa: E402
from core import BM25, DIM, Chunk, Embedder, Index, build_lexicon, load_chunks, tokenize  # noqa: E402

CKPT_EVERY = 200          # 每多少个 chunk 落一次断点、打一行进度


def resolve_sources(args: argparse.Namespace) -> list[Path]:
    if args.file:
        return [Path(f).resolve() for f in args.file]
    if args.dir:
        d = Path(args.dir).resolve()
        return sorted(p for p in list(d.rglob("*.md")) + list(d.rglob("*.txt")) if p.is_file())
    from core import SOURCES

    return [Path(p).resolve() for p in SOURCES]


def fingerprint(chunks: list[Chunk]) -> str:
    """语料换一个字节，断点就不该再复用。"""
    import hashlib

    h = hashlib.sha256()
    for c in chunks:
        h.update(f"{c.id}\x00{c.source}\x00{c.path}\x00{c.text}\n".encode())
    return h.hexdigest()[:16]


def embed_all(chunks: list[Chunk], emb: Embedder, batch: int, restart: bool) -> np.ndarray:
    """按长度排序分批编码（同批 padding 浪费最小），向量写回各自行号。

    定期把整张向量矩阵 + 已完成行号写进 index/.build/，被打断后重跑即续。
    断点里记着后端：CPU/GPU 向量不是同一个模型产出的，不允许混。
    """
    part = Index.dir() / ".build"
    part.mkdir(parents=True, exist_ok=True)
    vec_file = part / "vectors.partial.npy"
    meta_file = part / "progress.json"
    sig = fingerprint(chunks)
    backend = emb.backend

    vectors = np.zeros((len(chunks), DIM), np.float32)
    done: set[int] = set()
    if restart or not (vec_file.exists() and meta_file.exists()):
        vec_file.unlink(missing_ok=True)
        meta_file.unlink(missing_ok=True)
    else:
        prev = json.loads(meta_file.read_text(encoding="utf-8"))
        cached = np.load(vec_file)
        if prev.get("backend", "cpu") != backend:
            print(f"[提示] 断点是 {prev.get('backend','cpu')} 向量，本次要用 {backend}，不混用，重头编码")
            vec_file.unlink(missing_ok=True)
            meta_file.unlink(missing_ok=True)
        elif (
            prev.get("fingerprint") == sig
            and prev.get("count") == len(chunks)
            and cached.shape == (len(chunks), DIM)
        ):
            done = set(int(i) for i in prev.get("done", []))
            vectors[:] = cached
            print(f"[续跑] 已有 {len(done)}/{len(chunks)} 条向量（{backend}），从断点继续")
        else:
            print("[提示] 语料或规模变了，断点作废，重头编码")
            vec_file.unlink(missing_ok=True)

    texts = [c.text for c in chunks]
    todo = [i for i in range(len(texts)) if i not in done]
    todo.sort(key=lambda i: len(texts[i]))
    total, t0, last_ckpt = len(texts), time.time(), len(done)
    for i in range(0, len(todo), batch):
        rows = todo[i : i + batch]
        vectors[rows] = emb.encode([texts[r] for r in rows], batch=batch)
        n = len(done) + i + len(rows)
        if n - last_ckpt >= CKPT_EVERY or i + len(rows) == len(todo):
            np.save(vec_file, vectors)
            meta_file.write_text(
                json.dumps(
                    {
                        "fingerprint": sig,
                        "count": total,
                        "backend": backend,
                        "done": sorted(done | set(todo[: i + len(rows)])),
                    }
                ),
                encoding="utf-8",
            )
            last_ckpt = n
            rate = (n - len(done)) / max(1e-6, time.time() - t0)
            print(f"  [{n}/{total}] {rate:.1f} 块/秒，剩余约 {(total - n) / max(0.01, rate) / 60:.0f} 分钟",
                  flush=True)
    return vectors


def main() -> int:
    ap = argparse.ArgumentParser(description="构建达妮娅知识库检索索引")
    ap.add_argument("--file", nargs="*", help="显式指定语料文件")
    ap.add_argument("--dir", help="索引该目录下全部 .md/.txt")
    ap.add_argument("--threads", type=int, default=0, help="onnxruntime 线程数，0=自动（仅 CPU 后端）")
    ap.add_argument("--gpu", action="store_true", help="用 CUDA + fp16 模型编码（需 .venv-gpu 与 to_fp16.py 产物）")
    ap.add_argument("--batch", type=int, default=16, help="每批编码条数")
    ap.add_argument("--limit", type=int, help="只编码前 N 块（冒烟测试用，不写 BM25/正式索引）")
    ap.add_argument("--restart", action="store_true", help="忽略断点，从头编码")
    args = ap.parse_args()

    sources = [p for p in resolve_sources(args) if p.exists()]
    if not sources:
        print("[!] 找不到任何语料文件")
        return 1

    print("语料：")
    for p in sources:
        print(f"  {p}  ({p.stat().st_size/1024:.0f} KB)")

    t0 = time.time()
    chunks = load_chunks(sources)
    if not chunks:
        print("[!] 没有可索引的语料")
        return 1
    print(f"分块完成：{len(chunks)} 块（{time.time()-t0:.1f}s）")

    if args.limit:
        chunks = chunks[: args.limit]
        print(f"[--limit] 只跑前 {len(chunks)} 块，不落地正式索引")

    emb = Embedder(threads=args.threads, backend="gpu" if args.gpu else "cpu")
    print(f"编码后端：{emb.backend}（provider={emb.active_provider}）")
    vectors = embed_all(chunks, emb, args.batch, args.restart)
    print(f"向量完成：{vectors.shape}，用时 {(time.time()-t0)/60:.1f} 分钟")
    if args.limit:
        return 0

    idx = Index()
    idx.chunks = {c.id: c for c in chunks}
    idx.vectors = vectors
    idx.meta = {"dim": int(vectors.shape[1]), "count": len(chunks), "embed_backend": emb.backend}
    t = time.time()
    docs = [tokenize(c.text) for c in chunks]
    idx.bm25 = BM25([c.id for c in chunks], docs)
    print(f"BM25 完成：用时 {time.time()-t:.0f}s")
    idx.meta["sources"] = [str(p) for p in sources]
    t = time.time()
    idx.lexicon, idx.lex_rev = build_lexicon(chunks, sources, idx.bm25.df)
    covered = len({i for ids in idx.lexicon.values() for i in ids})
    idx.meta["entries"] = len(idx.lexicon)
    print(f"词条词典：{len(idx.lexicon)} 条 + 简称 {len(idx.lex_rev)} 个，"
          f"覆盖 {covered}/{len(chunks)} 块，用时 {time.time()-t:.0f}s")
    t = time.time()
    idx.calibrate(emb)
    print(f"闸门自校准：gate_margin={idx.gate_margin} stats={idx.meta['gate_stats']} 用时 {time.time()-t:.0f}s")
    idx.save(docs)
    leftover = Index.dir() / ".build"
    if leftover.exists() and not any(leftover.iterdir()):
        leftover.rmdir()
    print(
        f"[OK] {idx.size} 块 / dim={idx.meta['dim']} / 总用时 {(time.time()-t0)/60:.1f} 分钟\n"
        f"     → {Index.dir()}"
    )

    probe = tokenize("达妮娅")
    print(f"[check] jieba 就绪，「达妮娅」→ {probe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
