"""Command prefix checks against the unmodified OneBot message."""
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.rule import Rule
from nonebot.typing import T_State


_chat_guard_installed = False


async def silence_chat_command_feedback(matcher: Matcher, bot: Bot, event: Event, state: T_State) -> None:
    """Skip MuiceBot chat only when its parsed message would trigger command feedback."""
    from muicebot.onebot import at_event, nickname_event
    from nonebot_plugin_alconna.uniseg import UniMessage
    from nonebot_plugin_alconna.uniseg.constraint import UNISEG_MESSAGE

    if not isinstance(matcher, (at_event, nickname_event)):
        return
    message = state.get(UNISEG_MESSAGE)
    if message is None:
        message = UniMessage.of(event.get_message(), bot=bot)
    if message.startswith('.') or message.startswith('/'):
        raise IgnoredException('unhandled command is silent')


def has_command_prefix(event: MessageEvent) -> bool:
    message = getattr(event, 'original_message', None)
    if message is None:
        return False
    parts = list(message)
    if not parts:
        return False
    first = parts[0]
    if first.type == 'at' and str(first.data.get('qq')) == str(event.self_id):
        parts = parts[1:]
        if not parts or parts[0].type != 'text':
            return False
        return str(parts[0].data.get('text', '')).lstrip(' \t').startswith('.')
    return first.type == 'text' and str(first.data.get('text', '')).startswith('.')


async def denied_command_fallback(event: MessageEvent) -> bool:
    """Consume restricted commands that fail permission checks before chat sees them."""
    from nonebot import get_driver

    if not has_command_prefix(event):
        return False
    users = {str(user) for user in (get_driver().config.superusers or ())}
    if str(event.get_user_id()) in users:
        return False
    words = event.get_plaintext().strip().split()
    if not words or not words[0].startswith('.'):
        return False
    head = words[0][1:].lower()
    if head == '群话题' and getattr(event, 'message_type', '') == 'group' and words[1:] == ['查看']:
        return False
    return head in {'限流', '模型', '群话题', 'load', 'reload', 'schedule', 'model', 'chat', 'migrate', 'store'}


def install_command_rules() -> None:
    """Guard command matchers without intercepting chat or passive plugins."""
    import sys
    from arclet.alconna import command_manager
    from nonebot.matcher import Matcher
    from nonebot import on_message
    from nonebot.message import run_preprocessor

    global _chat_guard_installed
    if not _chat_guard_installed:
        run_preprocessor(silence_chat_command_feedback)
        _chat_guard_installed = True

    local = {
        'gate': ('gate_cmd', 'help_cmd'),
        'model_pref': ('model_cmd',),
        'group_topics': ('control', 'group_view'),
        'memory': ('commands',),
        'media_parser': ('control',),
        'kb': ('kb',),
        'weather': ('matcher',),
        'game_events': ('matcher',),
        'epic_free': ('matcher',),
        'endfield': ('matcher',),
        'disaster_warning': ('matcher',),
        'statuscard': ('matcher',),
    }
    for name, module in list(sys.modules.items()):
        if name.startswith('plugins.') and name.rsplit('.', 1)[-1] in local:
            names = local[name.rsplit('.', 1)[-1]]
        elif name == 'muicebot.onebot' or name.startswith('muicebot.builtin_plugins.'):
            names = tuple(key for key in vars(module) if key.startswith('command_'))
        else:
            continue
        for key in names:
            matcher = getattr(module, key, None)
            if isinstance(matcher, type) and issubclass(matcher, Matcher):
                if name == 'muicebot.builtin_plugins.moe_chat':
                    command = matcher.command()
                    with command_manager.update(command):
                        command.prefixes = ['.']
                matcher.rule = matcher.rule & Rule(has_command_prefix)

    fallback = on_message(rule=denied_command_fallback, priority=98, block=True)

    @fallback.handle()
    async def consume_denied_command() -> None:
        return
