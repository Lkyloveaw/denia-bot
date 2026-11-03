# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。
import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import epic_free_logic as logic

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
NOW = datetime(2026, 10, 31, 3, 14, tzinfo=timezone.utc)


def fixture(name):
    return json.loads((FIXTURES / name).read_text('utf-8'))


def elements(name):
    return fixture(name)['data']['Catalog']['searchStore']['elements']


def contains(games, needle):
    return any(needle in game['title'] for game in games)


def games(*ids):
    return [
        {
            'id': game_id,
            'start': datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc),
            'start_iso': '2026-10-29T15:00:00+00:00',
        }
        for game_id in ids
    ]


class EpicFreeLogicTest(unittest.TestCase):
    def test_cn_current_free_is_one_and_uses_window(self):
        free = logic.select_free(elements('epic_free_cn.json'), NOW)
        self.assertEqual(len(free), 1)
        self.assertTrue(contains(free, 'Sorry We’re Closed'))
        self.assertFalse(contains(free, 'Idle Champions'))
        self.assertFalse(contains(free, 'Eternal Threads'))
        self.assertFalse(contains(free, 'Monument Valley'))

    def test_reversed_offer_order_does_not_change_result(self):
        normal = logic.select_free(elements('epic_free_cn.json'), NOW)
        reversed_free = logic.select_free(elements('epic_free_cn_reversed.json'), NOW)
        self.assertEqual([game['id'] for game in normal], [game['id'] for game in reversed_free])
        self.assertEqual(logic.dedupe_key(normal), logic.dedupe_key(reversed_free))

    def test_us_region_free_and_discount_entry(self):
        free = logic.select_free(elements('epic_free_us.json'), NOW)
        self.assertEqual(len(free), 1)
        self.assertTrue(contains(free, 'Sorry We’re Closed'))
        self.assertFalse(contains(free, 'Castlevania'))

    def test_upcoming_is_filtered_and_limited(self):
        upcoming = logic.select_upcoming(elements('epic_free_cn.json'), NOW, limit=3)
        self.assertEqual(len(upcoming), 2)
        self.assertTrue(all(game['start'] > NOW for game in upcoming))
        self.assertTrue(contains(upcoming, 'Idle Champions'))
        self.assertEqual(len(logic.select_upcoming(elements('epic_free_cn.json'), NOW, limit=1)), 1)

    def test_permanent_free_without_window_is_not_reported(self):
        free = logic.select_free(elements('epic_free_cn.json'), NOW)
        self.assertFalse(contains(free, 'Idle Champions'))

    def test_boundary_start_and_end_are_inclusive(self):
        element = {
            'id': 'x',
            'title': 'X',
            'promotions': {'promotionalOffers': [{'promotionalOffers': [
                {'startDate': '2026-10-31T03:14:00.000Z', 'endDate': '2026-10-31T04:14:00.000Z',
                 'discountSetting': {'discountPercentage': 0}},
            ]}]},
        }
        self.assertEqual(len(logic.select_free([element], NOW)), 1)
        self.assertEqual(len(logic.select_free([element], NOW + timedelta(hours=2))), 0)

    def test_dedupe_key_changes_when_window_changes(self):
        free = logic.select_free(elements('epic_free_cn.json'), NOW)
        key = logic.dedupe_key(free)
        changed = copy.deepcopy(free)
        changed[0]['start'] = changed[0]['start'] + timedelta(days=7)
        changed[0]['start_iso'] = changed[0]['start'].astimezone(timezone.utc).isoformat()
        self.assertNotEqual(key, logic.dedupe_key(changed))
        self.assertEqual(logic.dedupe_key([]), '')

    def test_should_push(self):
        first = games('a')
        both = games('a', 'b')
        self.assertFalse(logic.should_push(None, first))
        self.assertFalse(logic.should_push([], first))
        self.assertFalse(logic.should_push(first, games('a')))
        self.assertFalse(logic.should_push(both, first))  # 纯下架，不推
        self.assertTrue(logic.should_push(both, games('a', 'c')))
        self.assertTrue(logic.should_push(first, both))
        self.assertFalse(logic.should_push(first, []))

    def test_should_push_key(self):
        self.assertFalse(logic.should_push_key(None, 'k'))
        self.assertFalse(logic.should_push_key('', 'k'))
        self.assertFalse(logic.should_push_key('k', 'k'))
        self.assertTrue(logic.should_push_key('old', 'k'))
        self.assertFalse(logic.should_push_key('k', ''))

    def test_text_fallback_has_required_fields(self):
        free = logic.select_free(elements('epic_free_cn.json'), NOW)
        upcoming = logic.select_upcoming(elements('epic_free_cn.json'), NOW)
        text = logic.format_text(free, upcoming)
        self.assertIn('Sorry We’re Closed', text)
        self.assertIn('免费', text)
        self.assertIn('北京时间', text)
        self.assertIn('https://store.epicgames.com/zh-CN/p/sorry-were-closed-724c86', text)
        self.assertNotIn('/p/null', text)

    def test_text_empty_state(self):
        text = logic.format_text([], [])
        self.assertIn('本周暂无免费游戏', text)
        self.assertIn('https://store.epicgames.com/zh-CN/', text)

    def test_link_falls_back_without_page_slug(self):
        element = {'id': 'x', 'title': 'X', 'promotions': {'promotionalOffers': [{'promotionalOffers': [
            {'startDate': '2026-10-29T15:00:00.000Z', 'endDate': '2026-11-05T16:00:00.000Z',
             'discountSetting': {'discountPercentage': 0}},
        ]}]}}
        free = logic.select_free([element], NOW)
        self.assertEqual(free[0]['url'], 'https://store.epicgames.com/zh-CN/')

    def test_links_message_contains_all_links_in_one_string(self):
        free = logic.select_free(elements('epic_free_cn.json'), NOW)
        upcoming = logic.select_upcoming(elements('epic_free_cn.json'), NOW)
        text = logic.format_links(free, upcoming)
        self.assertIn('【EPIC 领取链接】', text)
        self.assertIn('【即将免费】', text)
        for game in free + upcoming:
            self.assertIn(game['url'], text)
        self.assertEqual(text.count('https://store.epicgames.com'), 3)
        self.assertEqual(logic.format_links([], []), '')
        only_free = logic.format_links(free, upcoming, include_upcoming=False)
        self.assertNotIn('即将免费', only_free)

    def test_parse_datetime_variants(self):
        for value in ('2026-10-29T15:00:00.000Z', '2026-10-29T15:00:00Z', '2026-10-29T15:00:00+00:00'):
            self.assertEqual(logic.parse_datetime(value).isoformat(), '2026-10-29T15:00:00+00:00')
        self.assertIsNone(logic.parse_datetime(''))
        self.assertIsNone(logic.parse_datetime('not-a-date'))

    def test_build_view(self):
        view = logic.build_view(elements('epic_free_cn.json'), NOW)
        self.assertEqual(len(view['free']), 1)
        self.assertEqual(len(view['upcoming']), 2)
        self.assertEqual(len(view['key']), 64)

    def test_description_truncation(self):
        self.assertEqual(logic.truncate('x' * 100, 10), 'x' * 9 + '…')
        self.assertEqual(logic.truncate(' short ', 10), 'short')


if __name__ == '__main__':
    unittest.main()
