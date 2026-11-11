import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from media_parser.core.data import ParseResult, Platform
from media_parser.runtime import ParserRuntime


class PerfMetricsTests(unittest.IsolatedAsyncioTestCase):
    def make_runtime(self, *, failure=False):
        runtime = ParserRuntime.__new__(ParserRuntime)
        runtime.cfg = SimpleNamespace(arbiter=False)
        runtime.debouncer = SimpleNamespace(hit_link=lambda *_: False, hit_resource=lambda *_: False)
        runtime.log_failure = Mock()
        result = ParseResult(platform=Platform('testplatform', 'Test'), title='SECRET-TITLE', url='https://private.invalid/SECRET-URL')
        parser = SimpleNamespace(platform=result.platform, parse=AsyncMock(side_effect=RuntimeError('SECRET-ERROR') if failure else None, return_value=result))
        runtime.parsers = {'key': parser}
        runtime.sender = SimpleNamespace(send_parse_result=AsyncMock())
        return runtime

    async def test_success_reports_parse_send_and_total_without_content(self):
        runtime = self.make_runtime()
        event = SimpleNamespace(self_id=1, user_id=2)
        match = ('key', re.search('private', 'private'))
        with patch('media_parser.runtime.logger.info') as log:
            await runtime.process(SimpleNamespace(send=AsyncMock()), event, match)
        messages = '\n'.join(str(call.args[0]) for call in log.call_args_list)
        for stage in ('parse', 'send', 'total'):
            self.assertIn('stage=' + stage, messages)
        self.assertNotIn('SECRET', messages)
        self.assertNotIn('private', messages)

    async def test_failure_reports_failed_parse_and_total_without_exception(self):
        runtime = self.make_runtime(failure=True)
        event = SimpleNamespace(self_id=1, user_id=2)
        match = ('key', re.search('private', 'private'))
        with patch('media_parser.runtime.logger.info') as log:
            await runtime.process(SimpleNamespace(send=AsyncMock()), event, match)
        messages = '\n'.join(str(call.args[0]) for call in log.call_args_list)
        self.assertIn('stage=parse status=error', messages)
        self.assertIn('stage=total status=error', messages)
        self.assertNotIn('SECRET', messages)
        self.assertNotIn('private', messages)

    async def test_skipped_link_is_not_counted_as_success(self):
        runtime = self.make_runtime()
        runtime.debouncer.hit_link = lambda *_: True
        event = SimpleNamespace(self_id=1, user_id=2)
        with patch('media_parser.runtime.logger.info') as log:
            await runtime.process(SimpleNamespace(send=AsyncMock()), event, ('key', re.search('x', 'x')))
        self.assertIn('stage=total status=skipped', '\n'.join(str(call.args[0]) for call in log.call_args_list))

    async def test_cancellation_is_distinct_from_error(self):
        import asyncio
        runtime = self.make_runtime()
        runtime.parsers['key'].parse = AsyncMock(side_effect=asyncio.CancelledError())
        event = SimpleNamespace(self_id=1, user_id=2)
        with patch('media_parser.runtime.logger.info') as log:
            with self.assertRaises(asyncio.CancelledError):
                await runtime.process(SimpleNamespace(send=AsyncMock()), event, ('key', re.search('x', 'x')))
        messages = '\n'.join(str(call.args[0]) for call in log.call_args_list)
        self.assertIn('stage=parse status=cancelled', messages)
        self.assertIn('stage=total status=cancelled', messages)

    async def test_preview_fallback_is_recorded_as_degraded(self):
        from media_parser.core.sender import MessageSender
        from media_parser.core.perf import begin, end, outcome
        config = SimpleNamespace(single_heavy_render_card=True, forward_threshold=3, show_download_fail_tip=True, audio_to_file=False)
        sender = MessageSender(config, SimpleNamespace(render_cards=AsyncMock(return_value=[])))
        result = ParseResult(platform=Platform('testplatform', 'Test'), title='private-title', url='https://private.invalid/x')
        event = SimpleNamespace(send=AsyncMock(), chain_result=lambda x: x)
        token = begin('testplatform')
        try:
            with patch('media_parser.runtime.logger.info') as log:
                await sender._send_preview_card(event, result, {'preview_card': True})
            messages = '\n'.join(str(call.args[0]) for call in log.call_args_list)
            self.assertIn('stage=preview_render status=degraded', messages)
            self.assertIn('stage=preview_fallback_send status=ok', messages)
            self.assertEqual(outcome(), 'degraded')
        finally:
            end(token)

    async def test_fallback_send_error_is_not_counted_success(self):
        from media_parser.core.sender import MessageSender
        from media_parser.core.perf import begin, end, outcome
        config = SimpleNamespace(single_heavy_render_card=False, forward_threshold=3, show_download_fail_tip=True, audio_to_file=False)
        sender = MessageSender(config, SimpleNamespace())
        result = ParseResult(platform=Platform('testplatform', 'Test'), text='private-text')
        event = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError('private-error')), chain_result=lambda x: x)
        token = begin('testplatform')
        try:
            with patch('media_parser.runtime.logger.info') as log:
                await sender.send_parse_result(event, result)
            messages = '\n'.join(str(call.args[0]) for call in log.call_args_list)
            self.assertIn('stage=text_fallback_send status=error', messages)
            self.assertEqual(outcome(), 'error')
        finally:
            end(token)

    async def test_metric_logging_failure_does_not_disrupt_request(self):
        runtime = self.make_runtime()
        event = SimpleNamespace(self_id=1, user_id=2)
        with patch('media_parser.runtime.logger.info', side_effect=RuntimeError('logging failed')):
            await runtime.process(SimpleNamespace(send=AsyncMock()), event, ('key', re.search('x', 'x')))
        runtime.sender.send_parse_result.assert_awaited_once()

    async def test_request_total_reflects_preview_degradation(self):
        from media_parser.core.sender import MessageSender
        runtime = self.make_runtime()
        runtime.parsers['key'].parse.return_value.text = 'private-text'
        config = SimpleNamespace(single_heavy_render_card=True, forward_threshold=3, show_download_fail_tip=True, audio_to_file=False, container_cache_dir=None)
        runtime.cfg = SimpleNamespace(arbiter=False, container_cache_dir=None)
        runtime.sender = MessageSender(config, SimpleNamespace(render_cards=AsyncMock(return_value=[])))
        bot = SimpleNamespace(self_id='1', send=AsyncMock())
        event = SimpleNamespace(self_id=1, user_id=2)
        with patch('media_parser.runtime.logger.info') as log:
            await runtime.process(bot, event, ('key', re.search('x', 'x')))
        messages = '\n'.join(str(call.args[0]) for call in log.call_args_list)
        self.assertIn('stage=preview_render status=degraded', messages)
        self.assertIn('stage=preview_fallback_send status=ok', messages)
        self.assertIn('stage=send status=degraded', messages)
        self.assertIn('stage=total status=degraded', messages)
        self.assertNotIn('private', messages)

    async def test_media_download_failure_marks_request_degraded(self):
        import asyncio
        from pathlib import Path
        from media_parser.core.data import ImageContent
        from media_parser.core.exception import DownloadException
        from media_parser.core.sender import MessageSender
        runtime = self.make_runtime()
        async def fail_download():
            raise DownloadException('private-url')
        result = runtime.parsers['key'].parse.return_value
        result.contents = [ImageContent(asyncio.create_task(fail_download()))]
        config = SimpleNamespace(single_heavy_render_card=True, forward_threshold=3, show_download_fail_tip=True, audio_to_file=False, container_cache_dir=None)
        runtime.cfg = SimpleNamespace(arbiter=False, container_cache_dir=None)
        runtime.sender = MessageSender(config, SimpleNamespace(render_cards=AsyncMock(return_value=[Path('/tmp/perf-preview.png')])))
        bot = SimpleNamespace(self_id='1', send=AsyncMock())
        event = SimpleNamespace(self_id=1, user_id=2)
        with patch('media_parser.runtime.logger.info') as log:
            await runtime.process(bot, event, ('key', re.search('x', 'x')))
        messages = '\n'.join(str(call.args[0]) for call in log.call_args_list)
        self.assertIn('stage=media_prepare status=degraded', messages)
        self.assertIn('stage=send status=degraded', messages)
        self.assertIn('stage=total status=degraded', messages)
        self.assertNotIn('private', messages)
