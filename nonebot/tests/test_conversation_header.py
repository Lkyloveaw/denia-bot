import os
import asyncio
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conversation_header import configure


class ConversationHeaderTest(unittest.TestCase):
    def test_preserves_other_headers_and_replaces_existing_id(self):
        headers = configure('X-Other: keep\nX-Conversation-Id: stale', 'new-id')
        self.assertEqual(headers, 'X-Other: keep\nx-conversation-id: new-id')

    def test_processes_get_different_ids(self):
        command = [sys.executable, '-c', 'from conversation_header import configure; print(configure())']
        env = dict(os.environ, OPENAI_CUSTOM_HEADERS='')
        one = subprocess.check_output(command, env=env, text=True)
        two = subprocess.check_output(command, env=env, text=True)
        self.assertNotEqual(one, two)
        self.assertTrue(one.startswith('x-conversation-id: '))

    def test_sdk_sends_header_on_recreated_clients(self):
        import httpx
        from openai import AsyncOpenAI
        from unittest.mock import patch

        async def exercise():
            seen = []

            def respond(request):
                seen.append((request.headers.get('x-conversation-id'), request.headers.get('x-other')))
                return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion',
                                                  'created': 0, 'model': 'test', 'choices': []})

            with patch.dict(os.environ, {'OPENAI_CUSTOM_HEADERS': configure('x-other: keep', 'test-id')}):
                for _ in range(2):
                    transport = httpx.MockTransport(respond)
                    async with AsyncOpenAI(api_key='test', base_url='http://localhost/v1',
                                           http_client=httpx.AsyncClient(transport=transport)) as client:
                        await client.chat.completions.create(model='test', messages=[])
            self.assertEqual(seen, [('test-id', 'keep'), ('test-id', 'keep')])

        asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
