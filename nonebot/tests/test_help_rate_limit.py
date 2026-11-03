"""Group help is independent of the chat gate, with one shared sliding quota."""
import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_gate_wiring import FakeBot, group_event, private_event
import gate_logic as gl
import plugins.gate as gate
from nonebot.exception import FinishedException


def test_sliding_window_expires_requests_individually():
    limiter = gl.HelpRateLimiter()
    assert all(limiter.acquire('one', now=t) for t in (100, 110, 120, 130, 140))
    assert not limiter.acquire('one', now=159.999)
    assert limiter.acquire('two', now=159.999)
    assert limiter.acquire('one', now=160)
    assert not limiter.acquire('one', now=160)
    assert limiter.acquire('one', now=170)


def test_rejections_do_not_extend_the_window():
    limiter = gl.HelpRateLimiter()
    assert all(limiter.acquire('one', now=100) for _ in range(5))
    assert not limiter.acquire('one', now=159)
    assert all(limiter.acquire('one', now=160) for _ in range(5))
    assert not limiter.acquire('one', now=160)


def test_group_help_matches_without_at_and_admin_commands_stay_private():
    async def run():
        for command in ('.help', '/帮助', '.帮助'):
            event = group_event(command)
            assert not event.is_tome()
            assert await gate.help_cmd.rule(FakeBot(), event, {})
        assert not await gate.is_help_command(group_event('help me'))
        assert not await gate.is_gate_command(group_event('.限流 默认 100'))
    asyncio.run(run())
    assert gate.help_cmd.block and gate.help_cmd.priority < 90


def test_concurrent_members_share_five_slots_and_private_is_unlimited():
    replies = []

    async def finish(message=None):
        replies.append(message)
        raise FinishedException

    async def invoke(event):
        with pytest.raises(FinishedException):
            await gate.handle_help(event)

    async def run():
        # Includes the owner: the group limit applies to everyone together.
        await asyncio.gather(*(invoke(group_event('.help', user=str(user)))
                               for user in (10000001, 1, 2, 3, 4, 5, 6, 7)))
        assert len([reply for reply in replies if reply is not None]) == 5
        assert replies.count(None) == 3
        other = group_event('.help')
        other.group_id += 1
        await invoke(other)
        await asyncio.gather(*(invoke(private_event('.help')) for _ in range(7)))

    with patch.object(gate, '_help_limiter', gl.HelpRateLimiter()), \
            patch.object(gate, 'help_image', return_value=[Path('help.png')]) as render, \
            patch.object(gate, '_protocol_path', side_effect=lambda path: path), \
            patch.object(gate.help_cmd, 'finish', side_effect=finish):
        asyncio.run(run())
    assert render.call_count == 13
    assert len([reply for reply in replies if reply is not None]) == 13
    assert all(reply.type == 'image' for reply in replies if reply is not None)


def test_text_fallback_uses_the_same_quota():
    replies = []

    async def finish(message=None):
        replies.append(message)
        raise FinishedException

    async def run():
        for _ in range(6):
            with pytest.raises(FinishedException):
                await gate.handle_help(group_event('.help'))

    with patch.object(gate, '_help_limiter', gl.HelpRateLimiter()), \
            patch.object(gate, 'help_image', side_effect=RuntimeError('renderer offline')) as render, \
            patch.object(gate, 'help_text', return_value='help text'), \
            patch.object(gate.help_cmd, 'finish', side_effect=finish):
        asyncio.run(run())
    assert render.call_count == 5
    assert replies == ['help text'] * 5 + [None]
