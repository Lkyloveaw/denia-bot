"""Endfield 一期接线规则用例（不连 QQ/不发网络）。"""

import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parents[2])
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._bootstrap import ensure_nonebot  # noqa: E402

ensure_nonebot()

import plugins.endfield as endfield  # noqa: E402
from nonebot.adapters.onebot.v11 import (  # noqa: E402
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)

SELF = "3892894069"
GROUP = 741096253


def _msg(*segments) -> Message:
    message = Message()
    for segment in segments:
        message += segment
    return message


def group_event(
    text: str, *, at_self: bool = False, at_other: bool = False, user: str = "123456"
) -> GroupMessageEvent:
    message = _msg(
        *([MessageSegment.at(SELF)] if at_self else []),
        *([MessageSegment.at("999999")] if at_other else []),
        *([MessageSegment.text(text)] if text else []),
    )
    return GroupMessageEvent(
        time=1700000000,
        self_id=int(SELF),
        post_type="message",
        sub_type="normal",
        user_id=int(user),
        group_id=GROUP,
        message_type="group",
        message_id=1,
        message=message,
        raw_message=str(message),
        font=0,
        sender={"card": "", "nickname": "t", "role": "member", "title": ""},
        to_me=at_self,
    )


def private_event(text: str, user: str = "123456") -> PrivateMessageEvent:
    message = _msg(MessageSegment.text(text)) if text else Message()
    return PrivateMessageEvent(
        time=1700000000,
        self_id=int(SELF),
        post_type="message",
        sub_type="friend",
        user_id=int(user),
        message_type="private",
        message_id=2,
        message=message,
        raw_message=str(message),
        font=0,
        sender={"card": "", "nickname": "t", "role": "friend", "title": ""},
        to_me=True,
    )


def test_private_commands_match():
    assert endfield._rule(private_event("便签")) is True
    assert endfield._rule(private_event("理智")) is True
    assert endfield._rule(private_event("签到")) is True
    assert endfield._rule(private_event("切换绑定 2")) is True
    assert endfield._rule(private_event("删除绑定 3")) is True
    assert endfield._rule(private_event("便签呀")) is False
    assert endfield._rule(private_event("")) is False


def test_group_requires_at():
    assert endfield._rule(group_event("便签")) is False
    assert endfield._rule(group_event("便签", at_self=True)) is True
    assert endfield._rule(group_event("便签", at_other=True)) is False
    assert endfield._rule(group_event("绑定列表", at_self=True)) is True


def test_pending_consent_matches():
    endfield._pending_consent.add("123456")
    try:
        assert endfield._rule(private_event("同意")) is True
        assert endfield._rule(private_event("取消")) is True
    finally:
        endfield._pending_consent.discard("123456")
    assert endfield._rule(private_event("同意")) is False


def test_binding_payload_extraction():
    direct = endfield._binding_from_payload(
        {
            "status": "approved",
            "role_id": "role-1",
            "server_id": 1,
            "framework_token": "tok-1",
        }
    )
    assert direct is not None
    assert direct["role_id"] == "role-1"
    assert direct["framework_token"] == "tok-1"

    nested = endfield._binding_from_payload(
        {"binding": {"roleId": "role-2", "token": "tok-2", "serverId": 2}}
    )
    assert nested is not None
    assert nested["role_id"] == "role-2"
    assert nested["server_id"] == 2

    assert endfield._binding_from_payload({"status": "approved"}) is None


def test_status_approved():
    assert endfield._status_approved({"status": "approved"})
    assert endfield._status_approved({"approved": True})
    assert not endfield._status_approved({"status": "pending"})


def test_2a_commands_match():
    assert endfield._rule(private_event("抽卡记录 2")) is True
    assert endfield._rule(private_event("抽卡分析")) is True
    assert endfield._rule(private_event("干员列表 3")) is True
    assert endfield._rule(private_event("干员面板 陈")) is True
    assert endfield._rule(private_event("危机合约")) is True
    assert endfield._rule(private_event("日历")) is True
    assert endfield._rule(private_event("同步抽卡")) is True
    assert endfield._rule(private_event("同步面板")) is True
    assert endfield._rule(group_event("日历")) is False
    assert endfield._rule(group_event("日历", at_self=True)) is True
    assert "同步抽卡" in endfield.logic.PRIVATE_COMMANDS


def test_2b_commands_match():
    for text in ("成就列表", "帝江号建设", "地区建设", "全服统计"):
        assert endfield._rule(private_event(text)) is True
        assert text in endfield.CARD_COMMANDS
    assert endfield._rule(group_event("全服统计")) is False
    assert endfield._rule(group_event("全服统计", at_self=True)) is True


def test_subscription_and_announcement_commands_match():
    for text in ("订阅理智", "取消订阅理智", "订阅列表", "订阅公告", "取消订阅公告"):
        assert endfield._rule(private_event(text)) is True
    assert endfield._rule(group_event("订阅公告")) is False
    assert endfield._rule(group_event("订阅公告", at_self=True)) is True
    assert "订阅公告" in endfield.logic.SUB_GROUP_COMMANDS
    assert "订阅理智" in endfield.logic.SUB_PRIVATE_COMMANDS
    assert endfield._rule(private_event("公告")) is True
    assert endfield._rule(private_event("公告最新")) is True
    assert "公告" in endfield.CARD_COMMANDS


def test_2a_payload_helpers():
    row = endfield._find_char(
        [{"name_cn": "陈", "template_id": "tpl-1"}], "陈"
    )
    assert row is not None
    assert row["template_id"] == "tpl-1"
    assert endfield._find_char([{"name_cn": "陈"}], "银灰") is None

    contract = endfield._pick_contract(
        {"crisisContract": [{"name": "净罪", "id": "c-1"}]}, "净罪"
    )
    assert contract is not None
    assert contract["id"] == "c-1"
    assert endfield._pick_contract({"crisisContract": []}) is None
