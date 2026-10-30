import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
import nonebot
nonebot.init()
nonebot.load_plugin('muicebot')
from muicebot.llm import ModelRequest
from plugins import memory as plugin


class EventStub:
    def __init__(self, uid, kind):
        self.uid, self.message_type = uid, kind

    def get_user_id(self):
        return self.uid

    def get_plaintext(self):
        return '我喜欢喝咖啡'

    self_id = 3892894069
    message_id = 123456


class FakeCompletions:
    async def create(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"memories":["用户喜欢喝咖啡"]}'))])


class MemoryWiringTest(unittest.TestCase):
    def test_background_extraction_and_cross_scope_injection(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            old = plugin.memory.DEFAULT_PATH
            old_embed = plugin._embed
            plugin.memory.DEFAULT_PATH = Path(folder) / 'memory.sqlite3'
            try:
                async def fake_embed(_text):
                    return [1.0, 0.0]
                plugin._embed = fake_embed
                client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
                version = plugin.memory.revision(plugin.memory.DEFAULT_PATH, '42')
                asyncio.run(plugin._extract('42', '我喜欢咖啡', 'private', version, client, 'test'))
                req = ModelRequest(prompt='你好', system='人格')
                asyncio.run(plugin.inject(req, EventStub('42', 'group')))
                self.assertIn('用户喜欢喝咖啡', req.system)
                self.assertIn('不要主动', req.system)
                other = ModelRequest(prompt='你好', system='人格')
                asyncio.run(plugin.inject(other, EventStub('43', 'group')))
                self.assertEqual(other.system, '人格')
            finally:
                plugin.memory.DEFAULT_PATH = old
                plugin._embed = old_embed

    def test_durable_job_retries_and_then_completes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            old = plugin.memory.DEFAULT_PATH
            old_embed = plugin._embed
            plugin.memory.DEFAULT_PATH = Path(folder) / 'memory.sqlite3'
            try:
                client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
                provider = SimpleNamespace(model=SimpleNamespace(client=client, model='test'))
                event = EventStub('42', 'private')
                message = SimpleNamespace(respond='收到')
                asyncio.run(plugin.inject(ModelRequest(prompt='我喜欢喝咖啡'), event))
                asyncio.run(plugin.collect(message, event))
                job = plugin.memory.pending_jobs(plugin.memory.DEFAULT_PATH)[0]
                async def fail_embed(_text):
                    raise TimeoutError('temporary')
                async def good_embed(_text):
                    return [1.0, 0.0]
                with patch('muicebot.muice.Muice.get_instance', return_value=provider):
                    plugin._embed = fail_embed
                    asyncio.run(plugin._process_job(job))
                    job = plugin.memory.pending_jobs(plugin.memory.DEFAULT_PATH)[0]
                    self.assertEqual(job['attempts'], 1)
                    plugin._embed = good_embed
                    asyncio.run(plugin._process_job(job))
                self.assertEqual(plugin.memory.pending_jobs(plugin.memory.DEFAULT_PATH), [])
                self.assertEqual(len(plugin.memory.list_memories(plugin.memory.DEFAULT_PATH, '42')), 1)
            finally:
                plugin.memory.DEFAULT_PATH = old
                plugin._embed = old_embed


if __name__ == '__main__':
    unittest.main()
