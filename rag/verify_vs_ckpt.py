r"""GPU 保真校验（不重跑 CPU 推理）：拿断点里已算好的 CPU fp32 向量当基准。

index/.build/vectors.partial.npy 的第 i 行就是第 i 块文本的 fp32 向量，
progress.json 的 done 列表告诉我们是哪些块已经算过（上次 CPU 建到一半留下的）。
把这些块用 GPU fp16 再算一遍，直接比余弦——比 CPU 重跑一遍便宜两个数量级。

用法：
    .venv-gpu\\Scripts\\python.exe verify_vs_ckpt.py --n 1200
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from core import Embedder, Index, load_chunks  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1200, help="对比多少块")
    ap.add_argument("--batch", type=int, default=16)
    args = ap.parse_args()

    part = Index.dir() / ".build"
    vec_file = part / "vectors.partial.npy"
    meta_file = part / "progress.json"
    if not (vec_file.exists() and meta_file.exists()):
        print(f"[!] 没有断点可用：{vec_file}")
        return 1

    ref_all = np.load(vec_file)
    done = set(int(i) for i in json.loads(meta_file.read_text(encoding="utf-8")).get("done", []))
    chunks = load_chunks(sorted([p for p in Path("../knowledge").resolve().iterdir() if p.suffix in (".md", ".txt")]))
    print(f"断点：{ref_all.shape[0]} 行，其中 {len(done)} 行有 CPU fp32 向量；语料 {len(chunks)} 块")
    if len(done) < 50:
        print("[!] 可用基准太少")
        return 1

    rows = random.Random(11).sample(sorted(done), min(args.n, len(done)))
    rows.sort(key=lambda r: len(chunks[r].text))          # 长度相近，GPU 批次少浪费
    texts = [chunks[r].text for r in rows]
    ref = ref_all[rows]
    lens = np.array([len(t) for t in texts])
    print(f"抽样 {len(rows)} 块，字符长度 p50={np.median(lens):.0f} max={lens.max()}")

    t0 = time.time()
    emb = Embedder(backend="gpu")
    print(f"GPU 会话 {emb.active_provider}，建会话 {time.time()-t0:.1f}s", flush=True)

    t0 = time.time()
    got = emb.encode(texts, batch=args.batch)
    dt = time.time() - t0
    print(f"GPU 编码 {len(rows)} 块用时 {dt:.1f}s → {len(rows)/dt:.1f} 块/秒")

    cos = (ref * got).sum(axis=1) / (np.linalg.norm(ref, axis=1) * np.linalg.norm(got, axis=1) + 1e-9)
    print("\n=== fp16/GPU  vs  fp32/CPU（同一批文本、同一分词）===")
    print(f"  逐条余弦：min={cos.min():.6f}  p1={np.percentile(cos,1):.6f}  p50={np.percentile(cos,50):.6f}  mean={cos.mean():.6f}")
    print(f"  逐元素最大绝对误差 {np.abs(ref-got).max():.2e}")

    # 检索真正在乎的是排序：拿每条 fp32 查询去 GPU 库里找，还能不能找回自己
    a = ref / np.linalg.norm(ref, axis=1, keepdims=True)
    b = got / np.linalg.norm(got, axis=1, keepdims=True)
    sim = a @ b.T
    top1 = sim.argmax(axis=1)
    print(f"  fp32 查询命中 GPU 库同一条的比例 {(top1 == np.arange(len(rows))).mean():.4f}")

    # 闸门看的是「最高分 − 均值」的差值，两者相关才算安全
    np.random.default_rng(3)
    dense_ref = ref @ ref.T                                # 纯 fp32 空间
    gap_ref = np.array([np.delete(dense_ref[i], i).max() - dense_ref[i].mean() for i in range(len(rows))])
    dense_gpu = got @ ref.T                                # 混合空间：查询用 GPU、库用 CPU 的最坏情况
    gap_mix = np.array([np.delete(dense_gpu[i], i).max() - dense_gpu[i].mean() for i in range(len(rows))])
    corr = np.corrcoef(gap_ref, gap_mix)[0, 1]
    print(f"  闸门 margin：同空间分布 p50={np.percentile(gap_ref,50):.4f}，"
          f"混用后端 p50={np.percentile(gap_mix,50):.4f}，两者相关性 r={corr:.4f}")

    ok = cos.min() > 0.998
    print(f"\n结论：{'通过' if ok else '不通过'}（min 余弦 {cos.min():.6f}，阈值 0.998）")
    if not ok:
        bad = np.argsort(cos)[:5]
        for i in bad:
            print(f"  最差 {cos[i]:.5f}  {texts[i][:70]!r}")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
