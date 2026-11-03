"""Replay OneBot reply preprocessing and the actual MuiceBot chat parser."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests._bootstrap import ensure_nonebot

ensure_nonebot()

from nonebot.adapters.onebot.v11 import Bot as OneBot, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.bot import _check_at_me, _check_reply
from muicebot.onebot import at_event, _extract_multi_resources
from nonebot_plugin_alconna.uniseg.params import _uni_msg
from nonebot_plugin_alconna.extension import unimsg_cache, unimsg_origin_cache
from plugins import gate, quote_fix

SELF = '3892894069'


class Bot:
    self_id = SELF

    async def get_msg(self, message_id):
        return dict(time=1, message_type='group', message_id=message_id, real_id=message_id,
                    sender={'user_id': 123456, 'nickname': 'sender'},
                    message=Message('被引用的内容'))


def make_event(parts):
    message = Message(parts)
    return GroupMessageEvent(time=1, self_id=int(SELF), post_type='message', sub_type='normal',
        user_id=10000001, group_id=1108838060, message_type='group', message_id=10,
        message=message, raw_message=str(message), font=0,
        sender={'nickname': 'test', 'role': 'member'})


def preprocess(parts):
    event = make_event(parts)
    bot = Bot()
    asyncio.run(_check_reply(bot, event))
    _check_at_me(bot, event)
    return event


def can_reach_chat(event):
    unimsg_cache.clear()
    unimsg_origin_cache.clear()
    bot = OneBot(SimpleNamespace(get_name=lambda: 'OneBot V11'), SELF)
    return asyncio.run(at_event.rule(bot, event, {}))


def test_reply_and_at_without_body_reaches_chat():
    event = preprocess([MessageSegment.reply('99'), MessageSegment.at(SELF), MessageSegment.text(' ')])
    assert not can_reach_chat(event)
    asyncio.run(quote_fix.prepare_quoted_chat(event))
    assert can_reach_chat(event)
    assert asyncio.run(gate.gate_rule(Bot(), event))


def test_reply_and_middle_at_reaches_chat():
    event = preprocess([MessageSegment.reply('99'), MessageSegment.text('看看 '),
                        MessageSegment.at(SELF), MessageSegment.text(' 这句话')])
    assert not can_reach_chat(event)
    asyncio.run(quote_fix.prepare_quoted_chat(event))
    assert can_reach_chat(event)
    assert asyncio.run(gate.gate_rule(Bot(), event))


def test_quote_without_at_does_not_trigger():
    event = preprocess([MessageSegment.reply('99'), MessageSegment.text('看看')])
    asyncio.run(quote_fix.prepare_quoted_chat(event))
    assert not can_reach_chat(event)


def test_mentions_other_users_or_before_reply_do_not_trigger():
    for parts in (
        [MessageSegment.reply('99'), MessageSegment.at('456'), MessageSegment.text('你好')],
        [MessageSegment.at(SELF), MessageSegment.reply('99'), MessageSegment.text('你好')],
    ):
        event = preprocess(parts)
        before = event.message.copy()
        asyncio.run(quote_fix.prepare_quoted_chat(event))
        assert event.message == before


def test_original_message_is_preserved():
    event = preprocess([MessageSegment.reply('99'), MessageSegment.at(SELF)])
    original = event.original_message.copy()
    asyncio.run(quote_fix.prepare_quoted_chat(event))
    assert event.original_message == original
    assert quote_fix.QUOTE_CHAT_HINT in event.get_plaintext()


def test_quoted_image_and_at_reaches_chat():
    event = preprocess([MessageSegment.reply('99'), MessageSegment.image('test.png'),
                        MessageSegment.at(SELF)])
    asyncio.run(quote_fix.prepare_quoted_chat(event))
    assert can_reach_chat(event)
    assert any(segment.type == 'image' for segment in event.message)


def test_image_in_referenced_message_reaches_resource_extraction():
    event = preprocess([MessageSegment.reply('99'), MessageSegment.at(SELF)])
    event.reply.message = Message([MessageSegment.image('https://example.test/quoted.jpg')])
    asyncio.run(quote_fix.prepare_quoted_chat(event))
    bot = OneBot(SimpleNamespace(get_name=lambda: 'OneBot V11'), SELF)
    unimsg_cache.clear()
    unimsg_origin_cache.clear()
    state = {}
    assert asyncio.run(at_event.rule(bot, event, state))
    message = asyncio.run(_uni_msg(bot, event, state))
    with patch('muicebot.onebot.download_file', new=AsyncMock(return_value='quoted.jpg')) as download:
        resources = asyncio.run(_extract_multi_resources(message, event))
    assert len(resources) == 1 and resources[0].type == 'image'
    assert download.await_args.args[0] == 'https://example.test/quoted.jpg'


def test_quoted_images_leave_original_and_reply_unchanged():
    event = preprocess([MessageSegment.reply('99'), MessageSegment.at(SELF)])
    event.reply.message = Message([MessageSegment.text('原文'), MessageSegment.image('one.jpg'),
                                   MessageSegment.image('two.jpg')])
    original, reply = event.original_message.copy(), event.reply.message.copy()
    asyncio.run(quote_fix.prepare_quoted_chat(event))
    assert event.original_message == original and event.reply.message == reply
    assert [part.data['file'] for part in event.message if part.type == 'image'] == ['one.jpg', 'two.jpg']


def test_quoted_image_without_addressing_bot_is_not_added():
    event = preprocess([MessageSegment.reply('99'), MessageSegment.text('看看')])
    event.reply.message = Message([MessageSegment.image('one.jpg')])
    before = event.message.copy()
    asyncio.run(quote_fix.prepare_quoted_chat(event))
    assert event.message == before
