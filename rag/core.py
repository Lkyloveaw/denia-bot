"""达妮娅知识库检索核心：分块、bge-m3 向量化、BM25、混合召回。"""

from __future__ import annotations

import gzip
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "models" / "bge-m3" / "onnx"
INDEX_DIR = ROOT / "index"

SOURCES = [ROOT.parent / "knowledge" / "达妮娅.md"]

MAX_CHARS = 600
MIN_CHARS = 30
OVERLAP = 100
MODEL_MAX_TOKENS = 512
DIM = 1024

BM25_K1 = 1.5
BM25_B = 0.75

# 相关性闸门用「最高余弦 − 全库均值」而不是绝对余弦：中文短问句的绝对余弦基线
# 就在 0.40~0.55，"你好啊"能比"她的声优是谁"打得更高分。相对量才有区分度。
# 阈值随语料规模变化，所以在 build 时用探针句自校准，存进 meta.json。
CALIBRATION_PROBES = [
    "今天中午吃什么好呢", "帮我写一段python冒泡排序", "今天天气怎么样", "你好啊", "早上好",
    "你是谁开发的", "讲个笑话", "帮我查一下快递", "我心情不好", "明天要上班了",
    "1+1等于几", "推荐一部电影", "谢谢", "晚安", "你在干嘛",
    "我困了", "刚才卡了一下", "这个游戏好玩吗", "帮我看看代码", "我要去吃饭了",
    "最近好累", "周末干什么", "随便聊聊", "你叫什么名字", "给我唱首歌",
]
GATE_FLOOR, GATE_CEIL = 0.08, 0.30

# ---------------------------------------------------------------------------
# 词条词典闸门
# ---------------------------------------------------------------------------
# 分数闸门在全量语料上不成立（rag/analyze_gate.py 实测）：
# 「外面下雨了」的 top-全库均值=0.235 高于「你用什么武器」的 0.201，
# 因为全库均值随话题密度上升，恰好在最该注入的时候关死闸门；
# 而 340 万字的梗语料里确实有「哈哈哈」「晚安」这类条目，任何分数阈值都分不开
# “在问这个话题”和“顺口用了这个词”。
# 改成可解释的判据：查询里出现了词条名才注入。词条自动判定，不手写词表——
# 一个名字同时满足「很少作为标题重复」（模板段如 基础资料 会重复几十次）
# 和「只在少量块的路径里出现」（类目如 武器 522 块、共鸣者 2072 块），才算词条。
# 稀有度本身不够：实测「代码」全库只有 78 块、「快递」39 块，比很多术语还稀有，
# 所以「是标题里的名字」这一条不能松。反向简称（深塔→逆境深塔）单独一档：
# 只允许正向没命中时用，且子词 df 要低到 0.2%，注入底线也抬得更高。
LEX_MAX_HEAD_HITS = 20    # 作为标题最多出现这么多次：模板段重复上百次（基础信息 1754、剧情组件无边框 2035），
                          # 实体名反而常重复十几次（达妮娅 11 次），所以不能卡到 3
LEX_MAX_PATH_HITS = 300   # 作为路径段最多覆盖这么多块
LEX_MIN_CHARS = 1         # 单字词条允许，但只能整词命中（见 build_lexicon / match_entries）
LEX_MAX_CHARS = 24
LEX_NAME_DF_FRAC = 0.05   # 短名字（≤4 字）若在 5% 以上的块里出现，它是常用词不是词条：角色 25%、声骸 7.6%
REV_MAX_DF_FRAC = 0.002   # 反向子词要紧得多：深塔 0.2%、哈哈 0.8%
REV_MAX_NAMES = 5
REV_MIN_CHARS = 2
LEX_MAX_POOL = 600        # 命中词条合并后的候选池上限，超了就不是「查词条」而是「捞类目」
GATE_MODE = os.environ.get("KB_GATE_MODE", "lexicon")   # lexicon | margin

# MULTILINE：heading_hits 是对整篇文本 finditer，少了这个标记 ^$ 只认首尾各一行，
# 标题重复次数会全统计成 0，模板段（基础资料 出现 57 次）就混进词典了。
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
CATEGORY_PREFIX = re.compile(r"^[一二三四五六七八九十]+、")

STOPWORDS = set(
    "的 了 和 是 在 我 有 也 就 不 人 都 一 一个 上 说 要 你 会 与 及 或 等 这 那 他 她 它 "
    "其 为 以 于 之 并 很 更 最 到 从 被 把 让 向 对 但 而 且 因为 所以 如果 这个 那个 什么 怎么 "
)


@dataclass
class Chunk:
    id: int
    text: str
    path: str
    source: str
    category: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "path": self.path,
            "source": self.source,
            "category": self.category,
        }


def tokenize(text: str) -> list[str]:
    import jieba

    toks: list[str] = []
    for raw in jieba.lcut(text.lower()):
        w = raw.strip()
        if not w or w in STOPWORDS:
            continue
        if len(w) == 1 and not re.search(r"[\u4e00-\u9fff\w]", w):
            continue
        toks.append(w)
    return toks


def category_of(path: str) -> str:
    """取标题路径里的「一、人格与剧情」这类大章节名作为分类标签。"""
    for seg in path.split(" > "):
        if CATEGORY_PREFIX.match(seg):
            return seg
    return ""


def chunk_markdown(
    text: str, source: str, max_chars: int = MAX_CHARS, min_chars: int = MIN_CHARS
) -> list[tuple[str, str]]:
    """先按标题切段，超长段再按段落窗口二次切分。返回 (标题路径, 正文)。"""
    blocks: list[tuple[str, list[str]]] = []
    stack: list[tuple[int, str]] = []
    path = ""
    buf: list[str] = []

    for line in text.split("\n"):
        m = HEADING.match(line)
        if m:
            if buf and any(s.strip() for s in buf):
                blocks.append((path, buf))
            buf = []
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            path = " > ".join(t for _, t in stack)
            continue
        buf.append(line)
    if buf and any(s.strip() for s in buf):
        blocks.append((path, buf))

    out: list[tuple[str, str]] = []
    for block_path, lines in blocks:
        body = "\n".join(lines).strip()
        if not body:
            continue
        if len(body) <= max_chars:
            if len(body) >= min_chars:
                out.append((block_path, body))
            elif out:
                prev_path, prev = out[-1]
                out[-1] = (prev_path, f"{prev}\n\n{body}")
            continue

        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        cur = ""
        for para in paras:
            if len(para) > max_chars:
                if cur:
                    out.append((block_path, cur))
                    cur = ""
                step = max_chars - OVERLAP
                for i in range(0, len(para), step):
                    seg = para[i : i + max_chars]
                    if len(seg.strip()) >= min_chars:
                        out.append((block_path, seg))
                continue
            if cur and len(cur) + len(para) + 2 > max_chars:
                out.append((block_path, cur))
                cur = para
            else:
                cur = f"{cur}\n\n{para}" if cur else para
        if cur and len(cur) >= min_chars:
            out.append((block_path, cur))
    return out


def load_chunks(paths: list[Path] | None = None) -> list[Chunk]:
    chunks: list[Chunk] = []
    for p in paths or SOURCES:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for block_path, body in chunk_markdown(text, p.name):
            label = block_path.split(" > ")[-1] if block_path else p.stem
            chunks.append(
                Chunk(
                    id=len(chunks),
                    text=f"【{label}】\n{body}" if block_path else body,
                    path=block_path,
                    source=p.name,
                    category=category_of(block_path),
                )
            )
    return chunks


def _cuda_dll_dirs() -> list[Path]:
    """pip 装的 nvidia 轮子里的 CUDA/cuDNN DLL 目录。

    onnxruntime 的 Windows 轮子不会自动找它们（`capi/_ld_preload.py` 是空的），
    所以不显式挂上去就会静默回落 CPU——fp16 在 CPU 上比 fp32 还慢，比不转还糟。
    """
    import site

    dirs: list[Path] = []
    roots = {Path(p).resolve() for p in [*site.getsitepackages(), site.getusersitepackages()]}
    for root in roots:
        nvidia = root / "nvidia"
        if not nvidia.is_dir():
            continue
        for pkg in nvidia.iterdir():
            for sub in ("bin", "lib", "lib64"):
                cand = pkg / sub
                if not cand.is_dir():
                    continue
                # cu13 轮子把 DLL 放在 bin/x86_64、lib/x64 这类子目录里
                if any(cand.glob("*.dll")):
                    dirs.append(cand)
                else:
                    dirs.extend(p for p in cand.iterdir() if p.is_dir() and any(p.glob("*.dll")))
    return sorted(set(dirs))


def _enable_cuda() -> None:
    """必须在 import onnxruntime 之前调用。"""
    dirs = _cuda_dll_dirs()
    if not dirs:
        raise RuntimeError("找不到 pip 安装的 CUDA/cuDNN DLL，无法启用 GPU 后端")
    existing = os.environ.get("PATH", "")
    add = os.pathsep.join(str(d) for d in dirs if str(d) not in existing)
    if add:
        os.environ["PATH"] = add + os.pathsep + existing
        for d in dirs:
            try:
                os.add_dll_directory(str(d))
            except (AttributeError, OSError):
                pass


class Embedder:
    """bge-m3 向量化。

    backend="cpu" → fp32 模型，CPUExecutionProvider
    backend="gpu" → fp16 模型（to_fp16.py 产出），CUDAExecutionProvider
    两者用同一个 tokenizer.json，分词完全一致；切换后端必须整库重建，不能混用向量。
    """

    def __init__(self, threads: int = 0, backend: str = "cpu"):
        if backend == "gpu":
            _enable_cuda()
        import onnxruntime as ort

        self.backend = backend
        model_dir = MODEL_DIR.parent / "onnx_fp16" if backend in ("gpu", "cpu_fp16") else MODEL_DIR
        if backend == "gpu" and not (model_dir / "model.onnx").exists():
            raise FileNotFoundError(f"缺少 fp16 模型 {model_dir}/model.onnx，先跑 to_fp16.py")

        opts = ort.SessionOptions()
        providers = ["CPUExecutionProvider"]
        if backend == "gpu":
            # 线程数交给 CUDA，设置 CPU 线程反而会干扰
            providers = [
                ("CUDAExecutionProvider", {"device_id": 0, "cudnn_conv_algo_search": "DEFAULT"}),
                "CPUExecutionProvider",
            ]
        elif threads:
            opts.intra_op_num_threads = threads
            opts.inter_op_num_threads = threads
        self.sess = ort.InferenceSession(str(model_dir / "model.onnx"), sess_options=opts, providers=providers)
        if backend == "gpu" and self.sess.get_providers()[0] != "CUDAExecutionProvider":
            # 静默回落 CPU 是这次踩的坑：fp16 跑 CPU 比 fp32 慢一半，还不如不转
            raise RuntimeError(
                f"要求 CUDA 但实际 provider={self.sess.get_providers()}，"
                "检查 CUDA/cuDNN DLL 是否在 PATH（见 core._enable_cuda）"
            )

        from tokenizers import Tokenizer

        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.enable_truncation(max_length=MODEL_MAX_TOKENS)
        self.tok.enable_padding(pad_id=1, pad_token="</s>")

    @property
    def active_provider(self) -> str:
        return self.sess.get_providers()[0]

    def encode(self, texts: list[str], batch: int = 16) -> np.ndarray:
        out = []
        for i in range(0, len(texts), batch):
            enc = self.tok.encode_batch(texts[i : i + batch])
            feed = {
                "input_ids": np.fromiter((t for e in enc for t in e.ids), np.int64, len(enc) * len(enc[0].ids)).reshape(len(enc), -1),
                "attention_mask": np.fromiter(
                    (t for e in enc for t in e.attention_mask), np.int64, len(enc) * len(enc[0].attention_mask)
                ).reshape(len(enc), -1),
            }
            vec = self.sess.run(["sentence_embedding"], feed)[0].astype(np.float32)
            out.append(vec / (np.linalg.norm(vec, axis=1, keepdims=True) + 1e-9))
        return np.concatenate(out) if out else np.zeros((0, DIM), np.float32)


class BM25:
    def __init__(self, ids: list[int], docs: list[list[str]]):
        self.ids = ids
        self.doc_len = [len(d) for d in docs]
        self.avg_len = sum(self.doc_len) / max(1, len(self.doc_len))
        self.df: Counter[str] = Counter()
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for row, toks in enumerate(docs):
            tf = Counter(toks)
            self.df.update(tf.keys())   # 文档频次：一个词每篇只计一次，不是词频累加
            for term, freq in tf.items():
                self.postings[term].append((row, freq))
        self.n = len(ids)

    def search(self, query: str, top: int) -> list[tuple[int, float]]:
        scores: dict[int, float] = {}
        for term in tokenize(query):
            plist = self.postings.get(term)
            if not plist:
                continue
            idf = math.log(1 + (self.n - len(plist) + 0.5) / (len(plist) + 0.5))
            for row, freq in plist:
                norm = BM25_K1 * (1 - BM25_B + BM25_B * self.doc_len[row] / self.avg_len)
                scores[row] = scores.get(row, 0.0) + idf * freq * (BM25_K1 + 1) / (freq + norm)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top]
        return [(self.ids[r], s) for r, s in ranked]


ENTRY_NOISE = re.compile(r"[《》「」『』\"'’‘·、,，.。:：;；!！?？\-—\s]+")


def doc_freq(chunks: list[Chunk]) -> Counter[str]:
    """每个词出现在多少块里（文档频次）。建库时 BM25 顺手算过，离线脚本用这个。"""
    df: Counter[str] = Counter()
    for c in chunks:
        df.update(set(tokenize(c.text)))
    return df


def norm_entry(name: str) -> str:
    """词条名归一化：去引号书名号、去空白、转小写，让「称号「深塔的霸者」」也能匹配上。"""
    return ENTRY_NOISE.sub("", name).lower()


def heading_hits(paths: list[Path]) -> Counter[str]:
    """每个名字在语料里作为标题出现的次数（跨文件累计）。"""
    hits: Counter[str] = Counter()
    for p in paths:
        if not p.exists():
            continue
        for m in HEADING.finditer(p.read_text(encoding="utf-8")):
            hits[norm_entry(m.group(2))] += 1
    return hits


def build_lexicon(
    chunks: list[Chunk], paths: list[Path], df: Counter[str]
) -> tuple[dict[str, list[int]], dict[str, list[str]]]:
    """从标题自动抽词条词典，返回 (词条 → 块 id, 稀有子词 → 词条名)。

    正向：查询里直接出现词条名（「今汐是谁」）。
    反向：玩家口中的简称往往只是标题的一个子词（「深塔」→ 标题「逆境深塔」），
          所以把词条名再分词，只保留语料里稀有的子词做映射。
    稀有度用 BM25 的文档频次 df 量——实测「代码」在全库只出现在 78 块里，
    稀有本身不足以当判据，必须同时是「标题里的名字」。
    """
    heads = heading_hits(paths)
    total = max(1, len(chunks))
    rows: dict[str, list[int]] = defaultdict(list)
    for c in chunks:
        for seg in (c.path or "").split(" > "):
            key = norm_entry(seg)
            if key:
                rows[key].append(c.id)

    out: dict[str, list[int]] = {}
    for name, ids in rows.items():
        if not LEX_MIN_CHARS <= len(name) <= LEX_MAX_CHARS:
            continue
        if heads.get(name, 0) > LEX_MAX_HEAD_HITS or len(ids) > LEX_MAX_PATH_HITS:
            continue
        if not re.search(r"[\u4e00-\u9fff\w]", name):
            continue                      # 纯标点的段
        if len(name) <= 4 and df.get(name, 0) > LEX_NAME_DF_FRAC * total:
            continue                      # 「角色」「属性」这类常用词，标题里也有但不算词条
        out[name] = sorted(set(ids))
    out = dict(sorted(out.items(), key=lambda kv: -len(kv[0])))   # 长名优先，避免短名抢占匹配

    rev: dict[str, list[str]] = defaultdict(list)
    for name in out:
        if len(name) == 1:
            # 单字词条（角色「椿」）不进正向子串匹配——「这个角色」里的「角」会误命中，
            # 只让它通过分词命中，所以自己做自己简称的 key
            rev[name].append(name)
            continue
        for tok in tokenize(name):
            t = norm_entry(tok)
            if (
                len(t) >= REV_MIN_CHARS
                and t != name
                and df.get(t, 0) <= REV_MAX_DF_FRAC * total
                and len(rev[t]) < REV_MAX_NAMES
                and name not in rev[t]
            ):
                rev[t].append(name)
    return out, dict(rev)


def match_entries(
    lexicon: dict[str, list[int]], query: str, rev: dict[str, list[str]] | None = None
) -> list[tuple[str, list[int], bool]]:
    """查询对上了哪些词条，返回 (词条名, 块 id, 是否正向命中)。

    正向：整句里直接出现词条名（「今汐是谁」），可信度高。
    反向：只在正向全空时启用，用稀有子词对上标题里的简称（「深塔」→「逆境深塔」），
          标记 exact=False，让调用方给它抬一条更高的注入底线。
    最多 3 个词条、合并候选池不超过 LEX_MAX_POOL——再大就不是查词条而是捞类目，闸门该关。
    """
    q = norm_entry(query)
    if not q:
        return []
    found: dict[str, list[int]] = {}
    exact: dict[str, bool] = {}
    used: set[int] = set()

    def take(name: str, ids: list[int], is_exact: bool) -> bool:
        if not ids or name in found or len(found) >= 3:
            return False
        if len(used | set(ids)) > LEX_MAX_POOL:
            return False
        found[name] = ids
        exact[name] = is_exact
        used.update(ids)
        return True

    for name, ids in lexicon.items():
        if len(name) >= 2 and name in q:      # 单字词条只做分词匹配，见 build_lexicon
            take(name, ids, True)
            if len(found) >= 3:
                break

    if not found and rev:
        for tok in tokenize(query):
            t = norm_entry(tok)
            # 整句就等于这个词时（「晚安」）按闲聊处理：它只是碰巧是某个长标题的开头，
            # 不像在查词条。「深塔怎么打」这种带余下内容的才走简称。
            if q == t:
                continue
            for name in rev.get(t, ()):
                if take(name, lexicon.get(name, []), False):
                    break
    return [(n, i, exact[n]) for n, i in found.items()]


class Index:
    def __init__(self) -> None:
        self.chunks: dict[int, Chunk] = {}
        self.vectors: np.ndarray | None = None
        self.bm25: BM25 | None = None
        self.meta: dict = {}
        self.lexicon: dict[str, list[int]] = {}   # 词条名 → 属于该词条的块 id
        self.lex_rev: dict[str, list[str]] = {}   # 稀有子词 → 词条名（「深塔」→「逆境深塔」）

    @property
    def size(self) -> int:
        return len(self.chunks)

    @property
    def ready(self) -> bool:
        return self.size > 0 and self.vectors is not None

    @property
    def gate_margin(self) -> float:
        return float(self.meta.get("gate_margin", 0.15))

    @classmethod
    def dir(cls) -> Path:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        return INDEX_DIR

    @staticmethod
    def margin(vectors: np.ndarray, qv: np.ndarray) -> float:
        d = vectors @ qv
        return float(d.max() - d.mean())

    def calibrate(self, emb: Embedder) -> None:
        """用明显与语料无关的探针句量出「零分布」，闸门卡在探针最大值再留 5% 余量。"""
        pv = emb.encode(CALIBRATION_PROBES)
        margins = sorted(self.margin(self.vectors, pv[i]) for i in range(pv.shape[0]))
        raw = margins[-1] * 1.05
        self.meta["gate_margin"] = round(min(GATE_CEIL, max(GATE_FLOOR, raw)), 4)
        self.meta["gate_stats"] = {
            "probe_min": round(margins[0], 4),
            "probe_max": round(margins[-1], 4),
            "probe_median": round(margins[len(margins) // 2], 4),
        }

    @classmethod
    def build(cls, paths: list[Path] | None = None, embedder: Embedder | None = None) -> "Index":
        idx = cls()
        chunks = load_chunks(paths)
        if not chunks:
            raise RuntimeError("没有可索引的语料，检查 knowledge/ 目录")
        idx.chunks = {c.id: c for c in chunks}
        emb = embedder or Embedder()
        idx.vectors = emb.encode([c.text for c in chunks])
        idx.bm25 = BM25([c.id for c in chunks], [tokenize(c.text) for c in chunks])
        srcs = [Path(p).resolve() for p in (paths or SOURCES)]
        idx.lexicon, idx.lex_rev = build_lexicon(chunks, srcs, idx.bm25.df)
        idx.meta = {"dim": int(idx.vectors.shape[1]), "count": len(chunks), "entries": len(idx.lexicon),
                    "embed_backend": embedder.backend if embedder else "cpu"}
        idx.calibrate(emb)
        return idx

    @classmethod
    def load(cls) -> "Index":
        idx = cls()
        d = cls.dir()
        vec_file = d / "vectors.npy"
        if not vec_file.exists():
            return idx
        idx.vectors = np.load(vec_file)
        order: list[int] = []
        with gzip.open(d / "chunks.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                idx.chunks[row["id"]] = Chunk(**row)
                order.append(row["id"])
        with gzip.open(d / "tokens.jsonl.gz", "rt", encoding="utf-8") as f:
            docs = [[t for t in json.loads(line)] for line in f if line.strip()]
        idx.bm25 = BM25(order, docs)
        lex_file = d / "lexicon.json.gz"
        if lex_file.exists():
            with gzip.open(lex_file, "rt", encoding="utf-8") as f:
                raw = json.loads(f.read())
            # 早期版本存的是 {词条: [块 id]}，新版存 {"entries":…, "rev":…}，两种都读得动
            if "entries" in raw:
                idx.lexicon = {k: list(v) for k, v in raw["entries"].items()}
                idx.lex_rev = {k: list(v) for k, v in raw.get("rev", {}).items()}
            else:
                idx.lexicon = {k: list(v) for k, v in raw.items()}
        idx.meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        return idx

    def save(self, docs: list[list[str]] | None = None) -> None:
        """docs 传建库时已经分好的词，避免再跑一遍 2 万块的 jieba。"""
        d = self.dir()
        assert self.vectors is not None
        np.save(d / "vectors.npy", self.vectors)
        with gzip.open(d / "chunks.jsonl.gz", "wt", encoding="utf-8") as f:
            for c in sorted(self.chunks.values(), key=lambda x: x.id):
                f.write(json.dumps(c.as_dict(), ensure_ascii=False) + "\n")
        with gzip.open(d / "tokens.jsonl.gz", "wt", encoding="utf-8") as f:
            for i, cid in enumerate(sorted(self.chunks)):
                toks = docs[i] if docs is not None and i < len(docs) else tokenize(self.chunks[cid].text)
                f.write(json.dumps(toks, ensure_ascii=False) + "\n")
        with gzip.open(d / "lexicon.json.gz", "wt", encoding="utf-8") as f:
            f.write(json.dumps({"entries": self.lexicon, "rev": self.lex_rev}, ensure_ascii=False))
        (d / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class Hit:
    id: int
    text: str
    path: str
    source: str
    category: str
    score: float
    dense: float
    sparse: float

    def as_dict(self) -> dict:
        return self.__dict__ | {"score": round(self.score, 5), "dense": round(self.dense, 4), "sparse": round(self.sparse, 4)}


LEX_MIN_DENSE = 0.20       # 正向命中词条后的兜底绝对线，基本只为过滤向量异常
LEX_MIN_DENSE_REV = 0.40   # 靠简称反向命中时，信号弱一档，底线抬到常用余弦以上


def search(
    idx: Index,
    query: str,
    embedder: Embedder,
    top_k: int = 2,
    candidate: int = 12,
    margin: float | None = None,
    dropoff: float = 0.10,
    rrf_k: int = 60,
    category: str | None = None,
    stats: dict | None = None,
    gate: str | None = None,
) -> list[Hit]:
    """相关性闸门 + 稠密/稀疏 RRF 融合。

    gate="lexicon"（默认）：查询里出现词条名才注入，且只在属于该词条的块里排序；
        正向命中用 LEX_MIN_DENSE 兜底，靠简称反向命中时用更高的 LEX_MIN_DENSE_REV。
    gate="margin"：老逻辑，全库 top−均值过阈值，用来 A/B。
    stats 传一个空 dict 就填上本次观测量（调闸门时用）。
    """
    if not idx.ready or not query.strip():
        return []

    gate = gate or GATE_MODE
    qv = embedder.encode([query])[0]
    dense_all = idx.vectors @ qv
    mean_dense, top_dense = float(dense_all.mean()), float(dense_all.max())

    entries = match_entries(idx.lexicon, query, idx.lex_rev) if gate == "lexicon" else []
    floor = 0.0
    if stats is not None:
        stats.update(
            gate_mode=gate,
            entries=[e for e, *_ in entries],
            mean_dense=round(mean_dense, 4),
            top_dense=round(top_dense, 4),
            margin_observed=round(top_dense - mean_dense, 4),
        )

    if gate == "lexicon":
        if not idx.lexicon:
            raise RuntimeError("索引里没有 lexicon.json.gz，重跑 build_index.py 生成词条词典")
        if not entries:
            return []
        # 只在命中词条的块里排序：把库外行的分数钉成 -1，argsort 自然把它们甩到最后
        pool = np.unique(np.concatenate([np.asarray(i) for _, i, _ in entries]))
        scores = np.full(dense_all.shape, -1.0, np.float32)
        scores[pool] = dense_all[pool]
        exact = any(x for *_, x in entries)
        floor = LEX_MIN_DENSE if exact else LEX_MIN_DENSE_REV
        if stats is not None:
            stats.update(pool=int(pool.size), floor=round(floor, 4), exact=exact,
                         pool_top=round(float(scores.max()), 4))
        if float(scores.max()) < floor:
            return []
    else:
        scores = dense_all
        margin_val = idx.gate_margin if margin is None else margin
        floor = mean_dense + margin_val
        if stats is not None:
            stats.update(gate=margin_val, floor=round(floor, 4))
        if top_dense < floor:
            return []

    in_pool = {int(i) for i in np.where(scores >= 0)[0]} if gate == "lexicon" else None
    dense_rank = [int(i) for i in np.argsort(-scores)[:candidate] if in_pool is None or int(i) in in_pool]

    sparse: list[tuple[int, float]] = []
    if idx.bm25:
        # 词法命中的候选池：lexicon 模式下要放宽再筛回词条池，否则 300 块的池子
        # 在全库 BM25 前 12 名里几乎占不到位置，BM25 就形同虚设
        wide = idx.bm25.search(query, candidate * (40 if in_pool is not None else 1))
        sparse = [t for t in wide if in_pool is None or t[0] in in_pool][:candidate]

    d_rank = {cid: r for r, cid in enumerate(dense_rank)}
    s_rank = {cid: r for r, (cid, _) in enumerate(sparse)}
    s_abs = {cid: sc for cid, sc in sparse}

    fused: dict[int, float] = defaultdict(float)
    for cid in d_rank:
        fused[cid] += 1.0 / (rrf_k + d_rank[cid])
    for cid in s_rank:
        fused[cid] += 1.0 / (rrf_k + s_rank[cid])

    best = float(scores[dense_rank[0]]) if dense_rank else top_dense
    hits: list[Hit] = []
    for cid, score in sorted(fused.items(), key=lambda kv: kv[1], reverse=True):
        cos = float(scores[cid])
        if cos < floor or cos < best - dropoff:
            continue
        ch = idx.chunks.get(cid)
        if ch is None or (category and category not in ch.category):
            continue
        hits.append(
            Hit(
                id=cid,
                text=ch.text,
                path=ch.path,
                source=ch.source,
                category=ch.category,
                score=score,
                dense=cos,
                sparse=s_abs.get(cid, 0.0),
            )
        )
        if len(hits) >= top_k:
            break
    return hits
