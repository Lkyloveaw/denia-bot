import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import game_event_logic as g


def test_session_policy_matches_parser_and_blacklist_wins():
    cfg = {'whitelist': ['group:123', 'private:456'], 'blacklist': ['group:123']}
    assert not g.allowed(cfg, 'group', '123')
    assert not g.allowed(cfg, 'group', '999')
    assert g.allowed(cfg, 'private', '456')
    assert g.allowed({}, 'group', '999')
    assert not g.allowed({'private_enabled': False}, 'private', '456')
    assert not g.allowed({'group_enabled': False}, 'group', '123')


def test_aliases_expand_regions_and_unknown_input_is_rejected():
    assert g.resolve_games(['鸣潮', 'ww']) == ['ww']
    assert g.resolve_games(['蔚蓝档案']) == ['ba-cn', 'ba-global', 'ba-jp']
    with pytest.raises(ValueError):
        g.resolve_games(['鸣潮', '不存在'])


def test_commands_do_not_capture_casual_speech():
    assert g.parse_command('/活动 原神') == ('query', '原神')
    assert g.parse_command('活动订阅 原神') == ('subscribe', '原神')
    assert g.parse_command('.game_query ww') == ('query', 'ww')
    for text in ['活动安排得不错', '活动 原神', 'game_query ww', '知道了', '已知晓']:
        assert g.parse_command(text) is None


def test_times_are_aware_and_invalid_records_are_not_events():
    assert g.parse_time('2026-10-31T08:00:00') == g.parse_time('2026-10-31T00:00:00Z')
    assert g.parse_time(1793404800000) == 1793404800
    assert g.event_record('ww', '常驻', None, None) is None
    assert g.event_record('ww', '倒置', 200, 100) is None
    one = g.event_record('ww', '<b>活动</b>', 100, 200)
    two = g.event_record('ww', '<b>活动</b>', 100, 300)
    assert one['uid'] == two['uid'] and one['name'] == '活动'


@pytest.mark.parametrize('identity', ['', 'source-event-100'])
def test_missing_start_deadline_extension_keeps_identity_and_notification_history(identity):
    original = g.event_record('ww', '限时活动', None, 1000 + g.DAY, identity=identity)
    extended = g.event_record('ww', '限时活动', None, 2000 + g.DAY, identity=identity)
    assert extended['uid'] == original['uid']
    states = {original['uid']: {'new_done': True, 'tier': 1}}
    assert g.plans([extended], states, {}, 2000) == []


def test_only_most_urgent_tier_is_sent_and_success_does_not_repeat():
    event = g.event_record('ww', '测试', 1, 1000 + 5 * 3600)
    plan = g.plans([event], {}, {}, 1000)[0]
    assert plan['reasons'] == ['new', 'd1']
    assert g.plans([event], {event['uid']: {'new_done': True, 'tier': 1}}, {}, 2000) == []


def test_future_filter_expiry_and_exact_boundary():
    event = g.event_record('ww', '寻访', 1, 86400, tags=['卡池'])
    assert g.plans([event], {}, {'exclude_keywords': ['卡池']}, 100) == []
    assert g.plans([event], {}, {}, 86400) == []
    assert g.plans([event], {}, {}, 0)[0]['tier'] is None  # 未开始
    event['start'] = None
    assert g.plans([event], {}, {}, 0)[0]['tier'] == 1
    assert g.plans([event], {}, {'thresholds': [], 'notify_new': False}, 0) == []


def test_thresholds_validate_instead_of_silently_disabling():
    assert g.thresholds('7 3 1 3') == [7, 3, 1]
    assert g.thresholds('关闭') == []
    for invalid in ['oops', '7 -1', [True], [1.5], '366']:
        with pytest.raises(ValueError):
            g.thresholds(invalid)
