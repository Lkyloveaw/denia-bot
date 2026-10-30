r"""把 bge-m3 的 fp32 ONNX 转成 fp16，供 CUDA EP 使用。

为什么要转：显存只有 4 GB（桌面已占 ~1.8 GB），而 fp32 权重 model.onnx_data 有 2.27 GB，
连权重都装不下；fp16 约 1.13 GB，才能连同激活值放进剩余显存。

输出目录 models/bge-m3/onnx_fp16/，原目录不动，CPU 构建可以继续跑。

用法（必须用 GPU venv）：
    .venv-gpu/Scripts/python.exe to_fp16.py
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import onnx
from onnxruntime.transformers import float16

SRC = Path("models/bge-m3/onnx")
DST = Path("models/bge-m3/onnx_fp16")
# LayerNorm 保持 fp32：Transformer 的归一化层对下溢最敏感，代价很小但向量保真度明显更好。
BLOCK_OPS = ["LayerNormalization", "SimplifyLayerNorm", "Softmax", "ReduceMean"]


def main() -> None:
    t0 = time.time()
    DST.mkdir(parents=True, exist_ok=True)

    print(f"载入 {SRC/'model.onnx'}（含 external data）...", flush=True)
    model = onnx.load(str(SRC / "model.onnx"), load_external_data=True)
    print(f"  完成，用时 {time.time()-t0:.0f}s，图节点 {len(model.graph.node)}", flush=True)

    print("转 fp16 ...", flush=True)
    fp16_model = float16.convert_float_to_float16(
        model,
        keep_io_types=True,       # 输入 int64 / 输出 float32 不变，下游代码零改动
        disable_shape_infer=True,  # 已经知道是标准 BERT 图，省一次 shape 推断
        op_block_list=BLOCK_OPS,
    )
    del model

    out = DST / "model.onnx"
    print(f"写出 {out} ...", flush=True)
    onnx.save(
        fp16_model,
        str(out),
        save_as_external_data=True,
        all_tensors_to_one_file=True,
        location="model.onnx_data",
    )

    # tokenizer 与 CPU 版完全一致，直接复制，保证切后端不改变分词
    for name in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "config.json"):
        src_file = SRC / name
        if src_file.exists():
            shutil.copy2(src_file, DST / name)

    size = (DST / "model.onnx_data").stat().st_size / 1e9
    print(f"完成：{DST} 权重 {size:.2f} GB，总用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
