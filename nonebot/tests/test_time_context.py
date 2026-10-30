import asyncio
import copy
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.getcwd())
import nonebot
nonebot.init()
nonebot.load_plugin('muicebot')
from muicebot.llm import ModelRequest
from muicebot.models import Message
from plugins.time_context import add_time_context


def test_history_time_and_unknown():
    original = Message(time='2026.10.28 23:30:00', userid='1', message='晚安', respond='晚安')
    unknown = Message(time='invalid', userid='1', message='早上好', respond='早')
    req = ModelRequest(prompt='早上好', system='角色', history=[original, unknown])
    asyncio.run(add_time_context(req))
    assert '北京时间' in req.system and '当前' in req.system
    assert '2026.10.28 23:30:00' in req.history[0].message
    assert '时间未知' in req.history[1].message
    assert original.message == '晚安' and unknown.message == '早上好'
    assert req.history[0].respond == '晚安'


if __name__ == '__main__':
    test_history_time_and_unknown()
    print('time context test passed')
