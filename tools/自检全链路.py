r"""
自检全链路 —— 不碰 QQ，直接从 OneBot 通道喂一条消息进 NoneBot，看机器人回不回。

为什么要它：
  SnowLuma 是「连到 NoneBot 的 ws 客户端」，事件由它推进来。本脚本扮演同一个角色，
  连上 ws://127.0.0.1:8080/onebot/v11/ws 推一条 message 事件，再把 NoneBot 发回来
  的 API 调用（send_msg 等）原样打印 —— 这样能单独验证
      NoneBot → kb 检索注入 → AxonHub → 人格回复
  这一段，不必依赖真的有人在 QQ 里 @机器人。
  只走本地回环，且 self_id 用的是假的号，不会给任何真实 QQ 用户发消息。

握手要求（nonebot/adapters/onebot/v11/adapter.py:247）：
  · 必须带 X-Self-ID 头；真 bot 的 id 已被 SnowLuma 占用，重复会被 1008 踢掉，
    所以这里默认用 1000000001 这个假 id。
  · 必须带 Authorization: Bearer <ONEBOT_ACCESS_TOKEN>（从 nonebot/.env 读，不抄第二遍）。

触发条件（MuiceBot + 自家 gate 插件）：
  · 私聊：直接就能触发。
  · 群聊：默认只回「真的 @ 了机器人」的消息（叫名字也不行），
    且每人每群每天有回复上限（默认 20 次，超了就完全静默）。
  · self_id 是假的，但 @ 段里的 qq 用的就是这个假 self_id，闸门认得出来。
  · 主号 10000001 不受 @ 与上限约束（SUPERUSERS 豁免）。

用法：
    python 自检全链路.py                                  # 「达妮娅，今汐是谁」，应命中知识库
    python 自检全链路.py --text "你好呀"
    python 自检全链路.py --group 1108838060 --at          # 群里 @ 机器人，应该回
    python 自检全链路.py --group 1108838060               # 群里不 @，应该完全静默
    python 自检全链路.py --user 10000001 --text "限流 默认 1"    # 私聊把上限设成 1
    python 自检全链路.py --group 1108838060 --at --repeat 2 --timeout 25
                                                          # 连发两条：第 1 条回、第 2 条静默
    python 自检全链路.py --no-nickname                    # 不加昵称前缀，验证「不触发」

    python 自检全链路.py --group 922192732 --at --hang-name
        # 假装不答「这人叫什么」那两个接口。**注意**：真 SnowLuma 连着的时候这一招咬不到 ——
        #   muice.py:174 空手调 get_username()，里面 nonebot.get_bot() 返回的是**第一个**连上的 Bot
        #   （= 真 SnowLuma），那一跳根本不走本脚本这条 socket。验超时保险请改
        #   NAME_GUARD_TIMEOUT=0.05 重启（判据见 交接文档.md §6.6 / §16 第 11 步）。

QQ「回复（引用）」的三种情形（都带 --group，多 --quote / --quote-by）：
    python 自检全链路.py --group 922192732 --quote-by -1 --quote "嗯……我在呢" --text "那你回来看看"
        回复**她自己说过的**那句。真机上 QQ 会在正文前自动 @ 被引用那条的作者，所以脚本
        跟着补 at 段 —— 但那个 @ 不算在叫她（`QUOTE_AT_COUNTS=0`，2026-09-27 主人定的），
        闸门该拦。判据看日志：`[gate] 这个 @ 是引用自动补的…` + `[gate] 拦下`。
    python 自检全链路.py --group 922192732 --quote-by -1 --manual-at --quote "嗯……我在呢" --text "那你回来看看"
        回复她 + **又自己手打了一个 @** —— 这才该放行，也是验 quote_fix（引用渲染）的群聊用法。
    python 自检全链路.py --group 922192732 --quote-by 982272752 --quote "@达妮娅 在吗" --text "哈哈"
        @ 只活在**被引用的那段内容**里。当前事件里没有指向她的 at 段，闸门按没 @ 处理 —— 静默
        是设计如此（想改这条要动 gate，见交接文档里的 FIX 2）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "nonebot" / ".env"
PASS_FILE = ROOT / "tools" / "snowluma-webui-pass.txt"
BASE = "http://127.0.0.1:5099"
WS_URL = "ws://127.0.0.1:8080/onebot/v11/ws"

FAKE_SELF_ID = "1000000001"
FAKE_SELF_NICK = "达妮娅"

# 假装发消息的人叫什么 —— MuiceBot 在群里会把消息拼成 `<这个人的名片> 正文`
# （muice.py:174）再喂给模型，所以这两个值直接决定「模型看到的署名」。
# 线上那次「答非所问」就是主人昵称叫「对乙酰氨基酚」被模型当成了话题：
#     python 自检全链路.py --group 922192732 --at --nickname 对乙酰氨基酚 --text "娅娅"
NICK = "自检"
CARD = ""

# 我们「演」出来的历史消息：{message_id: get_msg 该返回的那一坨}。
# 引用回复的原文真机是 NoneBot 反查 get_msg 拿到的，不给它就整条事件被丢掉。
FAKE_MSGS: dict[int, dict] = {}

# --hang-name 时置真：「问名字」这两个接口一概不作答，模拟协议端挂住。
HANG_NAME = False
NAME_ACTIONS = ("get_group_member_info", "get_stranger_info")


def env_value(key: str, default: str = "") -> str:
    """从 nonebot/.env 里取值，避免同一份配置抄两遍。"""
    try:
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip()
    except OSError:
        pass
    return default


def webui_token() -> str | None:
    if not PASS_FILE.exists():
        return None
    pw = PASS_FILE.read_text(encoding="utf-8").strip()
    try:
        req = urllib.request.Request(
            BASE + "/api/login",
            data=json.dumps({"password": pw}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode()).get("token")
    except Exception:  # noqa: BLE001
        return None


def hooked_accounts() -> list[tuple[str, str]]:
    """问 SnowLuma 现在 hook 住了哪些号：[(uin, 昵称)]。"""
    tok = webui_token()
    if not tok:
        return []
    try:
        req = urllib.request.Request(BASE + "/api/connections",
                                     headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            rows = json.loads(r.read().decode()).get("list", [])
        return [(str(x.get("uin") or ""), str(x.get("nickname") or "")) for x in rows]
    except Exception:  # noqa: BLE001
        return []


def ok(echo, data: dict | None = None) -> str:
    return json.dumps({"status": "ok", "retcode": 0, "wording": "",
                       "data": data or {}, "message": "", "user_id": 0, "time": 0,
                       **({"echo": echo} if echo is not None else {})}, ensure_ascii=False)


def answer(action: str, params: dict) -> dict:
    """机器人可能反过来问我们一些 OneBot 接口，给个像样的假数据别让它卡住。"""
    if action == "get_login_info":
        return {"user_id": int(FAKE_SELF_ID), "nickname": FAKE_SELF_NICK}
    if action == "get_status" or action == "get_version_info":
        return {"online": True, "good": True, "app_name": "selfcheck", "app_version": "0"}
    if action == "get_msg":
        # 引用回复的原文真机是从这里取的：返回空 dict 会让适配器
        # 抛 `6 validation errors for Reply`，整条事件被 matcher 直接丢掉，
        # 连 MuiceBot 都进不去 —— 那就什么都没验到。
        mid = params.get("message_id")
        try:
            return FAKE_MSGS.get(int(mid), {})
        except (TypeError, ValueError):
            return {}
    if action == "get_group_member_info":
        return {"user_id": int(params.get("user_id") or 0), "nickname": NICK,
                "card": CARD, "sex": "unknown", "age": 0, "level": "1", "role": "member",
                "group_id": int(params.get("group_id") or 0), "group_name": "自检群"}
    if action == "get_stranger_info":
        return {"user_id": int(params.get("user_id") or 0), "nickname": NICK,
                "card": CARD, "sex": "unknown", "age": 0, "level": "1",
                "qq": int(params.get("user_id") or 0)}
    if action == "get_group_info":
        return {"group_id": int(params.get("group_id") or 0), "group_name": "自检群"}
    if action in ("get_group_list", "get_group_member_list", "get_friend_list"):
        return {}
    return {}


def quoted_message(text: str, author: int, group: int) -> dict:
    """事件里那个 `reply` 字段 —— OneBot 把被引用的原话直接带在推送里。

    alconna 的 OneBot v11 分支就读这里（`uniseg/adapters/onebot11/builder.py:64-69`），
    交出去的是**框架原生 Message**，MuiceBot 拿去 `UniMessage(...)` 会当场 TypeError。
    """
    qid = random.randint(1_000_000, 99_999_999)
    nick = FAKE_SELF_NICK if author == int(FAKE_SELF_ID) else NICK
    msg = {
        "time": int(time.time()) - 60,
        "message_type": "group" if group else "private",
        "message_id": qid,
        "real_id": qid,
        "sender": {"user_id": author, "nickname": nick, "card": ""},
        "message": [{"type": "text", "data": {"text": text}}],
    }
    # get_msg 的应答里字段名是 `message_id`（适配器按 Reply 模型校），这里同一份直接用
    FAKE_MSGS[qid] = msg
    return msg


def build_event(text: str, user: int, group: int, at_self: bool,
                quote: str | None = None, quote_by: int = 0, manual_at: bool = False) -> dict:
    """拼一个 OneBot 事件。at_self=True 时在开头塞真正的 at 段（群里只认这个）。

    quote 非空 = 这条用了 QQ 的「回复」：消息段里多一个 `reply:id`，事件里多一个 `reply` 对象。
    「回复」时 QQ 会自动在正文前 @ 被引用那条的作者，所以真机上这种消息**本来就带着 at**。
    manual_at=True 再补一个 at 段，排在自动那个**后面** —— 这就是人手又 @ 了一次的形状，
    闸门只吃紧跟 reply 的那一个（判据见 `gate_logic.auto_at_index`）。
    """
    segs = []
    reply = None
    if quote is not None:
        reply = quoted_message(quote, quote_by or user, group)
        segs.append({"type": "reply", "data": {"id": str(reply["message_id"])}})
    for _ in range(int(at_self) + int(manual_at)):
        segs.append({"type": "at", "data": {"qq": FAKE_SELF_ID}})
    if text:
        segs.append({"type": "text", "data": {"text": text}})
    event = {
        "time": int(time.time()),
        "self_id": int(FAKE_SELF_ID),
        "post_type": "message",
        "message_type": "group" if group else "private",
        "sub_type": "normal" if group else "friend",
        "group_id": group or None,
        "user_id": user,
        "message_id": random.randint(10_000_000, 99_999_999),
        "message": segs,
        "raw_message": "".join(
            f"[at:{s['data']['qq']}]" if s["type"] == "at" else
            f"[reply:{s['data']['id']}]" if s["type"] == "reply" else s["data"]["text"]
            for s in segs
        ),
        "font": 0,
        "sender": {"card": CARD, "nickname": NICK, "role": "member",
                   "user_id": user, "level": "1"},
    }
    if reply is not None:
        event["reply"] = reply
    return event


async def drain(ws, timeout: float) -> list[str]:
    """等这一条的回复：收到 send_* 就算回了，安静 2 秒认为说完了，整体不超过 timeout。"""
    replies: list[str] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.time()))
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            break
        try:
            api = json.loads(raw)
        except json.JSONDecodeError:
            print("← 非 JSON 帧:", str(raw)[:200])
            continue
        action = api.get("action")
        if not action:
            continue
        params = api.get("params") or {}
        echo = api.get("echo")
        if action.startswith("send"):
            content = params.get("message")
            if isinstance(content, list):
                content = "".join(
                    s.get("data", {}).get("text", f"[{s.get('type')}]") for s in content
                )
            print(f"\n机器人回复（{action}）：\n  {content!r}")
            replies.append(content)
            deadline = time.time() + 2.0  # 已经开口了，再等一小会儿看有没有第二段
        else:
            print(f"← 查询接口 {action} {json.dumps(params, ensure_ascii=False)[:80]}")
        if HANG_NAME and action in NAME_ACTIONS:
            print(f"← （本次假装）{action} 不作答 —— 机器人该在超时后自己顶上")
            continue
        await ws.send(ok(echo, answer(action, params)))
    return replies


def main() -> int:
    ap = argparse.ArgumentParser(description="从 OneBot 通道自检机器人回复链路")
    ap.add_argument("--text", default="今汐是谁")
    ap.add_argument("--user", type=int, default=999999999, help="假装发消息的人（明显是假的）")
    ap.add_argument("--group", type=int, default=0, help="非 0 则按群聊事件推")
    ap.add_argument("--at", action="store_true", help="群聊事件里加真正的 at 段（@ 机器人）")
    ap.add_argument("--manual-at", action="store_true",
                    help="在「回复」自动补的那个 @ 之后再加一个 at 段 = 自己又手动 @ 了一次（群里验引用要带这个）")
    ap.add_argument("--repeat", type=int, default=1, help="同一条消息连发几次，用来验上限")
    ap.add_argument("--no-nickname", action="store_true", help="不加昵称前缀，用来验证不该触发的不触发")
    ap.add_argument("--nickname", default=None,
                    help="假装这个人的 QQ 昵称（群聊署名就用它）—— 复现「昵称被当成话题」那类事故要设")
    ap.add_argument("--card", default=None, help="假装这个人的群名片（比昵称优先）")
    ap.add_argument("--quote", default=None,
                    help="用 QQ 的「回复」引用一条内容为 <文本> 的消息（消息段里多 reply:id，事件里多 reply 对象）")
    ap.add_argument("--quote-by", type=int, default=0,
                    help="被引用那条是谁说的：填 QQ 号；-1 = 机器人自己说的话。"
                         "真机上 QQ 会在正文前自动 @ 被引用那条的作者，所以 -1 时脚本替你补 at 段")
    ap.add_argument("--timeout", type=float, default=75.0, help="每条消息最多等多久（静默的那些要等满）")
    ap.add_argument("--hang-name", action="store_true",
                    help="假装不回 get_group_member_info / get_stranger_info。"
                         "只在**本脚本是唯一连接**时有效：真 SnowLuma 在线时那一跳走的是它的 Bot"
                         "（get_bot() 返回第一个连上的），要验超时保险请用 NAME_GUARD_TIMEOUT=0.05 重启")
    args = ap.parse_args()

    global NICK, CARD, FAKE_SELF_ID, HANG_NAME
    HANG_NAME = args.hang_name
    if args.nickname is not None:
        NICK = args.nickname
    if args.card is not None:
        CARD = args.card
    # 每次换一个假的 self_id：适配器按 self_id 记账，上一次跑完留下的连接没清干净的话，
    # 这一条会被「There's already a bot xxx, ignored」+ 403 挡在门外（同一个号连第二次必踩）。
    FAKE_SELF_ID = str(random.randint(1_000_000_000, 2_000_000_000))
    print(f"（本次用假 self_id = {FAKE_SELF_ID}）")

    # 「回复」机器人自己那条：作者要写成**本次**那个随机假 id，且真机上 QQ 会自动带上 @。
    quote_by = args.quote_by
    at = args.at
    manual_at = args.manual_at
    if args.quote is not None and quote_by == -1:
        quote_by = int(FAKE_SELF_ID)
        if not at:
            at = True
            print("（引用的是机器人自己那条 → 按真机行为自动补上 at 段。"
                  "这个 @ 闸门不当成在叫她，要她回再加 --manual-at）")
    if manual_at and args.quote is None:
        print("（--manual-at 是「回复之后再手打一个 @」，这条没有 --quote，那个 @ 就是普通的手打 @）")

    token = env_value("ONEBOT_ACCESS_TOKEN")
    if not token:
        print("[!] nonebot/.env 里没读到 ONEBOT_ACCESS_TOKEN")
        return 1
    nicknames = re.findall(r'"([^"]+)"', env_value("MUICE_NICKNAMES", "[]")) or [FAKE_SELF_NICK]

    hooked = hooked_accounts()
    print(f"SnowLuma 当前 hook：{hooked or '（没查到，SnowLuma 可能没起）'}")
    text = args.text
    if not args.no_nickname and not (at or manual_at) and not any(text.startswith(n) for n in nicknames):
        text = f"{nicknames[0]}，{text}"
    if args.group:
        where = f"群 {args.group}" + ("（@ 了）" if at else "（没 @）")
        if manual_at:
            where += "+手打 @"
    else:
        where = "私聊"
    if args.quote is not None:
        where += f"；引用「{args.quote[:20]}」作者={quote_by or args.user}"
    print(f"→ 推入事件：{text!r}  {where}  self_id={FAKE_SELF_ID}（假）  ×{args.repeat}")

    async def run() -> int:
        headers = {"Authorization": f"Bearer {token}", "X-Self-ID": FAKE_SELF_ID}
        async with websockets.connect(WS_URL, additional_headers=headers,
                                      open_timeout=10, max_size=None) as ws:
            hits = 0
            for i in range(max(1, args.repeat)):
                if args.repeat > 1:
                    print(f"\n—— 第 {i + 1}/{args.repeat} 条 ——")
                event = build_event(text, args.user, args.group, at,
                                    quote=args.quote, quote_by=quote_by,
                                    manual_at=manual_at)
                await ws.send(json.dumps(event, ensure_ascii=False))
                replies = await drain(ws, args.timeout)
                hits += 1 if replies else 0

            if args.repeat > 1:
                print(f"\n连发 {args.repeat} 条，机器人回了 {hits} 条。"
                      f"想看拦下的证据：Select-String '\\[gate\\]' logs\\nonebot.out.log")
                return 0 if 0 < hits < args.repeat else 1
            if hits:
                print("\n[OK] 全链路通了")
                return 0
            print("\n[!!] 没收到回复。逐个排：\n"
                  "     · 被闸门拦了 —— 群里必须 @ 机器人，且没到当天的上限；"
                  "看 logs/nonebot.out.log 里的 [gate] 拦下 行\n"
                  "     · 没触发 —— 私聊直接可触发，群聊要么 @ 要么昵称开头\n"
                  "     · 模型不通 —— 看 logs/nonebot.err.log，或先跑 tools/自检AxonHub.ps1\n"
                  "     · 超时 —— 加大 --timeout")
            return 1

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
