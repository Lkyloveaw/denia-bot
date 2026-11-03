"""解析器管理指令的权限用例。

口径：只认 `.env` 里的 `SUPERUSERS`，群主/群管理员不再放行。
这里锁住的就是「群角色不外溢成机器人权限」这一条。
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot

ensure_nonebot(superusers={'1000'})

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from plugins import media_parser as plugin

SUPERUSER_ID = 1000
STRANGER_ID = 2000


def group_event(user_id: int, role: str) -> GroupMessageEvent:
    message = Message([MessageSegment.text('开启解析')])
    return GroupMessageEvent(
        time=1700000000, self_id=100, post_type='message', sub_type='normal',
        user_id=user_id, group_id=555, message_type='group', message_id=1,
        message=message, raw_message=str(message), font=0,
        sender={'nickname': 'tester', 'role': role}, to_me=False,
    )


class MediaParserPermissionTest(unittest.TestCase):
    def test_superuser_allowed_whatever_the_group_role(self):
        for role in ('member', 'admin', 'owner'):
            self.assertTrue(plugin.is_superuser(group_event(SUPERUSER_ID, role)), role)

    def test_group_admin_and_owner_rejected(self):
        for role in ('admin', 'owner'):
            self.assertFalse(plugin.is_superuser(group_event(STRANGER_ID, role)), role)

    def test_plain_member_rejected(self):
        self.assertFalse(plugin.is_superuser(group_event(STRANGER_ID, 'member')))

    def test_handler_refuses_group_admin(self):
        """走到 handler 的群管理会被拒，且拒的是权限那一关。"""

        class Denied(Exception):
            pass

        async def deny(message):
            raise Denied(message)

        with patch.object(plugin.control, 'finish', deny):
            with self.assertRaises(Denied) as caught:
                asyncio.run(plugin.control_parser(FakeBot(), group_event(STRANGER_ID, 'admin')))
        self.assertIn('超级管理员', str(caught.exception))

    def test_handler_lets_superuser_through_the_permission_gate(self):
        """超管能过权限关，停在下一关（解析器未就绪），说明没被误拦。"""

        class Halt(Exception):
            pass

        async def halt(message):
            raise Halt(message)

        with patch.object(plugin.control, 'finish', halt),              patch.object(plugin, 'runtime', None):
            with self.assertRaises(Halt) as caught:
                asyncio.run(plugin.control_parser(FakeBot(), group_event(SUPERUSER_ID, 'member')))
        self.assertIn('解析器未就绪', str(caught.exception))


class FakeBot:
    async def send(self, *args, **kwargs):
        return None


if __name__ == '__main__':
    unittest.main()
