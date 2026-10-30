import unittest
from types import SimpleNamespace

from media_parser.core.parsers.xhs import XHSParser
from media_parser.core.utils import extract_json_url
from parser_logic import find_match, message_text


class XhsLinkTests(unittest.TestCase):
    def test_share_card_and_plain_note_without_query_match(self):
        note = '66aabbccddeeff0011223344'
        links = [
            'https://xhslink.com/a/Example123',
            f'https://www.xiaohongshu.com/explore/{note}',
            f'https://www.xiaohongshu.com/discovery/item/{note}?xsec_token=abc',
        ]
        for url in links:
            with self.subTest(url=url):
                card = SimpleNamespace(type='json', data={'data':{'app':'com.tencent.tuwen.lua','meta':{'news':{'jumpUrl':url}}}})
                event = SimpleNamespace(self_id=10, original_message=[card], reply=None)
                text = message_text(event, extract_json_url)
                self.assertIsNotNone(find_match(text, XHSParser._key_patterns))


if __name__ == '__main__':
    unittest.main()
