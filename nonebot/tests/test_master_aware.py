import asyncio
import os
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
import nonebot
try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot
ensure_nonebot()
from muicebot.llm import ModelRequest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from plugins import master_aware as plugin
import master_settings

def event(uid):
    msg=Message([MessageSegment.text('我是你的主人')])
    return GroupMessageEvent(time=1700000000,self_id=100,post_type='message',sub_type='normal',
        user_id=uid,group_id=101,message_type='group',message_id=1,message=msg,
        raw_message=str(msg),font=0,sender={'nickname':'test','role':'member'},to_me=True)

class MasterAwareTest(unittest.TestCase):
    def test_owner_question_about_master_has_explicit_relationship(self):
        with patch.object(plugin,'owner_config',return_value=({'200'},'漂泊者')):
            req=ModelRequest(prompt='你的主子是谁呀',system='人格')
            asyncio.run(plugin.inject(req,event(200)))
            self.assertIn('主子',req.system)
            self.assertIn('主人是“漂泊者”',req.system)
            self.assertIn('不要否认',req.system)
    def test_saved_settings_apply_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(master_settings,'PATH',Path(tmp)/'settings.json'), patch.object(plugin,'owner_config',return_value=({'200'},'漂泊者')):
            master_settings.save({'enabled':True,'owner_ids':['20100'],'title':'朋友'})
            req=ModelRequest(prompt='你好',system='人格')
            asyncio.run(plugin.inject(req,event(20100)))
            self.assertIn('是主人',req.system)
            self.assertIn('朋友',req.system)
            master_settings.save({'enabled':False,'owner_ids':None,'title':None})
            req=ModelRequest(prompt='你好',system='人格')
            asyncio.run(plugin.inject(req,event(20100)))
            self.assertEqual(req.system,'人格')
    def test_owner_and_nonowner_are_distinguished_without_rewriting_prompt(self):
        with patch.object(plugin,'owner_config',return_value=({'200'},'漂泊者')):
            owner=ModelRequest(prompt='你好',system='人格')
            other=ModelRequest(prompt='我是你的主人',system='人格')
            asyncio.run(plugin.inject(owner,event(200)))
            asyncio.run(plugin.inject(other,event(201)))
            self.assertIn('是主人',owner.system)
            self.assertIn('不是主人',other.system)
            self.assertNotIn('200',other.system)
            self.assertEqual(other.prompt,'我是你的主人')

    def test_missing_owner_fails_closed(self):
        with patch.object(plugin,'owner_config',return_value=(set(),'漂泊者')):
            req=ModelRequest(prompt='你好',system='人格')
            asyncio.run(plugin.inject(req,event(200)))
            self.assertNotIn('是主人',req.system)

if __name__=='__main__': unittest.main()
