"""离线检查词条词典：不建向量、不起服务，纯字符串层面看「哪些查询能匹配到词条」。

闸门换成词条词典后，召回的天花板就是词典本身——这一步能先看出漏了谁，
省得等全量索引建完才发现某个梗压根不是个标题。

词频优先从已建好的 index/tokens.jsonl.gz 读（省掉一遍 2 万块分词），没有才现算。

用法：
    .venv\\Scripts\\python.exe check_lexicon.py --dir ../knowledge
    .venv\\Scripts\\python.exe check_lexicon.py --dir ../knowledge --q 今汐是谁 深塔怎么打
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import (  # noqa: E402
    INDEX_DIR,
    LEX_MAX_HEAD_HITS,
    LEX_MAX_PATH_HITS,
    LEX_MIN_CHARS,
    LEX_NAME_DF_FRAC,
    REV_MAX_DF_FRAC,
    build_lexicon,
    doc_freq,
    heading_hits,
    load_chunks,
    match_entries,
    norm_entry,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="语料目录")
    ap.add_argument("--q", nargs="*", default=[], help="额外试几条查询")
    ap.add_argument("--top", type=int, default=15, help="打印覆盖块数最多的 N 个词条")
    args = ap.parse_args()

    sources = sorted(
        p for p in list(Path(args.dir).rglob("*.md")) + list(Path(args.dir).rglob("*.txt")) if p.is_file()
    )
    chunks = load_chunks(sources)
    n = len(chunks)

    cache = INDEX_DIR / "tokens.jsonl.gz"
    if cache.exists():
        df: Counter[str] = Counter()
        with gzip.open(cache, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    df.update(set(json.loads(line)))
        print(f"文档频次来自 {cache.name}（{len(df)} 个词）", flush=True)
    else:
        print("现算文档频次（要分 2 万块词，稍等）", flush=True)
        df = doc_freq(chunks)

    lex, rev = build_lexicon(chunks, sources, df)
    covered = len({i for ids in lex.values() for i in ids})
    print(f"语料 {len(sources)} 个文件 / {n} 块 → 词条 {len(lex)} 条、简称 {len(rev)} 个，"
          f"覆盖 {covered} 块（{covered/max(1,n):.0%}）", flush=True)

    # 词典阈值体检：哪些名字被「常用词」规则刷掉了（其余规则都过，只差 df 这一刀）
    heads = heading_hits(sources)
    rows: dict[str, set[int]] = {}
    for c in chunks:
        for seg in (c.path or "").split(" > "):
            k = norm_entry(seg)
            if k:
                rows.setdefault(k, set()).add(c.id)
    dropped = [
        (k, len(v), df.get(k, 0)) for k, v in rows.items()
        if LEX_MIN_CHARS <= len(k) <= 4
        and heads.get(k, 0) <= LEX_MAX_HEAD_HITS
        and len(v) <= LEX_MAX_PATH_HITS
        and df.get(k, 0) > LEX_NAME_DF_FRAC * n
    ]
    print(f"\n被「常用词」规则刷掉 {len(dropped)} 个名字，按 df 排前 15：")
    for k, blk, d in sorted(dropped, key=lambda x: -x[2])[:15]:
        print(f"  {k:<12} df={d:<6}({d/n:.1%}) 路径 {blk} 块")

    print(f"\n简称表 df≤{REV_MAX_DF_FRAC:.1%} 的前 15 个（正向没命中时才用）：")
    for t, names in sorted(rev.items(), key=lambda kv: -df.get(kv[0], 0))[:15]:
        print(f"  {t:<10} df={df.get(t,0):<5} → {names[:3]}")

    sizes = sorted(lex.items(), key=lambda kv: -len(kv[1]))
    print(f"\n覆盖最多的 {args.top} 条（越接近上限越像类目而不是词条）：")
    for name, ids in sizes[: args.top]:
        print(f"  {name:<22} {len(ids):>4} 块")
    print(f"覆盖块数中位数 {sorted(len(v) for v in lex.values())[len(lex)//2]}")

    print("\n探针匹配：")
    from eval_gate import IRR, PERS, REL  # noqa: E402

    hit_rev = 0
    for kind, probes in (("REL", REL), ("PERS", PERS), ("IRR", IRR)):
        for q in probes:
            ents = match_entries(lex, q, rev)
            if ents and not any(x for *_, x in ents):
                hit_rev += 1
            print(f"  {kind:<5} {q:<14} → "
                  f"{[f'{n}({len(i)}块){'' if x else '[简称]'}' for n, i, x in ents] or '无'}")
    for q in args.q:
        ents = match_entries(lex, q, rev)
        print(f"  Q     {q:<14} → "
              f"{[f'{n}({len(i)}块){'' if x else '[简称]'}' for n, i, x in ents] or '无'}")
    print(f"\n只能靠反向简称命中的探针数：{hit_rev}")
    long_ones = [k for k in lex if len(k) > 12]
    print(f"名字超 12 字的 {len(long_ones)} 条，样例：{long_ones[:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
