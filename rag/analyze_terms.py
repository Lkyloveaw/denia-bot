"""词条召回诊断：量每个候选关键词在语料里「有多稀有」，用来定词典阈值。

纯标题词典会漏掉玩家口中的简称（「深塔」→ 标题其实叫「逆境深塔」），
也挡不住标题里混进来的常用词（「角色」「属性」）。两头都要数据说话：
一个词该进词典，得是「只在少量块里出现」的专名；一个词该触发反向匹配，
df 得更低。

用法：
    .venv\\Scripts\\python.exe analyze_terms.py --dir ../knowledge
    .venv\\Scripts\\python.exe analyze_terms.py --dir ../knowledge --t 深塔 今汐 角色 乐
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import build_lexicon, load_chunks, tokenize  # noqa: E402

# 探针：REL 里希望进词典的、IRR 里必须挡掉的、以及两边共用的常用词
PROBE_TERMS = [
    "今汐", "库街区", "逆境深塔", "深塔", "声骸", "共鸣者", "武器", "角色", "属性", "moe",
    "乐", "蚌埠", "典中典", "保底", "抽卡", "图纸", "合成",
    "你好", "谢谢", "晚安", "下雨", "话费", "红包", "题目", "周报", "快递", "游戏",
    "哈哈", "上班", "心情", "代码",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--t", nargs="*", default=[], help="额外要量的词")
    args = ap.parse_args()

    sources = sorted(
        p for p in list(Path(args.dir).rglob("*.md")) + list(Path(args.dir).rglob("*.txt")) if p.is_file()
    )
    chunks = load_chunks(sources)
    n = len(chunks)
    print(f"{n} 块，开始统计…", flush=True)

    texts = [c.text for c in chunks]
    docs = [set(tokenize(t)) for t in texts]
    df: Counter[str] = Counter()
    for d in docs:
        df.update(d)
    lex, _rev = build_lexicon(chunks, sources, df)
    lex_ids = set(lex)

    rows = []
    for term in PROBE_TERMS + args.t:
        key = term.lower()
        sub = sum(1 for t in texts if key in t)                    # 子串命中块数
        tok = sum(1 for d in docs if key in d)                     # 分词命中块数
        heads = sum(1 for c in chunks if key in (c.path or "").lower())
        rows.append((term, sub, tok, heads, key in lex_ids))

    print(f"\n{'词':<8}{'子串块':>8}{'分词块':>8}{'路径块':>8}  {'占比':>7}  在词典")
    for term, sub, tok, heads, in_lex in rows:
        pct = sub / n
        print(f"{term:<8}{sub:>8}{tok:>8}{heads:>8}  {pct:>6.1%}  {'是' if in_lex else '—'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
