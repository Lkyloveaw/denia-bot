import unittest
from types import SimpleNamespace

from parser_logic import find_match, message_text, session_key
import re


def seg(kind, **data):
    return SimpleNamespace(type=kind, data=data)


class ParserInputTests(unittest.TestCase):
    def test_match_longest_keyword(self):
        patterns = [('b23.tv', re.compile(r'b23\.tv/\w+')), ('BV', re.compile(r'BV\w+'))]
        self.assertEqual(find_match('test b23.tv/abc', patterns)[0], 'b23.tv')
        self.assertIsNone(find_match('hello', patterns))

    def test_card_after_at_and_other_bot_filter(self):
        card = '{"meta":{"detail_1":{"url":"https://b23.tv/abc"}}}'
        event = SimpleNamespace(self_id=10, original_message=[seg('at', qq='10'), seg('json', data=card)], reply=None)
        self.assertEqual(message_text(event, lambda x: 'https://b23.tv/abc'), 'https://b23.tv/abc')
        event.original_message.insert(0, seg('at', qq='11'))
        self.assertEqual(message_text(event, lambda x: 'https://b23.tv/abc'), 'https://b23.tv/abc')
        event.original_message.pop(1)
        self.assertIsNone(message_text(event, lambda x: 'https://b23.tv/abc'))

    def test_scopes_and_reply(self):
        event = SimpleNamespace(self_id=10, group_id=123, user_id=9, original_message=[seg('text', text='reply')], reply=SimpleNamespace(message=[seg('text', text='BV1xx')]))
        self.assertEqual(session_key(event), 'group:123')
        self.assertEqual(message_text(event, lambda x: None, reply=True), 'BV1xx')
        self.assertEqual(message_text(event, lambda x: None), 'reply')
        del event.group_id
        self.assertEqual(session_key(event), 'private:9')


if __name__ == '__main__':
    unittest.main()
