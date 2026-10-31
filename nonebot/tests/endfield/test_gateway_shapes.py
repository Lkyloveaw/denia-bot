"""Gateway field contracts verified against the upstream integration source."""
from endfield import logic


def test_note_uses_base_summary():
    text=logic.format_note({'base':{'name':'测试干员','level':42,'worldLevel':5,
        'mainMission':{'description':'前往武陵'}},'chars':[{'name':'陈'}],'achieve':{'count':7}})
    for value in ['测试干员','42','5','前往武陵','7']:
        assert value in text


def test_domain_list_reports_settlement_tickets():
    text=logic.format_domain({'domain':[{'name':'四号谷地','level':3,
        'settlements':[{'name':'源石研究园','remainMoney':25,'moneyMax':50}]}]})
    assert '四号谷地' in text and '源石研究园' in text and '25/50' in text


def test_achievement_medals_are_not_discarded():
    text=logic.format_achievements({'achieve':{'count':4,'achieveMedals':[
        {'level':2,'isPlated':True,'achievementData':{'name':'开拓之路'}}]}})
    assert '开拓之路' in text and '镀层' in text


def test_global_stats_uses_total_pulls():
    text=logic.format_global_stats({'stats':{'total_pulls':1000,'total_users':20,'star6_total':33}})
    assert '1000' in text and '20' in text and '33' in text


def test_announcement_caption_orders_text_blocks():
    text=logic.format_announcement_latest({'title':'公告','texts':[
        {'id':'b','content':'第二段'},{'id':'a','content':'第一段'}],
        'caption':[{'type':'text','id':'a'},{'type':'newline'},{'type':'text','id':'b'}]})
    assert '第一段\n第二段' in text


def test_calendar_dates_are_readable_and_expired_events_excluded():
    text=logic.format_calendar({'activities':[
        {'name':'过期活动','activity_end_at_ts':100},
        {'name':'新活动','activity_start_at_ts':1793426400,'activity_end_at_ts':1793512800},
    ]},now=1793426400)
    assert '过期活动' not in text
    assert '2026-10-31' in text and '2026-11-01' in text
    assert '1793426400' not in text
