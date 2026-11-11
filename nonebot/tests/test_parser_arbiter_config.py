import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from media_parser.core.data import ParseResult, Platform
from media_parser.runtime import ParserRuntime

class ParserArbiterConfigTest(unittest.IsolatedAsyncioTestCase):
    async def test_group_arbitration_obeys_config(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                runtime = ParserRuntime.__new__(ParserRuntime)
                runtime.cfg = SimpleNamespace(arbiter=enabled)
                runtime.debouncer = SimpleNamespace(hit_link=lambda *_: False, hit_resource=lambda *_: False)
                runtime.arbiter = SimpleNamespace(compete=AsyncMock(return_value=True))
                runtime.log_failure = Mock()
                result = ParseResult(platform=Platform('test', 'Test'))
                runtime.parsers = {'key': SimpleNamespace(platform=result.platform, parse=AsyncMock(return_value=result))}
                runtime.sender = SimpleNamespace(send_parse_result=AsyncMock())
                event = SimpleNamespace(self_id=1, user_id=2, group_id=3, message_id=4, time=5)
                await runtime.process(SimpleNamespace(), event, ('key', re.search('x', 'x')))
                self.assertEqual(runtime.arbiter.compete.await_count, int(enabled))
                runtime.sender.send_parse_result.assert_awaited_once()
