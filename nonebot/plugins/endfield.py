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
from pathlib import Path
from typing import Any, Mapping

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.rule import Rule

from muicebot.plugin import PluginMetadata

try:
    from endfield import client as ec
    from endfield import config as ecfg
    from endfield import logic
    from endfield import store as estore
except ImportError:  # 不是从 nonebot\ 启动
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from endfield import client as ec
    from endfield import config as ecfg
    from endfield import logic
    from endfield import store as estore

__plugin_meta__ = PluginMetadata(
    name="endfield",
    description="终末地查询与多账号绑定（一期纯文本）",
    usage="私聊：授权登陆/绑定列表/切换绑定 N/删除绑定 N；群聊 @ 我：便签/理智/签到",
)

driver = get_driver()
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "endfield"
CONFIG_PATH = BASE_DIR / "data" / "endfield.json"
SECRETS_PATH = BASE_DIR / "data" / "endfield_secrets.json"
BINDINGS_PATH = DATA_DIR / "bindings.json"

_gate = logic.UserGate()
_pending_consent: set[str] = set()
_pending_auth: dict[str, str] = {}

PRIVATE_ONLY = set(logic.PRIVATE_COMMANDS)


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
        return True
    command = logic.parse_command(text)
    if command is None:
        return False
    if _is_group(event) and not event.is_tome():
        return False
    return True


matcher = on_message(rule=Rule(_rule), priority=3, block=True)


def _config() -> ecfg.EndfieldConfig:
    return ecfg.load_config(CONFIG_PATH)


def _api_key() -> str:
    env = os.environ.get("ENDFIELD_API_KEY") or getattr(
        driver.config, "endfield_api_key", None
    )
    if env:
        return str(env).strip()
    try:
        text = SECRETS_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("api_key") or "").strip() if isinstance(data, dict) else ""


def _client(event: MessageEvent, qq: str) -> ec.EndfieldClient:
    return ec.EndfieldClient(
        _api_key(),
        base_url=os.environ.get("ENDFIELD_BASE_URL") or ec.BASE_URL,
        bot_qq=str(getattr(event, "self_id", "") or ""),
        user_qq=str(qq),
    )


def _store() -> estore.BindingStore:
    return estore.BindingStore(BINDINGS_PATH)


def _binding_from_payload(data: Mapping[str, Any]) -> dict[str, Any] | None:
    """从授权状态/绑定响应里尽量取 role_id + token；字段未知时返回 None。"""
    nested = data.get("binding") if isinstance(data.get("binding"), Mapping) else {}
    candidates: list[Mapping[str, Any]] = [nested, data]
    for item in candidates:
        role_id = item.get("role_id") or item.get("roleId")
        token = (
            item.get("framework_token")
            or item.get("frameworkToken")
            or item.get("token")
        )
        if role_id and token:
            return {
                "binding_id": str(item.get("binding_id") or item.get("id") or ""),
                "role_id": str(role_id),
                "server_id": int(item.get("server_id") or item.get("serverId") or 1),
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


async def _send(event: MessageEvent, text: str) -> None:
    await matcher.send(text)


async def _handle_consent(event: MessageEvent, qq: str) -> bool:
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
    store = _store()
    user = await store.get_user(qq)
    if not user or not int(user.get("consent_version") or 0):
        _pending_consent.add(qq)
        await _send(event, logic.consent_text())
        return

    request_id = _pending_auth.get(qq)
    client = _client(event, qq)
    try:
        if not request_id:
            data = await client.create_authorization_request()
            request_id = str(
                data.get("request_id") or data.get("id") or data.get("requestId") or ""
            )
            auth_url = str(data.get("auth_url") or data.get("authUrl") or "")
            if not request_id or not auth_url:
                await _send(event, "创建授权请求失败，请稍后再试。")
                return
            _pending_auth[qq] = request_id
            await _send(
                event,
                f"请打开授权链接完成授权：\n{auth_url}\n完成后再次发送「授权登陆」。",
            )
            return

        status = await client.get_authorization_status(request_id)
        if not _status_approved(status):
            await _send(event, "授权还未完成，请先打开链接授权；完成后再次发送「授权登陆」。")
            return
        binding = _binding_from_payload(status)
        if not binding:
            await _send(event, "授权已完成，但未拿到角色信息；请稍后重试或联系主人。")
            return
        await store.add_binding(
            qq, binding, consent_version=int(user.get("consent_version") or 1)
        )
        _pending_auth.pop(qq, None)
        await _send(event, f"绑定成功：{binding['role_name']}")
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
    if command.name == "日历":
        client = _client(event, qq)
        try:
            data = await client.get_wiki_activities()
            await _send(event, logic.format_calendar(data))
        except ec.EndfieldConfigError:
            await _send(event, logic.error_text("config"))
        except ec.EndfieldRateLimitError:
            await _send(event, logic.error_text("rate_limit"))
        except ec.EndfieldTransientError:
            await _send(event, logic.error_text("timeout"))
        except ec.EndfieldProtocolError:
            await _send(event, "日历查询失败，请稍后再试。")
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
        elif command.name == "签到":
            data = await client.post_attendance(token, role_id, server_id)
            text = logic.format_attendance(data)
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
        else:
            await _send(event, "暂不支持该查询。")
            return
        await _send(event, text)
    except ec.EndfieldAuthError as exc:
        await store.mark_invalid(qq, role_id, str(exc.code or exc.status or "auth"))
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
        await store.mark_invalid(qq, role_id, str(exc.code or exc.status or "auth"))
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
        try:
            ok = await store.remove_binding(qq, command.arg)
        except estore.BindingNotFound:
            ok = False
        if ok:
            await _send(event, "已删除该绑定（本地）；如需网关侧清理请稍后重试。")
        else:
            await _send(event, "没有这个绑定；发送「绑定列表」查看序号。")


@matcher.handle()
async def _handle_endfield(event: MessageEvent) -> None:
    qq = str(event.get_user_id())
    if await _handle_consent(event, qq):
        return

    command = logic.parse_command(_text(event))
    if command is None:
        return

    if _is_group(event) and command.name in PRIVATE_ONLY:
        await _send(event, "绑定/同步/账号管理请私聊我。")
        return

    allowed, reason = _gate.check(
        qq,
        logic.action_of(command),
        is_superuser=qq in _superusers(),
    )
    if not allowed:
        await _send(event, reason)
        return

    if command.name == "授权登陆":
        await _handle_auth(event, qq)
    elif command.name == "扫码绑定":
        await _send(event, "扫码绑定将在二期实现；请先使用「授权登陆」。")
    elif command.name in logic.SYNC_COMMANDS:
        await _handle_sync(event, qq, command)
    elif command.name in logic.QUERY_COMMANDS:
        await _handle_query(event, qq, command)
    else:
        await _handle_binding(event, qq, command)
