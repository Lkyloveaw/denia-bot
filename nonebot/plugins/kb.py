"""知识库检索注入 —— 调 rag/ 的本地检索服务，把命中的设定片段拼进本轮 system prompt。

只在检索判定「相关」时才注入，无关就一条都不塞（避免资料稀释人格）。
检索服务挂了 / 超时，直接当没命中，不影响正常聊天。

拿哪一句去查由 `kb_logic.py` 定（纯逻辑，`tests\test_kb_logic.py` 直接测它）：
聊天时「达妮娅，今天中午吃什么」要剥掉唤醒词，「达妮娅是谁」不能剥 —— 唤醒词本身就是
知识库里的一个大词条，剥错了要么漏注、要么把她的设定塞进闲聊。

指令：
    /kb            查看状态
    /kb on|off     本会话开关自动注入
    /kb 查 <问题>   拿**原文**试检索，并说清是命中了、没命中、还是压根没去查
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx
from nonebot import on_command
from nonebot.adapters import Event
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Message
from muicebot.llm import ModelRequest
from muicebot.plugin import PluginMetadata
from muicebot.plugin.hook import on_before_completion

try:
    import kb_logic as kbl
except ImportError:  # 不是从 nonebot\ 目录启动的，把工程根补进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import kb_logic as kbl

__plugin_meta__ = PluginMetadata(
    name="kb", description="鸣潮设定知识库检索注入", usage=".kb on | .kb off | .kb 查 <问题>"
)

RAG_URL = os.getenv("KB_RAG_URL", "http://127.0.0.1:8091")
RAG_TIMEOUT = float(os.getenv("KB_RAG_TIMEOUT", "2.5"))
TOP_K = int(os.getenv("KB_TOP_K", "2"))
MAX_CHARS = int(os.getenv("KB_MAX_CHARS", "1100"))


def _env_file_value(key: str) -> str:
    """直接翻 nonebot/.env —— NoneBot 读它时并不会写进 os.environ，别指望 getenv。"""
    try:
        for line in (Path(__file__).resolve().parent.parent / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip()
    except OSError:
        pass
    return ""


def _nicknames() -> tuple[str, ...]:
    """MuiceBot 的唤醒词，跟它的配置同源。

    顺序：driver 配置（MuiceBot 自己用的就是这份）→ .env 文件 → os.environ。
    长名字排前面，剥前缀时「达妮娅」要优先于单字「娅」。
    """
    names: set[str] = set()
    try:
        from nonebot import get_driver
        for n in getattr(get_driver().config, "muice_nicknames", None) or []:
            if str(n).strip():
                names.add(str(n).strip())
    except Exception:  # noqa: BLE001 - 拿不到配置就用兜底
        pass
    if not names:
        raw = _env_file_value("MUICE_NICKNAMES") or os.getenv("MUICE_NICKNAMES", "")
        names = set(re.findall(r'"([^"]+)"', raw)) | set(re.findall(r"'([^']+)'", raw))
    names = {n for n in names if n.strip()}
    if not names:
        names = {"达妮娅"}
    return tuple(sorted(names, key=len, reverse=True))


NICKNAMES = _nicknames()


# 人格提示词永远排在检索内容前面，这段话只规定「怎么用资料」
FRAME = (
    "\n\n【资料参考】闸门按词条命中打开，所以这几段是你熟的东西：鸣潮的角色与玩法、"
    "网络梗的出处，以及你自己的设定记录。对得上对方说的才用，对不上就当中没有。"
    "用你自己的口吻讲，不要照搬原文，不要提「资料」「设定」「查到」「检索」这类词，"
    "也不要因此突然变得像在科普——你还是你，聊天优先。"
)

_enabled: dict[str, bool] = {}


def _key(event: Event) -> str:
    try:
        return event.get_session_id()
    except Exception:  # noqa: BLE001
        return str(event.get_user_id())


def _on(session: str) -> bool:
    return _enabled.get(session, True)


async def _search_once(query: str) -> tuple[list[dict], dict]:
    """把**这一句**原样发给检索服务，返回 (命中块, 闸门观测量)。

    服务不可用 / 超时当「没查到」，绝不拖垮回复；但要留下 `error`，
    否则诊断里分不清「没命中」和「没问成」。
    """
    try:
        async with httpx.AsyncClient(timeout=RAG_TIMEOUT) as client:
            r = await client.post(
                f"{RAG_URL}/search",
                json={"query": query, "top_k": TOP_K, "max_chars": MAX_CHARS},
            )
            r.raise_for_status()
            d = r.json()
            return d.get("hits", []), {k: d.get(k) for k in ("entries", "pool", "exact")}
    except Exception as e:  # noqa: BLE001 - 检索失败绝不能拖垮回复
        logger.debug(f"[kb] 检索服务不可用: {e}")
        return [], {"error": f"{type(e).__name__}: {e}"}


def _gate_line(stats: dict) -> str:
    """把服务给的闸门观测量说成人话。别把「没问成」写成「未命中」。"""
    if stats.get("error"):
        return f"闸门：没问成 —— 检索服务没答上来（{stats['error']}）"
    entries = stats.get("entries")
    gate = f"闸门：词条 {'、'.join(entries) if entries else '未命中'}"
    if stats.get("pool"):
        gate += f" / 候选 {stats['pool']} 块{'（简称）' if stats.get('exact') is False else ''}"
    return gate


@on_before_completion(priority=5)
async def inject(request: ModelRequest, event: Event) -> None:
    if not _on(_key(event)):
        return

    # 拿哪一句去查由 kb_logic 定：叫她一声（「达妮娅，…」）要剥，问她的事情不能剥。
    plans = kbl.plan_queries(request.prompt or "", NICKNAMES)
    if not plans:
        return
    q, hits, stats = await kbl.pick_hits(plans, _search_once)
    if not hits:
        return

    parts = [FRAME]
    budget = MAX_CHARS
    for h in hits:
        text = h["text"][:budget]
        parts.append(f"〈{h['path'].split(' > ')[-1]}〉\n{text}")
        budget -= len(text)
        if budget <= 0:
            break

    block = "\n\n".join(parts)
    if request.system:
        request.system += block
    else:
        request.prompt = (request.prompt or "") + block
    entries = "、".join(stats.get("entries") or []) or "—"
    logger.info(f"[kb] 注入 {len(hits)} 块 / {len(block)} 字，检索词「{q[:24]}」命中词条「{entries}」")


async def run_probe(arg: str) -> str | None:
    """`/kb 查 <问题>` 的回复正文；传进来的不是试检索子命令则返回 None。

    四种结果分开说，因为「没查」和「查了没命中」是两个完全不同的毛病：
    没去查（太短）/ 没问成（服务挂了）/ 查了没命中 / 命中。
    查的是**原文** —— 这条指令问的是「这句话会不会命中」，不是「聊天时会拿哪句去查」，
    所以这里绝不能剥唤醒词（剥了就会把「查 达妮娅」报成一个假的「未命中」）。
    两者确实不一致时补一行提示，省得对着诊断结果猜聊天的行为。
    """
    probe = kbl.parse_probe_command(arg)
    if probe is None:
        return None
    if not probe:
        return "用法：.kb 查 <问题>"

    q = kbl.diagnostic_query(probe)
    if not q:
        return (
            f"没去查：「{probe}」不足 {kbl.MIN_QUERY} 个字，检索服务根本没收到请求 —— "
            "这跟查了没结果是两回事。"
        )

    hits, stats = await _search_once(q)
    lines = [
        f"「{q}」命中 {len(hits)} 块：" if hits else f"查了「{q}」，没命中。",
        _gate_line(stats),
    ]
    plans = kbl.plan_queries(probe, NICKNAMES)
    chat = plans[0] if plans else ""
    if chat != q:
        lines.append(
            f"提示：聊天时这句会拿「{chat or '（太短，不去查）'}」去查 —— "
            "剥掉唤醒词是怕把她自己的设定塞进无关闲聊。"
        )
    for h in hits:
        lines.append(f"— {h['path']}  (相关度 {h['dense']:.3f})\n{h['text'][:160]}")
    return "\n".join(lines)


kb = on_command("kb", aliases={"知识库"}, priority=1, block=True)


@kb.handle()
async def status(event: Event, args: Message = CommandArg()) -> None:
    raw = args.extract_plain_text().strip()
    arg = raw.lower()  # 只给 on/off 这类关键词用小写；问题原文不能改大小写
    session = _key(event)

    if arg in ("on", "开", "开启"):
        _enabled[session] = True
        await kb.finish("知识库自动注入：已开启")
    if arg in ("off", "关", "关闭"):
        _enabled[session] = False
        await kb.finish("知识库自动注入：已关闭（只按人格说话）")

    reply = await run_probe(raw)  # None = 这条参数不是试检索子命令，继续走状态汇报
    if reply is not None:
        await kb.finish(reply)

    try:
        async with httpx.AsyncClient(timeout=2) as c:
            health = (await c.get(f"{RAG_URL}/health")).json()
        svc = f"已连接，索引 {health.get('chunks')} 块" if health.get("ready") else "服务在线但索引未就绪"
    except Exception:  # noqa: BLE001
        svc = "未启动（运行 启动机器人.bat，或 rag\\.venv-gpu\\Scripts\\python.exe rag\\serve.py）"

    await kb.finish(
        f"本会话自动注入：{'开' if _on(session) else '关'}\n检索服务：{svc}\n"
        f"指令：.kb on | .kb off | .kb 查 <问题>"
    )
