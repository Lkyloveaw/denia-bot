r"""闸门判据离线分析：把 eval_gate 的 34 条探针一次性算出多种候选统计量，看哪个真能分开。

背景：现在闸门用 max(dense) - mean(全库)，实测分不开——
无关的「外面下雨了」margin=0.235，相关的「你用什么武器」margin=0.201，
因为库越大、话题越密，全库均值越被抬高，正好在“该注入”的时候把闸门关死。

这个脚本同时回答两个问题：
  1. 漏检到底是闸门太严，还是相关块根本没排进前列（检索本身的问题）
  2. 哪种统计量的类间间隔最大（给出建议阈值）

用法：
    .venv-gpu\\Scripts\\python.exe analyze_gate.py
"""

from __future__ import annotations

import sys
import math
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from core import BM25_B, BM25_K1, Embedder, Index, search, tokenize  # noqa: E402
from eval_gate import IRR, REL  # noqa: E402

POOL = (10, 25, 50, 100)   # top-K 局部均值窗口


def rare_term_scores(idx: Index, query: str, df_ratio: float = 0.02) -> np.ndarray:
    """只用「稀有词」（df <= 2% 库）打分：高频词（你/干嘛/什么/怎么）不该成为注入理由。

    这是给闸门用的，不参与 RRF 排序，所以直接在 postings 上算，不走 BM25.search。
    """
    b = idx.bm25
    out = np.zeros(b.n, np.float64)
    n = float(b.n)
    for term in tokenize(query):
        plist = b.postings.get(term)
        if not plist:
            continue
        if len(plist) / n > df_ratio:
            continue                                  # 太常见，丢掉
        idf = math.log(1 + (n - len(plist) + 0.5) / (len(plist) + 0.5))
        for row, freq in plist:
            norm = BM25_K1 * (1 - BM25_B + BM25_B * b.doc_len[row] / b.avg_len)
            out[row] += idf * freq * (BM25_K1 + 1) / (freq + norm)
    return out


def stats_for(dense: np.ndarray, sparse: list[tuple[int, float]] | None = None,
              rare: np.ndarray | None = None, rare_terms: int = 0) -> dict[str, float]:
    """一条查询的多种候选判据。sparse 是 BM25 前几名 (chunk_id, score)。"""
    order = np.argsort(-dense)
    top = float(dense[order[0]])
    s: dict[str, float] = {
        "top": top,
        "g_mean": top - float(dense.mean()),           # 现行判据
        "top1_minus_top2": top - float(dense[order[1]]),
    }
    for k in POOL:
        pool = dense[order[:k]]
        s[f"p{k}_mean"] = top - float(pool.mean())
        s[f"p{k}_z"] = (top - float(pool.mean())) / (float(pool.std()) + 1e-9)
    # 去掉第一名之后的邻域落差：真正相关的查询会「一骑绝尘」，无关的是一条平肩
    tail = dense[order[1 : 11]]
    s["p10_tail"] = top - float(tail.mean())

    if sparse:
        sc = np.array([x[1] for x in sparse], np.float64)
        s["bm1"] = float(sc[0])
        s["bm1_gap"] = float(sc[0] - sc[1]) if sc.size > 1 else float(sc[0])
        s["bm1_rel"] = float(sc[0] / (sc[1] + 1e-9)) if sc.size > 1 else float(sc[0])
        # 头部命中是不是「整句几乎就是标题」：查询词覆盖率 × 第一名分数
        s["bm_top3_mean"] = float(sc[:3].mean())
    else:
        s.update(bm1=0.0, bm1_gap=0.0, bm1_rel=0.0, bm_top3_mean=0.0)

    if rare is not None:
        ro = np.argsort(-rare)
        s["rare1"] = float(rare[ro[0]])
        # 稀有判据要和「查询里有几个稀有词」挂钩，否则长句天然占便宜
        s["rare1_per_term"] = float(rare[ro[0]] / max(1, rare_terms))
        s["rare_terms"] = float(rare_terms)
        s["rare_top2_gap"] = float(rare[ro[0]] - rare[ro[1]])
        # 稠密第一名是不是也有稀有词支撑：两个通道互相印证
        s["rare_at_dense1"] = float(rare[order[0]])
        s["both"] = float(min(1.0, rare[ro[0]] / 5.0) * dense[order[0]])
    else:
        s.update(rare1=0.0, rare1_per_term=0.0, rare_terms=0.0, rare_top2_gap=0.0,
                 rare_at_dense1=0.0, both=0.0)
    return s


KEYS = [
    "top", "g_mean", "p25_mean", "p100_mean", "p10_tail",
    "bm1", "bm1_gap", "bm1_rel", "bm_top3_mean",
    "rare1", "rare1_per_term", "rare_top2_gap", "rare_at_dense1", "both",
]


def main() -> int:
    idx = Index.load()
    if not idx.ready:
        print("[!] 索引未就绪")
        return 1
    # 默认走 CPU fp32：serve.py 已经把 1.1 GB 的 fp16 模型压在显存里了，
    # 这个脚本只算 34 条查询，没必要再挤一个 CUDA 会话进去。
    backend = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("cpu", "gpu") else "cpu"
    emb = Embedder(backend=backend)
    print(f"索引 {idx.size} 块，探针后端 {backend} / {emb.active_provider}"
          f"（索引本身是 {idx.meta.get('embed_backend')}）\n")

    rows: list[tuple[str, bool, dict[str, float], list[str]]] = []
    t0 = time.time()
    for want, probes in ((True, REL), (False, IRR)):
        for q in probes:
            v = emb.encode([q], batch=1)[0]
            dense = idx.vectors @ v                      # 行号就是 chunk id
            sparse = idx.bm25.search(q, 10) if idx.bm25 else []
            rare = rare_term_scores(idx, q)
            rare_terms = sum(1 for t in tokenize(q) if len(idx.bm25.postings.get(t, ())) / idx.bm25.n <= 0.02)
            # margin 给个极负值 = 关掉闸门，看检索本身把什么排到前面
            pre = search(idx, q, emb, top_k=4, margin=-9.0, dropoff=9.9)
            paths = [h.path.split(" > ")[-1][:30] for h in pre]
            rows.append((q, want, stats_for(dense, sparse, rare, rare_terms), paths))
    print(f"34 条探针算完，用时 {time.time()-t0:.1f}s\n")

    print("=== 每条探针的判据（REL=该注入，IRR=不该注入）===")
    disp = ["top", "g_mean", "bm1", "rare1", "rare1_per_term", "rare_terms"]
    print("类别 查询                  " + "".join(f"{k:>14}" for k in disp))
    for q, want, s, paths in rows:
        print(f"{'REL' if want else 'IRR'}  {q:<18} " + "".join(f"{s[k]:>14.3f}" for k in disp))
    print()
    for q, want, s, paths in rows:
        if want:
            print(f"  REL「{q}」前列命中：{paths}")
    print()

    rel = [s for q, w, s, p in rows if w]
    irr = [s for q, w, s, p in rows if not w]
    print("=== 类间可分性（间隔 = min(REL) - max(IRR)，负的说明单靠这个量分不开）===")
    for k in KEYS:
        lo = min(r[k] for r in rel)
        hi = max(r[k] for r in irr)
        print(f"  {k:<12} REL[{lo:.3f} .. {max(r[k] for r in rel):.3f}]  "
              f"IRR[{min(r[k] for r in irr):.3f} .. {hi:.3f}]  间隔={lo - hi:+.3f}")

    print("\n=== 以 19 条无关探针为校准源：阈值 = IRR 最大值（保证零误注），看能救回多少 REL ===")
    for k in KEYS:
        hi = max(r[k] for r in irr)
        passed = [q for (q, w, s, p) in rows if w and s[k] > hi]
        print(f"  {k:<12} 阈值={hi:.3f}  REL 通过 {len(passed)}/{len(REL)}  漏：{[q for q in REL if q not in passed]}")

    print("\n=== 两路组合：稠密判据 OR 稀有词判据（任一超过各自 IRR 上限就放行）===")
    for dk in ("g_mean", "p25_mean", "top"):
        d_hi = max(r[dk] for r in irr)
        for bk in ("rare1", "rare1_per_term", "both"):
            b_hi = max(r[bk] for r in irr)
            passed = [q for (q, w, s, p) in rows if w and (s[dk] > d_hi or s[bk] > b_hi)]
            fp = [q for (q, w, s, p) in rows if not w and (s[dk] > d_hi or s[bk] > b_hi)]
            print(f"  {dk} > {d_hi:.3f}  或  {bk} > {b_hi:.3f} → REL {len(passed)}/{len(REL)}，IRR 误注 {len(fp)}")

    print("\n=== 只靠稀有词判据（阈值 = 该判据的 IRR 最大值，保证零误注）===")
    for bk in ("rare1", "rare1_per_term", "rare_at_dense1", "both"):
        b_hi = max(r[bk] for r in irr)
        passed = [q for (q, w, s, p) in rows if w and s[bk] > b_hi]
        print(f"  {bk} > {b_hi:.3f} → REL {len(passed)}/{len(REL)}；"
              f"漏：{[q for q in REL if q not in passed]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
