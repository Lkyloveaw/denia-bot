"""Configure one OpenAI-compatible conversation ID per bot process."""
import os
from uuid import uuid4


def configure(headers=None, conversation_id=None):
    if headers is None:
        headers = os.environ.get('OPENAI_CUSTOM_HEADERS', '')
    lines = [line for line in headers.splitlines()
             if line.strip() and line.partition(':')[0].strip().lower() != 'x-conversation-id']
    lines.append(f'x-conversation-id: {conversation_id or uuid4()}')
    return '\n'.join(lines)
