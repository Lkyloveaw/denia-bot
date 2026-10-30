r"""GPU 后端验收：数值保真 + 吞吐基准。

一个进程里同时建两个 Embedder（.venv-gpu 的 onnxruntime 既有 CPU 也有 CUDA EP）：
  cpu → fp32 模型 / CPUExecutionProvider，就是现在在建索引的那套
  gpu → fp16 模型 / CUDAExecutionProvider
先比同一批文本的向量是否一致（余弦），再测块/秒。

用法（必须用 GPU venv）：
    .venv-gpu\\Scripts\\python.exe verify_gpu.py --n 600
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from core import Embedder, load_chunks  # noqa: E402


def gpu_mem() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip().splitlines()[0]
    except Exception as e:  # noqa: BLE001
        return f"(nvidia-smi 失败: {e})"


def bench(emb: Embedder, texts: list[str], batch: int, reps: int = 2) -> tuple[float, float]:
    """返回 (块/秒, 单次批最慢耗时秒)。第一次跑当作预热，不计入。"""
    best_rate, slowest = 0.0, 0.0
    for r in range(reps + 1):
        t0 = time.time()
        for i in range(0, len(texts), batch):
            emb.encode(texts[i : i + batch], batch=batch)
        dt = time.time() - t0
        if r == 0:
            continue
        best_rate = max(best_rate, len(texts) / dt)
        slowest = max(slowest, dt)
    return best_rate, slowest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600, help="抽样条数")
    ap.add_argument("--batches", default="16,32,64", help="GPU 批次候选，逗号分隔")
    ap.add_argument("--dir", help="抽样语料目录，默认 core.SOURCES（只有达妮娅.md 98 块，太窄）")
    ap.add_argument("--cpu-only-ref", action="store_true", help="只校验数值+GPU 速率，不跑 CPU 基准（CPU 正被建索引占用时用）")
    args = ap.parse_args()

    paths = None
    if args.dir:
        d = Path(args.dir).resolve()
        paths = sorted(p for p in list(d.rglob("*.md")) + list(d.rglob("*.txt")) if p.is_file())
    chunks = load_chunks(paths)
    print(f"语料 {len(chunks)} 块，随机抽 {args.n} 条（长度分布贴近真实）")
    random.seed(7)
    sample = random.sample(chunks, min(args.n, len(chunks)))
    texts = [c.text for c in sample]
    lens = np.array([len(t) for t in texts])
    print(f"字符长度：中位 {np.median(lens):.0f}，均值 {lens.mean():.0f}，最长 {lens.max()}")

    print("\n建 GPU 会话 ...", flush=True)
    t0 = time.time()
    gpu = Embedder(backend="gpu")
    print(f"  provider={gpu.active_provider}，用时 {time.time()-t0:.1f}s，显存 {gpu_mem()}")
    probe = gpu.encode(["达妮娅是谁"], batch=1)
    print(f"  预热 ok，shape={probe.shape}，norm={np.linalg.norm(probe):.4f}")

    print("\n建 CPU 会话（fp32 基准）...", flush=True)
    cpu = Embedder(backend="cpu")
    print(f"  provider={cpu.active_provider}")

    ref = cpu.encode(texts, batch=16)
    got = gpu.encode(texts, batch=32)

    cos = (ref * got).sum(axis=1) / (np.linalg.norm(ref, axis=1) * np.linalg.norm(got, axis=1) + 1e-9)
    print("\n=== 数值保真（fp16/GPU vs fp32/CPU，同一批文本）===")
    print(f"  余弦：min={cos.min():.6f}  p1={np.percentile(cos,1):.6f}  mean={cos.mean():.6f}")
    print(f"  逐元素最大绝对误差 {np.abs(ref-got).max():.2e}")
    # 检索真正在乎的是相对排序：自相似矩阵的秩相关
    top1 = (ref @ got.T).argmax(axis=1)
    print(f"  用 CPU 查询命中 GPU 同一条的比例 {np.mean(top1 == np.arange(len(texts))):.4f}")
    ok_fidelity = cos.min() > 0.998

    if args.cpu_only_ref:
        print("\n=== GPU 吞吐（CPU 正被建索引占用，这里不测 CPU 基准）===")
        best = (0.0, 0)
        for b in [int(x) for x in args.batches.split(",") if x]:
            rate, slow = bench(gpu, texts, b)
            print(f"  GPU fp16 batch={b:<3}：{rate:.1f} 块/秒（最慢一轮 {slow:.1f}s）显存 {gpu_mem()}", flush=True)
            if rate > best[0]:
                best = (rate, b)
        print(f"\n  全量 {len(chunks)} 块预估：{len(chunks)/max(0.01,best[0])/60:.1f} 分钟 @batch={best[1]}")
        return 0 if ok_fidelity else 3

    if not args.cpu_only_ref:
        print("\n=== 吞吐（CPU 侧与在建索引进程抢核，只会低估 CPU，不影响 GPU 结论）===")
        cpu_rate, _ = bench(cpu, texts, 16)
        print(f"  CPU fp32 batch=16：{cpu_rate:.1f} 块/秒")
        best = (0.0, 0)
        for b in [int(x) for x in args.batches.split(",")]:
            rate, slow = bench(gpu, texts, b)
            print(f"  GPU fp16 batch={b:<3}：{rate:.1f} 块/秒（最慢一轮 {slow:.1f}s）显存 {gpu_mem()}")
            if rate > best[0]:
                best = (rate, b)
        full = len(chunks)
        print(f"\n  全量 {full} 块预估：GPU {best[0]:.1f} 块/秒 @batch={best[1]} → {full/best[0]/60:.1f} 分钟"
              f"；CPU {cpu_rate:.1f} 块/秒 → {full/cpu_rate/60:.0f} 分钟")
        print(f"  加速比 ≈ {best[0]/max(0.01, cpu_rate):.1f}x")
        if best[0] / max(0.01, cpu_rate) < 1.5:
            print("  [!] 加速不明显，不值得切换")
            return 2
    return 0 if ok_fidelity else 3


if __name__ == "__main__":
    sys.exit(main())
