import sys
import time
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard
import game_event_store


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'AUTH_FILE', tmp_path / 'auth.json')
    monkeypatch.setattr(dashboard, 'session_store_path', lambda: tmp_path / 'sessions.json')
    monkeypatch.setattr(dashboard, 'sessions', {})
    monkeypatch.setattr(dashboard, 'attempts', {})
    monkeypatch.setattr(game_event_store, 'PATH', tmp_path / 'events.sqlite3')
    async def no_network(*args, **kwargs):
        pytest.fail('the panel must not fetch remote sources or send messages')
    monkeypatch.setattr(httpx.AsyncClient, 'send', no_network)
    dashboard.set_password('game-calendar-test-password')
    with TestClient(dashboard.app) as client:
        yield client, game_event_store.Store()


def login(client):
    response = client.post('/api/login', json={'password': 'game-calendar-test-password'})
    assert response.status_code == 200
    return {'Origin': 'http://testserver', 'X-CSRF-Token': response.json()['csrf']}


def subscription(**changes):
    return {'bot_id': '10001', 'kind': 'group', 'target_id': '20001', 'games': ['sr'], **changes}


def cached_event(**changes):
    return {'uid': 'event-one', 'game_id': 'sr', 'name': '[CQ:at,qq=all]<img src=x>',
            'start': time.time() - 100, 'end': time.time() + 3600,
            'description': '活动介绍', 'cover': 'file:///secret', 'url': 'https://example.com/detail',
            'tags': ['剧情'], 'extra_times': [], **changes}


def test_every_game_event_endpoint_requires_login_and_writes_require_csrf(panel):
    client, store = panel
    assert client.get('/api/game-events').status_code == 401
    assert client.get('/api/game-events/preview?game_id=sr').status_code == 401
    paths = ['/settings', '/subscriptions', '/subscriptions/missing/delete', '/urgent',
             '/urgent/10001/30001/delete', '/refresh']
    for path in paths:
        assert client.post('/api/game-events' + path, json={}).status_code == 401
    assert not store.path.exists()
    login(client)
    for path in paths:
        assert client.post('/api/game-events' + path, json={}).status_code == 403


def test_settings_patch_uses_revision_and_preserves_unrelated_fields(panel):
    client, store = panel
    headers = login(client)
    view = client.get('/api/game-events').json()
    assert view['settings']['thresholds'] == [7, 3, 1]
    assert len(view['games']) == 10
    payload = {'revision': view['settings']['revision'], 'settings': {
        'group_enabled': False, 'private_enabled': True,
        'whitelist': ['group:20001', 'private:30001'], 'blacklist': ['group:20001']}}
    saved = client.post('/api/game-events/settings', json=payload, headers=headers)
    assert saved.status_code == 200
    assert saved.json()['settings']['group_enabled'] is False
    assert store.settings()['poll_minutes'] == 30
    assert client.post('/api/game-events/settings', json=payload, headers=headers).status_code == 409
    assert store.settings()['blacklist'] == ['group:20001']


@pytest.mark.parametrize('payload', [
    [], {'revision': 0, 'settings': {'whitelist': ['bad']}},
    {'revision': 0, 'settings': {'group_enabled': 'false'}},
    {'revision': 0, 'settings': {'poll_minutes': 1}},
    {'revision': 0, 'settings': {'unknown_setting': True}},
])
def test_invalid_settings_are_rejected_without_changing_config(panel, payload):
    client, store = panel
    headers = login(client)
    response = client.post('/api/game-events/settings', json=payload, headers=headers)
    assert response.status_code == 400
    assert store.settings()['revision'] == 0


def test_subscription_crud_preserves_paused_targets_and_requires_fresh_revision(panel):
    client, store = panel
    headers = login(client)
    store.save_settings({'blacklist': ['group:20001']}, 0)
    created = client.post('/api/game-events/subscriptions', json=subscription(
        thresholds=[3, 1], include_keywords=['剧情'], exclude_keywords=['卡池'], cover_mode='link'), headers=headers)
    assert created.status_code == 200
    sub = created.json()['subscription']
    row = client.get('/api/game-events').json()['subscriptions'][0]
    assert row['paused'] is True and row['access_allowed'] is False
    assert row['game_states']['sr']['status'] == 'initializing'
    assert row['cover_mode'] == 'link'
    saved = client.post('/api/game-events/subscriptions', json={'id': sub['id'], 'revision': sub['revision'], 'enabled': False}, headers=headers)
    assert saved.status_code == 200
    assert client.post('/api/game-events/subscriptions', json={'id': sub['id'], 'revision': sub['revision'], 'enabled': True}, headers=headers).status_code == 409
    assert client.post('/api/game-events/subscriptions/' + sub['id'] + '/delete', json={'revision': sub['revision']}, headers=headers).status_code == 409
    assert client.post('/api/game-events/subscriptions/' + sub['id'] + '/delete', json={'revision': saved.json()['subscription']['revision']}, headers=headers).status_code == 200
    assert store.subscriptions() == []


def test_empty_whitelist_allows_and_blacklist_always_wins(panel):
    client, store = panel
    login(client)
    store.save_subscription(subscription())
    assert client.get('/api/game-events').json()['subscriptions'][0]['access_allowed'] is True
    store.save_settings({'whitelist': ['group:20001'], 'blacklist': ['group:20001']}, 0)
    assert client.get('/api/game-events').json()['subscriptions'][0]['access_allowed'] is False


def test_preview_uses_only_cached_events_and_filters_without_rendering_images(panel):
    client, store = panel
    login(client)
    sub = store.save_subscription(subscription(include_keywords=['剧情']))
    events = [cached_event(), cached_event(uid='second', name='其他', tags=[], description='其他')]
    store.save_snapshot({'game_id': 'sr', 'source': 'sra', 'activities': events, 'version': 'v1',
                         'checked_at': time.time(), 'fetched_at': time.time() - 30, 'stale': True,
                         'complete': True, 'error': 'temporary error'})
    result = client.get('/api/game-events/preview', params={'game_id': 'sr', 'subscription_id': sub['id']})
    assert result.status_code == 200
    data = result.json()
    assert len(data['events']) == 1
    assert data['events'][0]['name'] == '[CQ:at,qq=all]<img src=x>'
    assert data['events'][0]['cover'] == ''
    assert data['events'][0]['url'] == 'https://example.com/detail'
    assert data['stale'] is True
    assert data['fetched_at'] > 0
    assert client.get('/api/game-events/preview?game_id=unknown').status_code == 400
    assert client.get('/api/game-events/preview?game_id=sr&subscription_id=missing').status_code == 404


def test_refresh_is_only_enqueued_and_duplicate_does_not_create_another_job(panel):
    client, store = panel
    headers = login(client)
    first = client.post('/api/game-events/refresh', json={'mode': 'data', 'games': ['sr']}, headers=headers)
    assert first.status_code == 202
    assert first.json()['job']['status'] == 'pending'
    second = client.post('/api/game-events/refresh', json={'mode': 'data', 'games': ['sr']}, headers=headers)
    assert second.json()['job']['id'] == first.json()['job']['id']
    job = store.claim_job()
    assert job['games'] == ['sr'] and job['mode'] == 'data'
    store.finish_job(job['id'], result={'fetched': ['sr']})
    view = client.get('/api/game-events').json()
    assert view['jobs'][0]['status'] == 'succeeded'
    assert client.post('/api/game-events/refresh', json={'mode': 'data'}, headers=headers).status_code == 409


def test_check_request_requires_explicit_bot_and_never_sends_in_panel(panel):
    client, store = panel
    headers = login(client)
    assert client.post('/api/game-events/refresh', json={'mode': 'check'}, headers=headers).status_code == 400
    response = client.post('/api/game-events/refresh', json={'mode': 'check', 'bot_id': '10001'}, headers=headers)
    assert response.status_code == 202
    assert store.claim_job()['bot_id'] == '10001'
    assert store.deliveries() == []


def test_different_pending_refresh_scope_returns_conflict_instead_of_losing_intent(panel):
    client, store = panel
    headers = login(client)
    first = client.post('/api/game-events/refresh', json={'mode': 'data', 'games': ['sr']}, headers=headers)
    assert first.status_code == 202
    conflicting = client.post('/api/game-events/refresh', json={'mode': 'check', 'games': ['ww'], 'bot_id': '10001'}, headers=headers)
    assert conflicting.status_code == 409
    assert len(store.jobs()) == 1 and store.jobs()[0]['mode'] == 'data'


def test_urgent_crud_and_origin_policy_are_independent_of_ordinary_subscription(panel):
    client, store = panel
    headers = login(client)
    store.save_settings({'urgent_enabled': True, 'blacklist': ['group:20001']}, 0)
    payload = {'bot_id': '10001', 'user_id': '30001', 'games': ['sr'], 'origin_session': 'group:20001'}
    response = client.post('/api/game-events/urgent', json=payload, headers=headers)
    assert response.status_code == 200
    saved = response.json()['target']
    row = client.get('/api/game-events').json()['urgent_targets'][0]
    assert row['paused'] is True
    assert row['access_allowed'] is False
    assert store.subscriptions() == []
    response = client.post('/api/game-events/urgent', json={**payload, 'revision': saved['revision'], 'enabled': False}, headers=headers)
    assert response.status_code == 200
    assert client.post('/api/game-events/urgent', json={**payload, 'revision': saved['revision']}, headers=headers).status_code == 409
    path = '/api/game-events/urgent/10001/30001/delete'
    assert client.post(path, json={'revision': response.json()['target']['revision']}, headers=headers).status_code == 200
    assert store.urgent_targets() == []


def test_overview_exposes_cache_delivery_and_recent_runtime_without_remote_calls(panel):
    client, store = panel
    login(client)
    now = time.time()
    store.set_runtime(['10001'], now)
    store.save_snapshot({'game_id': 'sr', 'source': 'sra', 'activities': [cached_event(), cached_event(uid='old', end=now - 10)],
                         'checked_at': now, 'fetched_at': now - 50, 'version': 'v1', 'stale': True, 'complete': False,
                         'error': 'https://user:secret@proxy.example.com/?token=private-token'})
    store.log_delivery('10001', 'group', '20001', 'sr', 'unknown', 'timeout')
    data = client.get('/api/game-events').json()
    source = next(row for row in data['sources'] if row['game_id'] == 'sr')
    assert source['valid_count'] == 1 and source['expired_count'] == 1
    assert source['complete'] is False and source['stale'] is True
    assert 'secret' not in source['error'] and 'private-token' not in source['error']
    assert data['runtime']['bots'] == ['10001']
    assert data['runtime']['possibly_offline'] is False
    assert data['delivery_counts']['unknown'] == 1
    assert data['deliveries'][0]['detail'] == 'timeout'


def test_dynamic_panel_rows_render_external_values_as_literal_text(panel):
    client, store = panel
    login(client)
    label = '<img src=x onerror=alert(1)>'
    store.save_settings({'aliases': {'sr': label}}, 0)
    store.save_subscription(subscription())
    data = client.get('/api/game-events').json()
    script = r'''
const fs=require('fs'),vm=require('vm');
const elements=new Map();
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.value='';this.dataset={};this.style={};this.classList={toggle(){}};this._text=''}
  set textContent(value){this._text=String(value);this.children=[]}
  get textContent(){return this._text+this.children.map(c=>typeof c==='string'?c:c.textContent).join('')}
  set innerHTML(value){throw Error('External row used HTML rendering')}
  append(...values){this.children.push(...values)}
  replaceChildren(...values){this._text='';this.children=[...values]}
  addEventListener(){}
  setAttribute(name,value){this[name]=value}
}
const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},createElement(tag){return new Element(tag)},createTextNode(text){return String(text)},querySelector(){return new Element('a')},querySelectorAll(){return []}};
const context={document,fetch:async()=>({ok:false}),console,URL,URLSearchParams,setTimeout(){return 1},clearTimeout(){},confirm(){return true}};
vm.createContext(context);
const html=fs.readFileSync(process.argv[1],'utf8');
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
context.gameRenderRows(JSON.parse(process.argv[2]));
context.gameEditSubscription(JSON.parse(process.argv[2]).subscriptions[0]);
if(!document.getElementById('gameSubBot').disabled||!document.getElementById('gameSubKind').disabled||!document.getElementById('gameSubTarget').disabled)throw Error('Existing subscription identity is editable');
context.gameEditSubscription();
if(document.getElementById('gameSubBot').disabled||document.getElementById('gameSubKind').disabled||document.getElementById('gameSubTarget').disabled)throw Error('New subscription identity is locked');
context.api=async()=>({cached:true,events:[{name:'<svg onload=alert(2)>',description:'<script>alert(3)</script>',tags:[],extra_times:[],url:'https://example.com',cover:''}],total:1,access_allowed:true});
context.gamePreview().then(()=>console.log(JSON.stringify({text:[...elements.values()].map(e=>e.textContent).join('\n')}))).catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', script, str(Path(dashboard.__file__).with_name('dashboard.html')), json.dumps(data)],
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    assert label in json.loads(result.stdout)['text']
    assert '<svg onload=alert(2)>' in json.loads(result.stdout)['text']
    assert '<script>alert(3)</script>' in json.loads(result.stdout)['text']


def test_previous_snapshot_is_opt_in_diagnostic_and_never_current_activity(panel):
    client, store = panel
    login(client)
    now = time.time()
    previous = {'game_id': 'sr', 'activities': [cached_event(end=now - 100)],
                'fetched_at': now - 1000, 'checked_at': now - 1000, 'complete': True, 'stale': False}
    store.save_snapshot(previous)
    store.save_snapshot({'game_id': 'sr', 'activities': [], 'fetched_at': now, 'checked_at': now,
                         'complete': True, 'stale': False,
                         'warning': '来源只包含已过期活动'})
    current = client.get('/api/game-events/preview?game_id=sr').json()
    assert current['events'] == []
    historical = client.get('/api/game-events/preview?game_id=sr&diagnostic=true').json()
    assert historical['diagnostic'] is True
    assert len(historical['events']) == 1
    assert historical['events'][0]['end'] < now
    assert historical['fetched_at'] == previous['fetched_at']
    overview = client.get('/api/game-events').json()
    source = next(row for row in overview['sources'] if row['game_id'] == 'sr')
    assert source['valid_count'] == 0 and source['has_previous'] is True
    assert '过期' in source['warning']


def test_subscription_overview_exposes_progress_without_repeating_snapshot_contents(panel):
    client, store = panel
    login(client)
    row = store.save_subscription(subscription())
    state = store.game_state(row['id'], 'sr')
    store.begin_initial(row['id'], 'sr', state['generation'], [cached_event()])
    current = client.get('/api/game-events').json()['subscriptions'][0]['game_states']['sr']
    assert current['initial_count'] == 1 and current['sent_count'] == 0
    assert 'initial' not in current and 'sent' not in current


def test_malformed_json_and_invalid_subscription_do_not_escape_as_server_errors(panel):
    client, store = panel
    headers = login(client)
    assert client.post('/api/game-events/settings', content='{', headers={**headers, 'Content-Type': 'application/json'}).status_code == 400
    assert client.post('/api/game-events/subscriptions', json=subscription(bot_id='bad'), headers=headers).status_code == 400
    assert client.post('/api/game-events/subscriptions', json=subscription(games=['invalid']), headers=headers).status_code == 400
    assert store.subscriptions() == []
