r"""GPU 最小冒烟：确认 fp16 模型真的跑在 CUDA 上，而不是静默回落 CPU。

判据三条，缺一不可：
  1. Embedder 建的会话 active provider 是 CUDAExecutionProvider（回落会直接抛错）
  2. 建模前后显存涨了约 1.1 GB
  3. 重复推理耗时稳定，且明显快过 CPU
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from core import Embedder, _cuda_dll_dirs, load_chunks  # noqa: E402


def vram_mib() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10,
    )
    return int(out.stdout.strip().splitlines()[0])


print("找到的 CUDA DLL 目录:")
for d in _cuda_dll_dirs():
    print("  ", d)

chunks = load_chunks()
rng = np.random.default_rng(7)
rows = rng.choice(len(chunks), 64, replace=False)
texts = [chunks[int(r)].text for r in rows]
lens = sorted(len(t) for t in texts)
print(f"语料 {len(chunks)} 块，取 64 条真实块，字符长度 p50={lens[32]} p95={lens[60]} max={lens[-1]}")

before = vram_mib()
print(f"建模前显存 {before} MiB", flush=True)
t0 = time.time()
emb = Embedder(backend="gpu")
print(f"建模用时 {time.time()-t0:.1f}s，active={emb.active_provider}", flush=True)
after = vram_mib()
print(f"建模后显存 {after} MiB（差 {after-before} MiB）", flush=True)

for rep in range(4):
    t0 = time.time()
    vec = emb.encode(texts, batch=32)
    dt = time.time() - t0
    print(f"  第{rep+1}次 64 条 batch=32：{dt:.2f}s → {64/dt:.1f} 块/秒，shape={vec.shape} "
          f"范数1={np.allclose(np.linalg.norm(vec,axis=1),1,atol=1e-3)} 显存 {vram_mib()} MiB", flush=True)
