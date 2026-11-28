"""Exercise both Xiaohongshu page formats through their public parser entry points."""
import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from media_parser.core.parsers.xhs import XHSParser
from media_parser.core.render import Renderer


async def parse_note(entry, **fields):
    desktop = entry == 'explore'
    nickname_key = 'nickname' if desktop else 'nickName'
    note = {'type': 'normal', 'title': '出游', 'desc': '', 'user': {nickname_key: '作者', 'avatar': ''}, **fields}
    state = ({'note': {'noteDetailMap': {'example': {'note': note}}}} if desktop else
             {'noteData': {'data': {'noteData': note}}})
    html = '<script>window.__INITIAL_STATE__=' + json.dumps(state, ensure_ascii=False) + '</script>'
    parser = object.__new__(XHSParser)
    parser.headers = parser.ios_headers = {}
    parser._session = MagicMock(closed=False)
    response = parser._session.get.return_value.__aenter__.return_value
    response.text = AsyncMock(return_value=html)
    if desktop:
        return await parser.parse_explore('https://example.invalid/explore/example', 'example')
    return await parser.parse_discovery('https://example.invalid/discovery/item/example')


class XhsNoteTest(unittest.IsolatedAsyncioTestCase):
    async def test_text_displays_topics_and_known_stickers_and_keeps_literal_brackets(self):
        raw = '周末[喝奶茶R]🥰\n\n#摄影[话题]# #周末出游[话题]#\n[话题] [HDR] [未知R] #普通标签#'
        expected = '周末🧋🥰\n\n#摄影# #周末出游#\n[话题] [HDR] [未知R] #普通标签#'
        for entry in ('explore', 'discovery'):
            with self.subTest(entry=entry):
                note = await parse_note(entry, desc=raw)
                self.assertEqual(note.text, expected)
                self.assertIn(expected, await Renderer(None).build_html(note))

    async def test_publication_time_reaches_card_in_seconds_from_both_pages(self):
        seconds = 1770000000
        displayed = datetime.fromtimestamp(seconds).strftime('%Y-%m-%d %H:%M:%S')
        for entry in ('explore', 'discovery'):
            for value in (seconds * 1000 + 567, seconds):
                with self.subTest(entry=entry, value=value):
                    note = await parse_note(entry, time=value, lastUpdateTime=(seconds + 86400) * 1000)
                    self.assertEqual(note.timestamp, seconds)
                    self.assertIn(displayed, await Renderer(None).build_html(note))

    async def test_optional_date_never_discards_a_note_or_shows_the_epoch(self):
        for entry in ('explore', 'discovery'):
            for fields in ({}, {'time': 0}, {'time': None}, {'time': 'n/a'}, {'time': -1}):
                with self.subTest(entry=entry, fields=fields):
                    note = await parse_note(entry, **fields)
                    self.assertEqual(note.title, '出游')
                    self.assertIsNone(note.timestamp)
                    self.assertNotIn('1970-', await Renderer(None).build_html(note))
