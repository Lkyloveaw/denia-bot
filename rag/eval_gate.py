"""检索闸门复测：全量语料下量三件事——该注入的注入了没有、闲聊有没有硬塞百科、
匹配到的词条和候选池长什么样。

探针分三类：
  REL  = 语料里有对应资料、且人格卡里没写的问句 → 期望注入 ≥1 条
  PERS = 达妮娅自己的设定 → 人格提示词已经回答了，注入与否都算正常，只做记录
  IRR  = 纯闲聊，语料里没有答案 → 期望注入 0 条（人格优先，不许硬塞百科）

用法：
    .venv\\Scripts\\python.exe eval_gate.py                 # 用服务端默认闸门
    .venv\\Scripts\\python.exe eval_gate.py --gate margin   # A/B 老的分数闸门
    .venv\\Scripts\\python.exe eval_gate.py --quiet        # 只打印出问题的探针
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8091"

REL = [
    "鸣潮里抽卡保底是多少",
    "武器合成图纸怎么获得",
    "共鸣者是什么意思",
    ".moe 是什么梗",
    "典中典是什么意思",
    "乐是什么梗",
    "蚌埠住是什么意思",
    "库街区是什么",
    "今汐是谁",
    "深塔怎么打",
    "椿这个角色怎么样",
    "今汐的声优是谁",
]

PERS = [
    "她是谁",
    "你的声优是谁",
    "你是什么属性的角色",
    "你用什么武器",
    "你还记得那个生日蛋糕吗",
]

IRR = [
    "你好啊",
    "在吗",
    "谢谢啦",
    "今天中午吃什么好呢",
    "哈哈哈笑死我了",
    "早上好",
    "帮我写个周报",
    "外面下雨了",
    "我明天要早起",
    "这道题怎么做",
    "晚安",
    "陪我聊聊天",
    "这个月话费好多",
    "刚刚那局游戏赢了吗",
    "发个红包呗",
    "你在干嘛呢",
    "？",
    "滚",
    "再见",
]


def search(query: str, gate: str | None, margin: float | None) -> dict:
    body: dict = {"query": query, "top_k": 2}
    if gate:
        body["gate"] = gate
    if margin is not None:
        body["margin"] = margin
    req = urllib.request.Request(
        f"{BASE}/search", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", choices=["lexicon", "margin"], help="覆盖服务端默认闸门")
    ap.add_argument("--margin", type=float, help="margin 闸门下覆盖自校准阈值")
    ap.add_argument("--quiet", action="store_true", help="只打印出问题的探针")
    args = ap.parse_args()

    health = json.loads(urllib.request.urlopen(f"{BASE}/health", timeout=30).read())
    mode = args.gate or health.get("gate_mode", "margin")
    print(f"索引 {health['chunks']} 块 / 词条 {health.get('entries')} 条 / 本次闸门 {mode}")
    if mode == "margin":
        print(f"          阈值 {args.margin if args.margin is not None else health.get('gate_margin')}")
    print()

    miss, false_pos, no_entry = [], [], []
    for kind, probes, want in (("REL", REL, True), ("PERS", PERS, None), ("IRR", IRR, False)):
        print(f"=== {kind}：{'期望命中' if want else ('只做记录' if want is None else '期望零注入')} ===")
        for q in probes:
            d = search(q, args.gate, args.margin)
            hits = d["hits"]
            ents = d.get("entries") or []
            ok = bool(hits) if want else (True if want is None else not hits)
            if want and not hits:
                miss.append(q)
                if not ents:
                    no_entry.append(q)
            if want is False and hits:
                false_pos.append((q, hits[0]["path"].split(" > ")[-1], ents))
            if ok and args.quiet:
                continue
            print(f"  {'OK ' if ok else '!! '} {q:<14} 词条={ents or '—'} 池={d.get('pool', '—')} "
                  f"top={d.get('top_dense', 0.0):.3f} 底线={d.get('floor', 0.0):.3f} "
                  f"→ 注入 {len(hits)} 条"
                  f"{'' if not hits else ' → ' + hits[0]['path'].split(' > ')[-1][:34]}")
        print()

    print(f"[{'通过' if not (miss or false_pos) else '有问题'}] "
          f"REL 漏 {len(miss)}/{len(REL)}：" + ("无" if not miss else ""))
    for q in miss:
        print(f"          {q}{'（词典里压根没这个词条 → 覆盖缺口）' if q in no_entry else '（匹配到了但分数没过底线）'}")
    print(f"        IRR 误注 {len(false_pos)}/{len(IRR)}：" + ("无" if not false_pos else ""))
    for q, path, ents in false_pos:
        print(f"          {q} → {path}（词条 {ents}）")
    return 1 if (miss or false_pos) else 0


if __name__ == "__main__":
    sys.exit(main())
