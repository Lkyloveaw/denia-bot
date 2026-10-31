"""Endfield 一期命令接线（MIT）。

8 条命令：授权登陆、扫码绑定（二期）、便签、理智、签到、绑定列表、
切换绑定 N、删除绑定 N。
触发：群聊必须 @ 机器人；查询主体永远是发送者；绑定类仅私聊。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Mapping

import nonebot
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.rule import Rule

from muicebot.plugin import PluginMetadata

try:
    from endfield import cards
    from endfield import client as ec
    from endfield import config as ecfg
    from endfield import logic
    from endfield import render as er
    from endfield import scheduler as esched
    from endfield import store as estore
except ImportError:  # 不是从 nonebot\ 启动
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from endfield import cards
    from endfield import client as ec
    from endfield import config as ecfg
    from endfield import logic
    from endfield import render as er
    from endfield import scheduler as esched
    from endfield import store as estore

__plugin_meta__ = PluginMetadata(
    name="endfield",
    description="终末地账号查询、图卡与订阅提醒",
    usage="私聊授权登陆；查询可私聊或群聊 @ 我；help 查看终末地命令",
)

driver = get_driver()
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "endfield"
CONFIG_PATH = BASE_DIR / "data" / "endfield.json"
SECRETS_PATH = BASE_DIR / "data" / "endfield_secrets.json"
BINDINGS_PATH = DATA_DIR / "bindings.json"
SUBSCRIPTIONS_PATH = DATA_DIR / "subscriptions.json"
HEARTBEAT_PATH = DATA_DIR / "heartbeat.json"
AUDIT_PATH = DATA_DIR / "audit.jsonl"

_gate = logic.UserGate()
_bind_gate = logic.UserGate()
_pending_consent: set[str] = set()
_pending_auth: dict[str, str] = {}
_auth_deadlines: dict[str, float] = {}
_auth_locks: dict[str, asyncio.Lock] = {}
_auth_recall_tasks: set[asyncio.Task] = set()
_binding_store: estore.BindingStore | None = None
_subscription_store: estore.SubscriptionStore | None = None

PRIVATE_ONLY = set(logic.PRIVATE_COMMANDS)
CARD_COMMANDS = {
    "抽卡记录",
    "抽卡分析",
    "干员列表",
    "干员面板",
    "危机合约",
    "日历",
    "成就列表",
    "帝江号建设",
    "地区建设",
    "全服统计",
    "公告",
    "公告最新",
}

import gate_logic

gate_logic.register_help_rows([
    ("授权登陆 / 扫码绑定", "私聊网页授权；扫码绑定暂未开放"),
    ("绑定列表 / 切换绑定 N / 删除绑定 N", "私聊管理自己的终末地绑定"),
    ("便签 / 理智 / 签到", "终末地账号状态与逐账号签到；群聊需 @"),
    ("抽卡记录 [页码] / 抽卡分析", "查询终末地抽卡记录与统计"),
    ("干员列表 [页码] / 干员面板 名称", "查询终末地干员面板"),
    ("同步抽卡 / 同步面板", "私聊同步自己的终末地数据"),
    ("危机合约 [名称] / 成就列表", "查询终末地战斗与成就数据"),
    ("帝江号建设 / 地区建设 / 全服统计", "查询终末地建设与全服数据"),
    ("日历 / 公告 / 公告最新", "查看终末地活动和公告"),
    ("订阅理智 / 订阅调度券 / 订阅签到", "私聊开启个人提醒；命令前加「取消」关闭"),
    ("订阅列表 / 订阅公告 / 取消订阅公告", "查看订阅；群公告订阅仅主人可操作"),
])


def _superusers() -> set[str]:
    return {str(u) for u in getattr(driver.config, "superusers", None) or set()}


def _is_private(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent)


def _is_group(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageEvent)


def _text(event: MessageEvent) -> str:
    return (event.get_plaintext() or "").strip()


def _is_pending_consent(qq: str) -> bool:
    return qq in _pending_consent


def _rule(event: MessageEvent) -> bool:
    text = _text(event)
    if text in {"同意", "取消"} and _is_pending_consent(str(event.get_user_id())):
        return _is_private(event)
    command = logic.parse_command(text)
    if command is None:
        return False
    if _is_group(event):
        message = getattr(event, "original_message", None)
        if message is None:
            message = event.get_message()
        segments = [(part.type, str(part.data.get("qq", ""))) for part in message]
        sender = getattr(getattr(event, "reply", None), "sender", None)
        author = getattr(sender, "user_id", None)
        count_auto = str(getattr(driver.config, "quote_at_counts", True)).strip().lower() not in (
            "0", "false", "off", "no", "关"
        )
        if not gate_logic.mentions_self(
            segments, event.self_id, ignore_auto_at=not count_auto, reply_author=author
        ):
            return False
    return True


matcher = on_message(rule=Rule(_rule), priority=3, block=True)


def _config() -> ecfg.EndfieldConfig:
    return ecfg.load_config(CONFIG_PATH)


def _api_key() -> str:
    try:
        text = SECRETS_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        data = {}
    panel = str(data.get("api_key") or "").strip() if isinstance(data, dict) else ""
    return panel or str(os.environ.get("ENDFIELD_API_KEY") or getattr(
        driver.config, "endfield_api_key", "") or "").strip()


def _client(event: MessageEvent, qq: str) -> ec.EndfieldClient:
    return ec.EndfieldClient(
        _api_key(),
        base_url=os.environ.get("ENDFIELD_BASE_URL") or ec.BASE_URL,
        bot_qq=str(getattr(event, "self_id", "") or ""),
        user_qq=str(qq),
    )


def _store() -> estore.BindingStore:
    global _binding_store
    if _binding_store is None or _binding_store.path != BINDINGS_PATH:
        _binding_store = estore.BindingStore(BINDINGS_PATH)
    return _binding_store


def _subscriptions() -> estore.SubscriptionStore:
    global _subscription_store
    if _subscription_store is None or _subscription_store.path != SUBSCRIPTIONS_PATH:
        _subscription_store = estore.SubscriptionStore(SUBSCRIPTIONS_PATH)
    return _subscription_store


class OneBotPushSink:
    async def private(self, qq: str, text: str) -> bool:
        if os.environ.get("ENDFIELD_PUSH_DRY_RUN", "").lower() in {"1", "true"}:
            return False
        bot = _first_bot()
        if bot is None:
            return False
        try:
            await bot.send_private_msg(user_id=int(qq), message=text)
            return True
        except Exception:
            return False

    async def group(self, group_id: str, text: str) -> bool:
        if os.environ.get("ENDFIELD_PUSH_DRY_RUN", "").lower() in {"1", "true"}:
            return False
        bot = _first_bot()
        if bot is None:
            return False
        try:
            await bot.send_group_msg(group_id=int(group_id), message=text)
            return True
        except Exception:
            return False


def _first_bot():
    bots = nonebot.get_bots()
    return next(iter(bots.values()), None)


def _scheduler_client(qq: str) -> ec.EndfieldClient | None:
    key = _api_key()
    if not key:
        return None
    return ec.EndfieldClient(
        key,
        base_url=os.environ.get("ENDFIELD_BASE_URL") or ec.BASE_URL,
        bot_qq=str(getattr(_first_bot(), "self_id", "") or ""),
        user_qq=str(qq),
    )


_scheduler_task: asyncio.Task | None = None


@driver.on_startup
async def _start_endfield_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    scheduler = esched.EndfieldScheduler(
        bindings=_store(),
        subscriptions=_subscriptions(),
        client_factory=_scheduler_client,
        sink=OneBotPushSink(),
        config_provider=_config,
        heartbeat_path=HEARTBEAT_PATH,
        audit_path=AUDIT_PATH,
    )
    _scheduler_task = asyncio.create_task(scheduler.run_forever(60))


@driver.on_shutdown
async def _stop_endfield_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await _scheduler_task
        _scheduler_task = None
    for task in list(_auth_recall_tasks):
        task.cancel()
    await asyncio.gather(*_auth_recall_tasks, return_exceptions=True)
    _auth_recall_tasks.clear()


def _binding_from_payload(data: Mapping[str, Any]) -> dict[str, Any] | None:
    """从授权状态/绑定响应里尽量取 role_id + token；字段未知时返回 None。"""
    nested = data.get("binding") if isinstance(data.get("binding"), Mapping) else {}
    candidates: list[Mapping[str, Any]] = [nested, data]
    roles = data.get("available_roles")
    if isinstance(roles, list):
        roles = [role for role in roles if isinstance(role, Mapping)]
        if roles:
            role = next((r for r in roles if r.get("is_default")), roles[0])
            candidates.insert(0, {**data, **role})
    for item in candidates:
        role_id = item.get("role_id") or item.get("roleId")
        token = (
            item.get("framework_token")
            or item.get("frameworkToken")
            or item.get("token")
        )
        if role_id and token:
            try:
                server_id = int(item.get("server_id") or item.get("serverId") or 1)
            except (TypeError, ValueError):
                return None
            return {
                "binding_id": str(item.get("binding_id") or item.get("id") or ""),
                "role_id": str(role_id),
                "server_id": server_id,
                "role_name": str(
                    item.get("role_name")
                    or item.get("nickname")
                    or item.get("roleName")
                    or role_id
                ),
                "framework_token": str(token),
                "provider": str(item.get("provider") or "skland"),
            }
    return None


def _status_approved(data: Mapping[str, Any]) -> bool:
    status = str(data.get("status") or data.get("state") or "").lower()
    if status in {"approved", "authorized", "success", "completed", "ok"}:
        return True
    return bool(data.get("approved") is True or data.get("framework_token"))


async def _send(event: MessageEvent, text: str) -> Any:
    return await matcher.send(text)


async def _expire_auth_link(event: MessageEvent, message_id: Any, qq: str, request_id: str) -> None:
    await asyncio.sleep(100)
    if message_id is not None:
        with suppress(Exception):
            bot = nonebot.get_bot(str(event.self_id))
            await bot.delete_msg(message_id=int(message_id))
    await asyncio.sleep(200)
    if _pending_auth.get(qq) == request_id:
        _pending_auth.pop(qq, None)
        _auth_deadlines.pop(qq, None)


async def _send_card(event: MessageEvent, title: str, text: str) -> None:
    """图卡优先；渲染器不可用/超限时回退文本。"""
    config = _config()
    pages = cards.build_pages(title, text.splitlines())
    client = er.RenderClient(
        config.render_url, timeout=max(5, config.render_timeout_seconds + 5)
    )
    try:
        for page in pages:
            png = await client.render_html(
                page.html, width=900, height=page.height,
                timeout_ms=config.render_timeout_seconds * 1000,
            )
            await matcher.send(MessageSegment.image(png))
    except er.RenderError:
        await _send(event, text)
    finally:
        await client.close()


async def _handle_consent(event: MessageEvent, qq: str) -> bool:
    if not _is_private(event):
        return False
    text = _text(event)
    if text not in {"同意", "取消"} or not _is_pending_consent(qq):
        return False
    if text == "取消":
        _pending_consent.discard(qq)
        await _send(event, "已取消绑定。")
        return True
    await _store().set_consent(qq)
    _pending_consent.discard(qq)
    await _send(event, "已记录同意。请发送「授权登陆」继续。")
    return True


async def _handle_auth(event: MessageEvent, qq: str) -> None:
    async with _auth_locks.setdefault(qq, asyncio.Lock()):
        await _authorize(event, qq)


async def _authorize(event: MessageEvent, qq: str) -> None:
    store = _store()
    user = await store.get_user(qq)
    if not user or not int(user.get("consent_version") or 0):
        _pending_consent.add(qq)
        await _send(event, logic.consent_text())
        return

    if time.monotonic() >= _auth_deadlines.get(qq, float("inf")):
        _pending_auth.pop(qq, None)
        _auth_deadlines.pop(qq, None)
    request_id = _pending_auth.get(qq)
    client = _client(event, qq)
    try:
        if not request_id:
            allowed, reason = _bind_gate.check(qq, "bind", is_superuser=qq in _superusers())
            if not allowed:
                await _send(event, reason)
                return
            data = await client.create_authorization_request()
            request_id = str(
                data.get("request_id") or data.get("id") or data.get("requestId") or ""
            )
            auth_url = str(data.get("auth_url") or data.get("authUrl") or "")
            if not request_id or not auth_url:
                await _send(event, "创建授权请求失败，请稍后再试。")
                return
            _pending_auth[qq] = request_id
            _auth_deadlines[qq] = time.monotonic() + 300
            message = await _send(
                event,
                f"请打开授权链接完成授权：\n{auth_url}\n链接将在 100 秒后撤回；完成后再次发送「授权登陆」。",
            )
            message_id = message.get("message_id") if isinstance(message, Mapping) else None
            task = asyncio.create_task(_expire_auth_link(event, message_id, qq, request_id))
            _auth_recall_tasks.add(task)
            task.add_done_callback(_auth_recall_tasks.discard)
            return

        status = await client.get_authorization_status(request_id)
        if str(status.get("status") or "").lower() in {"expired", "rejected", "denied", "cancelled", "canceled", "failed"}:
            _pending_auth.pop(qq, None)
            _auth_deadlines.pop(qq, None)
            await _send(event, "授权已过期或取消，请重新发送「授权登陆」。")
            return
        if not _status_approved(status):
            await _send(event, "授权还未完成，请先打开链接授权；完成后再次发送「授权登陆」。")
            return
        binding = _binding_from_payload(status)
        if not binding:
            await _send(event, "授权已完成，但未拿到角色信息；请稍后重试或联系主人。")
            return
        existing = await store.list_bindings(qq)
        if len(existing) >= estore.MAX_BINDINGS_PER_USER and not any(
            (r["role_id"], r["server_id"]) == (binding["role_id"], binding["server_id"])
            for r in existing
        ):
            _pending_auth.pop(qq, None)
            await _send(event, "最多绑定 3 个角色，请先删除一个绑定。")
            return
        created = await client.create_binding(
            binding["framework_token"], qq, is_primary=not existing,
            role_id=binding["role_id"], server_id=binding["server_id"],
            nickname=binding["role_name"],
        )
        binding_id = created.get("id") or created.get("binding_id")
        if not binding_id:
            await _send(event, "网关未返回绑定 ID，请稍后再试。")
            return
        binding["binding_id"] = str(binding_id)
        await store.add_binding(
            qq, binding, consent_version=int(user.get("consent_version") or 1)
        )
        _pending_auth.pop(qq, None)
        _auth_deadlines.pop(qq, None)
        cleanup_notice = ""
        previous = next((row for row in existing
                         if (row["role_id"], row["server_id"]) == (binding["role_id"], binding["server_id"])), None)
        old_id = str(previous.get("binding_id") or "") if previous else ""
        if old_id and old_id != binding["binding_id"]:
            try:
                await client.delete_binding(old_id, qq)
            except ec.EndfieldError:
                cleanup_notice = "\n旧网关绑定未能清理，请到协议终端授权管理检查并移除旧绑定。"
        await _send(event, f"绑定成功：{binding['role_name']}{cleanup_notice}")
    except ec.EndfieldConfigError:
        await _send(event, logic.error_text("config"))
    except ec.EndfieldAuthError:
        _pending_auth.pop(qq, None)
        await _send(event, logic.error_text("invalid_binding"))
    except ec.EndfieldRateLimitError:
        await _send(event, logic.error_text("rate_limit"))
    except ec.EndfieldTransientError:
        await _send(event, logic.error_text("timeout"))
    except ec.EndfieldProtocolError:
        await _send(event, "授权接口返回异常，请稍后再试。")
    except estore.StoreError as exc:
        await _send(event, f"绑定失败：{exc}")
    finally:
        await client.close()


def _find_char(rows: Any, name: str) -> dict[str, Any] | None:
    wanted = str(name or "").strip().lower()
    if not wanted or not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        candidates = [
            row.get("name_cn"),
            row.get("name"),
            row.get("alias"),
            row.get("char_name"),
        ]
        if any(str(item or "").strip().lower() == wanted for item in candidates):
            return dict(row)
    return None


def _pick_contract(detail: Mapping[str, Any], desired: str = "") -> dict[str, Any] | None:
    payload = (
        detail.get("crisisContract")
        or detail.get("crisis_contract")
        or detail.get("contracts")
        or []
    )
    if isinstance(payload, Mapping):
        rows = [dict(payload)]
    elif isinstance(payload, list):
        rows = [dict(item) for item in payload if isinstance(item, Mapping)]
    else:
        rows = []
    if desired:
        for row in rows:
            if desired in {
                str(row.get("name") or ""),
                str(row.get("id") or ""),
                str(row.get("contract_id") or ""),
                str(row.get("contractId") or ""),
            }:
                return row
    return rows[0] if rows else None


async def _handle_query(event: MessageEvent, qq: str, command: logic.Command) -> None:
    if command.name == "签到":
        await _manual_signin(event, qq)
        return
    if command.name in {"日历", "全服统计", "公告", "公告最新"}:
        client = _client(event, qq)
        try:
            if command.name == "日历":
                data = await client.get_wiki_activities()
                text = logic.format_calendar(data)
            elif command.name == "全服统计":
                data = await client.get_global_stats()
                text = logic.format_global_stats(data)
            elif command.name == "公告最新":
                data = await client.get_announcement_latest()
                text = logic.format_announcement_latest(data)
            else:
                data = await client.get_announcements(page=1, page_size=5)
                text = logic.format_announcement_list(data)
            await _send_card(event, command.name, text)
        except ec.EndfieldConfigError:
            await _send(event, logic.error_text("config"))
        except ec.EndfieldRateLimitError:
            await _send(event, logic.error_text("rate_limit"))
        except ec.EndfieldTransientError:
            await _send(event, logic.error_text("timeout"))
        except ec.EndfieldProtocolError:
            await _send(event, f"{command.name}查询失败，请稍后再试。")
        finally:
            await client.close()
        return

    store = _store()
    binding = await store.get_primary(qq)
    if not binding:
        await _send(event, logic.error_text("no_binding"))
        return
    if binding.get("status") != "valid":
        await _send(event, logic.error_text("invalid_binding"))
        return
    client = _client(event, qq)
    token = str(binding.get("framework_token") or "")
    role_id = str(binding.get("role_id") or "")
    server_id = int(binding.get("server_id") or 1)
    try:
        if command.name == "便签":
            data = await client.get_note(token, role_id, server_id)
            text = logic.format_note(data)
        elif command.name == "理智":
            data = await client.get_stamina(token, role_id, server_id)
            text = logic.format_stamina(data)
        elif command.name == "抽卡记录":
            page = int(command.arg or 1)
            data = await client.get_gacha_records(token, page=page, limit=20)
            text = logic.format_gacha_records(
                data, page=int(data.get("page") or page)
            )
        elif command.name == "抽卡分析":
            data = await client.get_gacha_stats(token)
            text = logic.format_gacha_stats(data)
        elif command.name == "干员列表":
            page = int(command.arg or 1)
            data = await client.get_panel_chars(token, page=page, page_size=20)
            text = logic.format_operator_list(
                data, page=int(data.get("page") or page)
            )
        elif command.name == "干员面板":
            rows = await client.get_panel_chars_all(token)
            row = _find_char(rows, command.arg)
            if row is None:
                await _send(event, f"没有找到干员「{command.arg}」；可先发送「同步面板」。")
                return
            template_id = (
                row.get("template_id")
                or row.get("templateId")
                or row.get("char_id")
                or row.get("charId")
            )
            if not template_id:
                await _send(event, "该干员缺少模板 ID，暂不能查看面板。")
                return
            data = await client.get_panel_char(token, str(template_id))
            text = logic.format_operator_panel(data, name=command.arg)
        elif command.name == "危机合约":
            detail = await client.get_card_detail(token, role_id, server_id)
            contract = _pick_contract(detail, command.arg)
            if contract is None:
                await _send(event, "当前没有可用的危机合约数据。")
                return
            contract_id = (
                contract.get("contract_id")
                or contract.get("contractId")
                or contract.get("id")
            )
            if not contract_id:
                await _send(event, "危机合约缺少 ID，暂不能查询。")
                return
            data = await client.get_crisis_contract(
                token, str(contract_id), role_id=role_id, server_id=server_id
            )
            text = logic.format_crisis_contract(data, contract=contract)
        elif command.name == "成就列表":
            data = await client.get_achieve(token, role_id, server_id)
            text = logic.format_achievements(data)
        elif command.name == "帝江号建设":
            data = await client.get_spaceship(token, role_id, server_id)
            text = logic.format_spaceship(data)
        elif command.name == "地区建设":
            data = await client.get_domain(token, role_id, server_id)
            text = logic.format_domain(data)
        else:
            await _send(event, "暂不支持该查询。")
            return
        if command.name in CARD_COMMANDS:
            await _send_card(event, command.name, text)
        else:
            await _send(event, text)
    except ec.EndfieldAuthError as exc:
        await store.mark_invalid(qq, role_id, str(exc.code or exc.status or "auth"), server_id=server_id)
        await _send(event, logic.error_text("invalid_binding"))
    except ec.EndfieldConfigError:
        await _send(event, logic.error_text("config"))
    except ec.EndfieldRateLimitError:
        await _send(event, logic.error_text("rate_limit"))
    except ec.EndfieldTransientError:
        await _send(event, logic.error_text("timeout"))
    except ec.EndfieldProtocolError:
        await _send(event, "查询失败，请稍后再试。")
    finally:
        await client.close()


async def _manual_signin(event: MessageEvent, qq: str) -> None:
    store = _store()
    bindings = await store.list_bindings(qq)
    if not bindings:
        await _send(event, logic.error_text("no_binding"))
        return
    client = _client(event, qq)
    lines = ["终末地签到："]
    try:
        for binding in bindings:
            name = str(binding.get("role_name") or "未命名角色")
            if binding.get("status") != "valid":
                lines.append(f"{name}：绑定已失效，请重新授权。")
                continue
            try:
                data = await client.post_attendance(
                    binding["framework_token"], binding["role_id"], binding["server_id"])
                lines.append(f"{name}：{logic.format_attendance(data)}")
            except ec.EndfieldAuthError as exc:
                await store.mark_invalid(qq, binding["role_id"], str(exc.status or "auth"), server_id=binding["server_id"])
                lines.append(f"{name}：绑定已失效，请重新授权。")
            except ec.EndfieldRateLimitError:
                lines.append("网关限流，剩余账号已暂停，请稍后再试。")
                break
            except ec.EndfieldConfigError:
                lines.append(logic.error_text("config"))
                break
            except ec.EndfieldError:
                lines.append(f"{name}：签到失败，请稍后再试。")
    finally:
        await client.close()
    await _send(event, "\n".join(lines))


async def _poll_sync(fn: Any, token: str, *, attempts: int = 15) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for _ in range(attempts):
        data = await fn(token)
        status = str(data.get("status") or "").lower()
        if status in {"completed", "success", "done", "failed", "error", "conflict"}:
            return data
        await asyncio.sleep(2.0)
    return data


async def _handle_sync(event: MessageEvent, qq: str, command: logic.Command) -> None:
    store = _store()
    binding = await store.get_primary(qq)
    if not binding:
        await _send(event, logic.error_text("no_binding"))
        return
    if binding.get("status") != "valid":
        await _send(event, logic.error_text("invalid_binding"))
        return
    client = _client(event, qq)
    token = str(binding.get("framework_token") or "")
    role_id = str(binding.get("role_id") or "")
    try:
        if command.name == "同步抽卡":
            started = await client.post_gacha_fetch(token, role_id)
            status = str(started.get("status") or "").lower()
            data = (
                started
                if status in {"completed", "success", "done", "failed", "error", "conflict"}
                else await _poll_sync(client.get_gacha_sync_status, token)
            )
        else:
            started = await client.sync_panel(token)
            status = str(started.get("status") or "").lower()
            data = (
                started
                if status in {"completed", "success", "done", "failed", "error", "conflict"}
                else await _poll_sync(client.get_panel_sync_status, token)
            )
        await _send(event, logic.format_sync_status(data))
    except ec.EndfieldAuthError as exc:
        await store.mark_invalid(qq, role_id, str(exc.code or exc.status or "auth"), server_id=int(binding.get("server_id") or 1))
        await _send(event, logic.error_text("invalid_binding"))
    except ec.EndfieldConfigError:
        await _send(event, logic.error_text("config"))
    except ec.EndfieldRateLimitError:
        await _send(event, logic.error_text("rate_limit"))
    except ec.EndfieldTransientError:
        await _send(event, logic.error_text("timeout"))
    except ec.EndfieldProtocolError:
        await _send(event, "同步失败，请稍后再试。")
    finally:
        await client.close()


async def _handle_binding(event: MessageEvent, qq: str, command: logic.Command) -> None:
    store = _store()
    if command.name == "绑定列表":
        await _send(event, logic.format_binding_list(await store.public_bindings(qq)))
    elif command.name == "切换绑定":
        try:
            item = await store.set_primary(qq, command.arg)
            await _send(event, f"已切换到：{item.get('role_name') or command.arg}")
        except estore.BindingNotFound:
            await _send(event, "没有这个绑定；发送「绑定列表」查看序号。")
    elif command.name == "删除绑定":
        rows = await store.list_bindings(qq)
        index = int(command.arg) - 1
        deleted = rows[index] if 0 <= index < len(rows) else None
        try:
            ok = await store.remove_binding(qq, command.arg)
        except estore.BindingNotFound:
            ok = False
        if ok:
            message = f"已删除绑定：{deleted.get('role_name') or command.arg}（本地）。"
            if deleted and deleted.get("binding_id"):
                client = _client(event, qq)
                try:
                    await client.delete_binding(str(deleted["binding_id"]), qq)
                    message += "网关绑定已清理。"
                except ec.EndfieldError:
                    message += "网关清理未成功，请在协议终端授权管理中撤销该绑定。"
                finally:
                    await client.close()
            await _send(event, message)
        else:
            await _send(event, "没有这个绑定；发送「绑定列表」查看序号。")


KIND_BY_COMMAND = {
    "订阅理智": "sanity",
    "取消订阅理智": "sanity",
    "订阅调度券": "tickets",
    "取消订阅调度券": "tickets",
    "订阅签到": "signin",
    "取消订阅签到": "signin",
}


async def _handle_subscription(
    event: MessageEvent, qq: str, command: logic.Command
) -> None:
    store = _store()
    subscriptions = _subscriptions()
    if command.name in logic.SUB_GROUP_COMMANDS:
        if not _is_group(event):
            await _send(event, "公告订阅请在群聊中使用。")
            return
        if qq not in _superusers():
            await _send(event, "只有主人可以订阅/取消群公告。")
            return
        group_id = str(getattr(event, "group_id", "") or "")
        if command.name == "订阅公告":
            since_ts = 0
            last_item_id = ""
            client = _client(event, qq)
            try:
                latest = await client.get_announcement_latest()
                since_ts = int(latest.get("published_at_ts") or 0)
                last_item_id = str(latest.get("item_id") or "")
            except ec.EndfieldError:
                await _send(event, "读取最新公告失败，请稍后再试。")
                return
            finally:
                await client.close()
            await subscriptions.add_announcement(
                group_id,
                qq,
                since_ts=since_ts,
                last_item_id=last_item_id,
            )
            await _send(event, "已订阅本群公告推送。")
        else:
            await subscriptions.remove_announcement(group_id)
            await _send(event, "已取消本群公告推送。")
        return

    if command.name == "订阅列表":
        personal = await subscriptions.list_personal()
        announcements = (
            await subscriptions.list_announcements()
            if qq in _superusers()
            else None
        )
        await _send(
            event,
            logic.format_subscription_list(
                personal.get("sanity", {}).get(qq),
                personal.get("tickets", {}).get(qq),
                personal.get("signin", {}).get(qq),
                announcements=announcements,
            ),
        )
        return

    kind = KIND_BY_COMMAND.get(command.name)
    if kind is None:
        return
    if command.name.startswith("订阅"):
        binding = await store.get_primary(qq)
        if not binding:
            await _send(event, logic.error_text("no_binding"))
            return
        await subscriptions.set_personal(kind, qq)
        await _send(event, "已订阅。")
    else:
        await subscriptions.remove_personal(kind, qq)
        await _send(event, "已取消订阅。")


@matcher.handle()
async def _handle_endfield(event: MessageEvent) -> None:
    qq = str(event.get_user_id())
    config = _config()
    if not config.enabled:
        await _send(event, "终末地功能已在管理面板暂停。")
        return
    if await _handle_consent(event, qq):
        return

    command = logic.parse_command(_text(event))
    if command is None:
        return
    if (command.name in logic.QUERY_COMMANDS or command.name in logic.SYNC_COMMANDS) and not config.enable_queries:
        await _send(event, "终末地查询已暂停。")
        return
    if command.name == "签到" and not config.enable_signin:
        await _send(event, "终末地签到已暂停。")
        return
    if command.name.startswith("订阅") and command.name != "订阅列表" and (
        not config.enable_subscriptions or (command.name == "订阅签到" and not config.enable_signin)
    ):
        await _send(event, "终末地订阅已暂停，仍可取消已有订阅。")
        return

    if _is_group(event) and command.name in PRIVATE_ONLY:
        await _send(event, "绑定/同步/账号管理/个人订阅请私聊我。")
        return
    if not _is_group(event) and command.name in logic.SUB_GROUP_COMMANDS:
        await _send(event, "公告订阅请在群聊中使用。")
        return

    allowed, reason = _gate.check(
        qq,
        "auth_poll" if command.name == "授权登陆" else logic.action_of(command),
        is_superuser=qq in _superusers(),
    )
    if not allowed:
        await _send(event, reason)
        return

    if command.name == "授权登陆":
        await _handle_auth(event, qq)
    elif command.name == "扫码绑定":
        await _send(event, "扫码绑定将在二期实现；请先使用「授权登陆」。")
    elif command.name in logic.SUBSCRIPTION_COMMANDS:
        await _handle_subscription(event, qq, command)
    elif command.name in logic.SYNC_COMMANDS:
        await _handle_sync(event, qq, command)
    elif command.name in logic.QUERY_COMMANDS:
        await _handle_query(event, qq, command)
    else:
        await _handle_binding(event, qq, command)
