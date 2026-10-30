#!/bin/bash
# 从 ModelScope 下载 bge-m3 的 ONNX 版（HuggingFace 在国内被墙）
BASE="https://modelscope.cn/api/v1/models/BAAI/bge-m3/repo?Revision=master&FilePath="
DEST="/d/Software/qqbot/rag/models/bge-m3"

fetch() {
  local remote="$1" local_path="$2"
  if [ -f "$DEST/$local_path" ] && [ -s "$DEST/$local_path" ]; then
    echo "[skip] $local_path 已存在 ($(du -h "$DEST/$local_path" | cut -f1))"
    return
  fi
  echo "[get ] $local_path"
  curl -L --retry 5 --retry-delay 3 --max-time 3600 -o "$DEST/$local_path" "${BASE}${remote}"
  echo "       -> $(du -h "$DEST/$local_path" 2>/dev/null | cut -f1)"
}

# 大文件
fetch "onnx/model.onnx_data"            "onnx/model.onnx_data"
# 模型图
fetch "onnx/model.onnx"                 "onnx/model.onnx"
# 分词器
fetch "onnx/tokenizer.json"             "onnx/tokenizer.json"
fetch "onnx/sentencepiece.bpe.model"    "onnx/sentencepiece.bpe.model"
fetch "onnx/tokenizer_config.json"      "onnx/tokenizer_config.json"
fetch "onnx/special_tokens_map.json"    "onnx/special_tokens_map.json"
# 配置
fetch "config.json"                     "config.json"
fetch "onnx/config.json"                "onnx/config.json"
fetch "tokenizer_config.json"           "tokenizer_config.json"
fetch "special_tokens_map.json"         "special_tokens_map.json"
fetch "sentence_bert_config.json"       "sentence_bert_config.json"
fetch "modules.json"                    "modules.json"
fetch "1_Pooling/config.json"           "1_Pooling/config.json"

echo "=== 完成 ==="
du -sh "$DEST"
