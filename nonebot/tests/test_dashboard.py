import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import dashboard as d
import memory_logic as memory
import group_topics_logic as topics
import weather_service as weather
import master_settings
import parser_settings
import bot_identity
import log_viewer
from unittest.mock import AsyncMock, Mock, patch

class DashboardTest(unittest.TestCase):
    def test_parser_arbitration_setting_is_authorized_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(parser_settings, 'PATH', Path(tmp)/'parser.json'):
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                login = client.post('/api/login', json={'password':'a-strong-test-password'})
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                data = client.get('/api/parser').json()
                self.assertTrue(data['general']['arbiter'])
                update = {'version':data['version'], 'general':{'arbiter':False}}
                self.assertEqual(client.post('/api/parser', json=update).status_code, 403)
                self.assertEqual(client.post('/api/parser', json=update, headers=headers).status_code, 200)
                self.assertFalse(parser_settings.load()['arbiter'])
                self.assertFalse(client.get('/api/parser').json()['general']['arbiter'])

    def test_failed_restart_unlocks_only_after_new_process_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            controller=SimpleNamespace(running=True,error='failed')
            def ready(): controller.running=False; controller.error=''
            controller.observe_ready=Mock(side_effect=ready)
            monitor=SimpleNamespace(status=AsyncMock(return_value={'bot':{},'snowluma':{},'rag':{}}), bot_health=AsyncMock(return_value={'state':'ready','pid':1,'start_token':'old'}))
            worker=SimpleNamespace(available=True,expected_token='new',previous_pid=1,previous_state='ready')
            with patch.object(d,'monitor',monitor), patch.object(d,'restart_worker',worker), patch.object(d,'restart_controller',controller), TestClient(d.app) as client:
                client.post('/api/login',json={'password':'a-strong-test-password'})
                self.assertTrue(client.get('/api/bot-status').json()['restart']['running'])
                monitor.bot_health.return_value={'state':'ready','pid':2,'start_token':None}
                self.assertFalse(client.get('/api/bot-status').json()['restart']['running'])
    def test_restart_requires_login_and_csrf_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                self.assertEqual(client.get('/api/bot-status').status_code,401)
                login=client.post('/api/login',json={'password':'a-strong-test-password'})
                self.assertEqual(client.post('/api/bot-restart').status_code,403)
                headers={'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                with patch.object(d.restart_controller,'start',new_callable=AsyncMock,return_value=False):
                    self.assertEqual(client.post('/api/bot-restart',headers=headers).status_code,409)
    def test_bot_name_settings_require_login_csrf_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp) / 'auth'
            d.set_password('a-strong-test-password')
            target = Path(tmp) / 'bot-name.json'
            with patch.object(bot_identity, 'PATH', target), \
                    patch.object(bot_identity, 'get_private', lambda key: ''):
                with TestClient(d.app) as client:
                    self.assertEqual(client.get('/api/bot-name').status_code, 401)
                    login = client.post('/api/login', json={'password': 'a-strong-test-password'})
                    headers = {'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']}
                    self.assertEqual(client.get('/api/bot-name').json()['name'], '达妮娅')
                    self.assertEqual(client.post('/api/bot-name', json={'name': '泡泡机'}).status_code, 403)
                    saved = client.post('/api/bot-name', json={'name': '泡泡机'}, headers=headers)
                    self.assertEqual(saved.status_code, 200)
                    self.assertEqual(saved.json()['source'], 'file')
                    self.assertEqual(client.get('/api/bot-name').json()['name'], '泡泡机')
                    self.assertTrue(target.exists())
                    self.assertEqual(client.post('/api/bot-name', json={'name': ''}, headers=headers).status_code, 400)
                    reset = client.post('/api/bot-name', json={'reset': True}, headers=headers)
                    self.assertEqual(reset.status_code, 200)
                    self.assertEqual(reset.json()['source'], 'default')
                    self.assertFalse(target.exists())

    def test_parser_settings_auth_validation_and_secret_preservation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(parser_settings, 'PATH', Path(tmp)/'parser.json'):
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                self.assertEqual(client.get('/api/parser').status_code, 401)
                login = client.post('/api/login', json={'password':'a-strong-test-password'})
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                data = client.get('/api/parser').json()
                self.assertIn('bilibili', data['platforms'])
                update = {'version':data['version'], 'general':{'debounce_interval':60}, 'platforms':{'bilibili':{'cookies':'private-cookie', 'enable':True}}}
                self.assertEqual(client.post('/api/parser', json=update).status_code, 403)
                self.assertEqual(client.post('/api/parser', json=update, headers=headers).status_code, 200)
                data = client.get('/api/parser').json()
                self.assertNotIn('private-cookie', str(data))
                self.assertTrue(data['secrets']['bilibili.cookies'])
                update = {'version':data['version'], 'general':{'debounce_interval':30}, 'platforms':{'bilibili':{'cookies':''}}}
                self.assertEqual(client.post('/api/parser', json=update, headers=headers).status_code, 200)
                self.assertEqual(parser_settings.load()['parsers_template'][0]['cookies'], 'private-cookie')
                self.assertEqual(client.post('/api/parser', json=update, headers=headers).status_code, 409)
                data = client.get('/api/parser').json()
                self.assertEqual(client.post('/api/parser', json={'version':data['version'],'general':{'source_max_size':-1}}, headers=headers).status_code, 400)
                self.assertEqual(client.post('/api/parser', json={'version':data['version'],'general':{'whitelist':['invalid']}}, headers=headers).status_code, 400)
                self.assertEqual(client.post('/api/parser', json={'version':data['version'],'general':{'clean_cron':''}}, headers=headers).status_code, 200)
                data = client.get('/api/parser').json()
                self.assertEqual(client.post('/api/parser', json={'version':data['version'],'clear_secrets':['bilibili.cookies']}, headers=headers).status_code, 200)
                self.assertFalse(client.get('/api/parser').json()['secrets']['bilibili.cookies'])
    def test_parser_payload_exposes_baseline_and_platform_summary(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(parser_settings, 'PATH', Path(tmp)/'parser.json'):
            d.AUTH_FILE = Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                login = client.post('/api/login', json={'password':'a-strong-test-password'})
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                data = client.get('/api/parser').json()
                total = len(data['platforms'])
                # 基线用于前端判断「改没改过」，必须覆盖每个平台
                self.assertEqual(set(data['baseline']), set(data['platforms']))
                self.assertIn('enable', data['baseline']['bilibili'])
                self.assertEqual(data['summary']['total'], total)
                self.assertEqual(data['summary']['enabled'], len(data['summary']['names']))
                # 关掉一个平台后，总览要跟着变
                target = data['summary']['names'][0]
                update = {'version':data['version'], 'platforms':{target:{'enable':False}}}
                self.assertEqual(client.post('/api/parser', json=update, headers=headers).status_code, 200)
                after = client.get('/api/parser').json()
                self.assertEqual(after['summary']['enabled'], data['summary']['enabled'] - 1)
                self.assertNotIn(target, after['summary']['names'])
                self.assertEqual(after['summary']['total'], total)
    def test_summary_counts_only_explicit_true(self):
        platforms = {'a':{'enable':True}, 'b':{'enable':False}, 'c':{}}
        result = parser_settings.summary(platforms, baseline={})
        self.assertEqual(result, {'total':3, 'enabled':1, 'names':['a']})
    def test_master_settings_require_login_and_csrf(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(master_settings,'PATH',Path(tmp)/'settings.json'):
            d.AUTH_FILE=Path(tmp)/'auth'
            d.set_password('a-strong-test-password')
            data={'owner_ids':['123456'],'title':'漂泊者'}
            with TestClient(d.app) as client:
                self.assertEqual(client.get('/api/master-aware').status_code,401)
                login=client.post('/api/login',json={'password':'a-strong-test-password'})
                self.assertEqual(client.post('/api/master-aware',json=data).status_code,403)
                headers={'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                self.assertEqual(client.post('/api/master-aware',json=data,headers=headers).status_code,200)
                self.assertEqual(client.get('/api/master-aware').json(),data)
                self.assertEqual(client.post('/api/master-aware',json={'owner_ids':['x'],'title':None},headers=headers).status_code,400)
    def test_temporary_password_requires_change_and_revokes_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp)/'auth'
            with patch.object(d, 'session_store_path', lambda: Path(tmp)/'sessions.json'):
                d.sessions.clear()
                d.attempts.clear()
                d.set_password('temporary-pass-123', must_change=True)
                with TestClient(d.app) as client:
                    login = client.post('/api/login', json={'password':'temporary-pass-123'})
                    self.assertTrue(login.json()['must_change'])
                    headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                    self.assertEqual(client.get('/api/state').status_code, 403)
                    self.assertEqual(client.post('/api/password', json={'password':'new-secret-pass-456'}, headers=headers).status_code, 409)
                    self.assertEqual(client.post('/api/setup-password', json={'password':'too-short','confirm':'too-short'}, headers=headers).status_code, 400)
                    self.assertEqual(client.post('/api/setup-password', json={'password':'temporary-pass-123','confirm':'temporary-pass-123'}, headers=headers).status_code, 400)
                    self.assertEqual(client.post('/api/setup-password', json={'password':'new-secret-pass-456','confirm':'new-secret-pass-456'}, headers=headers).status_code, 200)
                    self.assertEqual(client.get('/api/state').status_code, 401)
                    self.assertEqual(client.post('/api/login', json={'password':'temporary-pass-123'}).status_code, 401)
                    self.assertFalse(client.post('/api/login', json={'password':'new-secret-pass-456'}).json()['must_change'])

    def test_first_login_exposes_setup_state_but_blocks_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp)/'dashboard-auth.json'
            with patch.object(d, 'AUTH_FILE', auth), \
                 patch.object(d, 'session_store_path', lambda: Path(tmp)/'sessions.json'):
                d.sessions.clear()
                d.attempts.clear()
                password = d.initialize_password()
                with TestClient(d.app) as client:
                    self.assertEqual(client.get('/api/session').status_code, 401)
                    self.assertEqual(client.post('/api/setup-password', json={
                        'password':'new-secret-pass-456', 'confirm':'new-secret-pass-456'}).status_code, 401)
                    login = client.post('/api/login', json={'password':password})
                    self.assertEqual(login.status_code, 200)
                    self.assertTrue(login.json()['must_change'])
                    headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                    state = client.get('/api/session')
                    self.assertEqual(state.status_code, 200)
                    self.assertTrue(state.json()['must_change'])
                    self.assertEqual(client.get('/api/state').status_code, 403)
                    self.assertEqual(client.get('/api/bot-status').status_code, 403)
                    self.assertEqual(client.post('/api/memes/rules', json={}, headers=headers).status_code, 403)
                    # 常规改密端点不是首次设置的旁路
                    self.assertEqual(client.post('/api/password', json={
                        'password':'new-secret-pass-456'}, headers=headers).status_code, 409)
                    self.assertTrue(json.loads(auth.read_text(encoding='utf-8'))['must_change'])

    def test_setup_password_validates_and_completes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp)/'dashboard-auth.json'
            with patch.object(d, 'AUTH_FILE', auth), \
                 patch.object(d, 'session_store_path', lambda: Path(tmp)/'sessions.json'):
                d.sessions.clear()
                d.attempts.clear()
                temporary = d.initialize_password('temporary-pass-123')
                initial = auth.parent/d.INITIAL_PASSWORD_FILE
                self.assertTrue(initial.exists())
                with TestClient(d.app) as client:
                    login = client.post('/api/login', json={'password':temporary})
                    headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                    for body in (
                        {'password':'short','confirm':'short'},
                        {'password':'new-secret-pass-456','confirm':'different-pass-456'},
                        {'password':'new-secret-pass-456'},
                        {'password':temporary,'confirm':temporary},
                        {'password':'x'*257,'confirm':'x'*257},
                    ):
                        with self.subTest(body=body):
                            self.assertEqual(client.post('/api/setup-password', json=body, headers=headers).status_code, 400)
                    self.assertEqual(client.post('/api/setup-password', json={
                        'password':'new-secret-pass-456', 'confirm':'new-secret-pass-456'}).status_code, 403)
                    self.assertTrue(json.loads(auth.read_text(encoding='utf-8'))['must_change'])
                    self.assertTrue(initial.exists())

                    response = client.post('/api/setup-password', json={
                        'password':'new-secret-pass-456', 'confirm':'new-secret-pass-456'}, headers=headers)
                    self.assertEqual(response.status_code, 200)
                    saved = json.loads(auth.read_text(encoding='utf-8'))
                    self.assertFalse(saved['must_change'])
                    self.assertTrue(d.password_matches('new-secret-pass-456', saved))
                    self.assertFalse(initial.exists())
                    # 旧会话已失效，临时密码不可再用
                    self.assertEqual(client.get('/api/state').status_code, 401)
                    self.assertEqual(client.post('/api/login', json={'password':temporary}).status_code, 401)

                    fresh = client.post('/api/login', json={'password':'new-secret-pass-456'})
                    self.assertEqual(fresh.status_code, 200)
                    self.assertFalse(fresh.json()['must_change'])
                    fresh_headers = {'Origin':'http://testserver','X-CSRF-Token':fresh.json()['csrf']}
                    self.assertEqual(client.get('/api/state').status_code, 200)
                    # 完成后即使持有效会话，设置端点也必须拒绝
                    self.assertEqual(client.post('/api/setup-password', json={
                        'password':'another-pass-789', 'confirm':'another-pass-789'}, headers=fresh_headers).status_code, 409)
                    self.assertTrue(d.password_matches('new-secret-pass-456', json.loads(auth.read_text(encoding='utf-8'))))

    def test_first_run_guide_is_seeded_and_can_be_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp)/'dashboard-auth.json'
            with patch.object(d, 'AUTH_FILE', auth), \
                 patch.object(d, 'session_store_path', lambda: Path(tmp)/'sessions.json'):
                d.sessions.clear()
                d.attempts.clear()
                temporary = d.initialize_password('temporary-pass-123')
                with TestClient(d.app) as client:
                    login = client.post('/api/login', json={'password': temporary})
                    headers = {'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']}
                    # 改密前不能进入引导，也不能读取引导状态
                    self.assertEqual(client.get('/api/onboarding').status_code, 403)
                    self.assertEqual(client.post('/api/setup-password', json={
                        'password': 'new-secret-pass-456',
                        'confirm': 'new-secret-pass-456'}, headers=headers).status_code, 200)
                    self.assertTrue((Path(tmp)/'dashboard-onboarding.json').exists())

                    fresh = client.post('/api/login', json={'password': 'new-secret-pass-456'})
                    fresh_headers = {'Origin': 'http://testserver', 'X-CSRF-Token': fresh.json()['csrf']}
                    self.assertEqual(client.get('/api/onboarding').json(),
                                     {'basics_pending': True, 'basics_step': 0})
                    self.assertEqual(client.post('/api/onboarding', json={'basics_done': True}).status_code, 403)
                    self.assertEqual(client.post('/api/onboarding', json={'basics_done': False},
                                                 headers=fresh_headers).status_code, 400)
                    self.assertEqual(client.post('/api/onboarding', json={'basics_step': 99},
                                                 headers=fresh_headers).status_code, 400)
                    stepped = client.post('/api/onboarding', json={'basics_step': 2},
                                          headers=fresh_headers)
                    self.assertEqual(stepped.status_code, 200)
                    self.assertEqual(stepped.json(), {'basics_pending': True, 'basics_step': 2})
                    completed = client.post('/api/onboarding', json={'basics_done': True},
                                            headers=fresh_headers)
                    self.assertEqual(completed.status_code, 200)
                    self.assertEqual(completed.json(), {'basics_pending': False, 'basics_step': 0})
                    self.assertEqual(client.get('/api/onboarding').json(),
                                     {'basics_pending': False, 'basics_step': 0})
                    # 完成后的步骤写入不能重新打开引导
                    self.assertEqual(client.post('/api/onboarding', json={'basics_step': 3},
                                                 headers=fresh_headers).json(),
                                     {'basics_pending': False, 'basics_step': 0})

    def test_runtime_logs_are_authorized_whitelisted_and_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project/'logs').mkdir()
            (project/'logs'/'nonebot.out.log').write_text(
                'first\napi_key=secret123\nlast\n', encoding='utf-8')
            (project/'nonebot'/'logs').mkdir(parents=True)
            (project/'nonebot'/'logs'/'2026-11-22.log').write_text('daily\n', encoding='utf-8')
            d.AUTH_FILE = project/'auth'
            d.sessions.clear()
            d.attempts.clear()
            d.set_password('a-strong-test-password')
            with patch.object(log_viewer, 'PROJECT_ROOT', project), \
                 patch.object(log_viewer, 'NONEBBOT_ROOT', project/'nonebot'):
                with TestClient(d.app) as client:
                    self.assertEqual(client.get('/api/logs').status_code, 401)
                    client.post('/api/login', json={'password':'a-strong-test-password'})
                    data = client.get('/api/logs?name=nonebot.out.log&tail=2').json()
                    self.assertTrue(data['available'])
                    self.assertEqual(data['lines'][-1], 'last')
                    self.assertIn('api_key=***', '\n'.join(data['lines']))
                    self.assertNotIn('secret123', '\n'.join(data['lines']))
                    self.assertEqual(client.get('/api/logs?name=../../etc/passwd').status_code, 400)
                    self.assertEqual(client.get('/api/logs?name=foo.log').status_code, 400)
                    daily = client.get('/api/logs?name=nonebot.log&tail=5').json()
                    self.assertEqual(daily['lines'], ['daily'])

    def test_normal_password_change_requires_auth_and_csrf(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp)/'auth'
            with patch.object(d, 'session_store_path', lambda: Path(tmp)/'sessions.json'):
                d.sessions.clear()
                d.attempts.clear()
                d.set_password('a-strong-test-password')
                with TestClient(d.app) as client:
                    self.assertEqual(client.post('/api/password', json={
                        'password':'another-strong-pass'}).status_code, 401)
                    login = client.post('/api/login', json={'password':'a-strong-test-password'})
                    headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                    self.assertEqual(client.post('/api/password', json={
                        'password':'another-strong-pass'}).status_code, 403)
                    self.assertEqual(client.post('/api/password', json={'password':'another-strong-pass'}, headers={
                        'Origin':'http://testserver','X-CSRF-Token':'bad'}).status_code, 403)
                    self.assertEqual(client.post('/api/password', json={
                        'password':'a-strong-test-password'}, headers=headers).status_code, 400)
                    self.assertEqual(client.get('/api/state').status_code, 200)
                    self.assertEqual(client.post('/api/password', json={
                        'password':'another-strong-pass'}, headers=headers).status_code, 200)
                    self.assertEqual(client.get('/api/state').status_code, 401)
                    self.assertEqual(client.post('/api/login', json={'password':'a-strong-test-password'}).status_code, 401)
                    changed = client.post('/api/login', json={'password':'another-strong-pass'})
                    self.assertEqual(changed.status_code, 200)
                    self.assertFalse(changed.json()['must_change'])

    def test_login_and_csrf(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp)/'auth'
            memory.DEFAULT_PATH = Path(tmp)/'memory.db'
            topics.DEFAULT_PATH = Path(tmp)/'topics.db'
            weather.PATH = Path(tmp)/'weather.db'
            # Functions bind their default path at definition time; patch only
            # the explicit paths used by memory/topic operations in this test.
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                self.assertEqual(client.get('/api/state').status_code, 401)
                self.assertEqual(client.post('/api/login', json={'password':'wrong'}).status_code, 401)
                login = client.post('/api/login', json={'password':'a-strong-test-password'})
                self.assertEqual(login.status_code, 200)
                self.assertEqual(client.post('/api/topic', json={'group':'123','action':'开'}).status_code, 403)
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                self.assertEqual(client.post('/api/topic', json={'group':'123','action':'开'}, headers=headers).status_code, 200)
                self.assertTrue(topics.enabled(topics.DEFAULT_PATH,'123'))
                self.assertEqual(client.get('/api/group/123').json()['enabled'], True)
                topics.record(topics.DEFAULT_PATH, '123', '1', '你好', user_id='42', display_name='群友', tokenizer=lambda _: [])
                ranking = client.get('/api/group/123').json()['speakers']
                self.assertEqual([(s['user_id'], s['display_name'], s['count']) for s in ranking], [('42', '群友', 1)])

    def test_group_topic_empty_and_invalid_groups_have_readable_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp) / 'auth'
            topics.DEFAULT_PATH = Path(tmp) / 'topics.db'
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                login = client.post('/api/login', json={'password': 'a-strong-test-password'})
                headers = {'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']}
                empty = client.post('/api/topic', json={'group': '   ', 'action': '开'}, headers=headers)
                self.assertEqual(empty.status_code, 400)
                self.assertEqual(empty.json()['detail'], '请先选择或输入群号')
                for bad in ('abc', '0', '000', '²', '1' * 21):
                    response = client.get('/api/group/' + bad)
                    self.assertEqual(response.status_code, 400, bad)
                    self.assertEqual(response.json()['detail'], '群号无效')
                    response = client.post('/api/topic', json={'group': bad, 'action': '开'}, headers=headers)
                    self.assertEqual(response.status_code, 400, bad)
                    self.assertEqual(response.json()['detail'], '群号无效')
                numeric_zero = client.post('/api/topic', json={'group': 0, 'action': '开'}, headers=headers)
                self.assertEqual(numeric_zero.status_code, 400)
                self.assertEqual(numeric_zero.json()['detail'], '群号无效')
                invalid_action = client.post('/api/topic', json={'group': '123', 'action': 'x'}, headers=headers)
                self.assertEqual(invalid_action.status_code, 400)
                self.assertEqual(invalid_action.json()['detail'], '操作无效')
                saved = client.post('/api/topic', json={'group': '123', 'action': '开'}, headers=headers)
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()['group'], '123')
                legacy = client.post('/api/topic', json={'group': '00123', 'action': '开'}, headers=headers)
                self.assertEqual(legacy.status_code, 200)
                self.assertEqual(legacy.json()['group'], '00123')
                self.assertTrue(client.get('/api/group/00123').json()['enabled'])
                self.assertEqual(client.post('/api/topic', json={'group': '00123', 'action': '关'}, headers=headers).status_code, 200)
                self.assertFalse(client.get('/api/group/00123').json()['enabled'])
                self.assertTrue(client.get('/api/group/123').json()['enabled'])

    def test_model_chat_test_endpoint_requires_csrf_and_maps_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            d.AUTH_FILE = Path(tmp) / 'auth'
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                login = client.post('/api/login', json={'password': 'a-strong-test-password'})
                headers = {'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']}
                messages = [{'role': 'user', 'content': '你好'}]
                self.assertEqual(client.post('/api/model/chat', json={'messages': messages}).status_code, 403)
                with patch.object(d.model_chat, 'chat', new_callable=AsyncMock, return_value={
                    'content': '你好', 'model': 'test', 'usage': {'total_tokens': 2}, 'latency_ms': 12,
                }) as chat:
                    response = client.post('/api/model/chat', json={'messages': messages}, headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()['content'], '你好')
                    chat.assert_awaited_once()
                    self.assertEqual(chat.await_args.args[0], messages)
                with patch.object(d.model_chat, 'chat', new_callable=AsyncMock, side_effect=d.model_chat.ChatError('模型提供商返回 HTTP 500')):
                    response = client.post('/api/model/chat', json={'messages': messages}, headers=headers)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()['detail'], '模型提供商返回 HTTP 500')

    def test_user_memory_payload_includes_origin_relations_and_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d.AUTH_FILE = root/'auth'
            d.sessions.clear()
            d.attempts.clear()
            old = memory.DEFAULT_PATH
            memory.DEFAULT_PATH = root/'memory.db'
            try:
                d.set_password('a-strong-test-password')
                origin = {'group_id':'123','bot_id':'1000','origin_ts':1000,'participants':['2']}
                memory.remember_people(memory.DEFAULT_PATH, '123', {'2':'小明'})
                memory.apply_facts(memory.DEFAULT_PATH, '1', 'group', 0,
                                   [{'action':'save','text':'用户喜欢咖啡','vector':[1.0,0.0],
                                     'links':[],'allowed':[]}], event_id='e1', origin=origin)
                memory.apply_facts(memory.DEFAULT_PATH, '1', 'group', 0,
                                   [{'action':'candidate','text':'用户不喜欢咖啡',
                                     'similar_id':1,'reason':'conflict','weight':0.9}],
                                   event_id='e2', origin=origin)
                with TestClient(d.app) as client:
                    login = client.post('/api/login', json={'password':'a-strong-test-password'})
                    self.assertEqual(login.status_code, 200)
                    payload = client.get('/api/user/1').json()
                self.assertEqual(payload['memories'][0]['text'], '用户喜欢咖啡')
                self.assertEqual(payload['memories'][0]['origin']['participants'],
                                 [{'id':'2','name':'小明'}])
                self.assertEqual(payload['memories'][0]['relations'], [])
                self.assertEqual(payload['stats']['memories'], 1)
                self.assertEqual(payload['candidates'][0]['reason'], 'conflict')
            finally:
                memory.DEFAULT_PATH = old

    def test_status_grafana_token_is_stored_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d.AUTH_FILE = root/'auth'
            d.set_password('a-strong-test-password')
            config_path = root/'statuscard.json'
            with patch.object(d, 'status_paths', return_value=(config_path, root/'state.json', root/'status.db')), \
                    patch.object(d, 'ROOT', root), TestClient(d.app) as client:
                login = client.post('/api/login', json={'password':'a-strong-test-password'})
                headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                response = client.post('/api/status/config', headers=headers, json={'grafana': {
                    'enabled': True, 'url': 'http://grafana:3000', 'datasource_uid': 'prom-uid',
                    'temp_query': 'max(node_hwmon_temp_celsius)', 'avg_window_minutes': 30,
                    'token': 'glsa_secret',
                }})
                self.assertEqual(response.status_code, 200)
                saved = json.loads(config_path.read_text(encoding='utf-8'))
                self.assertEqual(saved['grafana']['datasource_uid'], 'prom-uid')
                self.assertNotIn('token', saved['grafana'])
                secret = json.loads((root/'data'/'status_grafana_secrets.json').read_text(encoding='utf-8'))
                self.assertEqual(secret['token'], 'glsa_secret')
                self.assertNotIn('glsa_secret', json.dumps(response.json()))

    def test_initialize_password_creates_usable_initial_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp)/'dashboard-auth.json'
            with patch.object(d, 'AUTH_FILE', auth), \
                 patch.object(d, 'session_store_path', lambda: Path(tmp)/'dashboard-sessions.json'):
                password = d.initialize_password()
                self.assertGreaterEqual(len(password), 24)
                saved = json.loads(auth.read_text(encoding='utf-8'))
                self.assertTrue(saved['must_change'])
                self.assertTrue(d.password_matches(password, saved))
                initial = auth.parent/d.INITIAL_PASSWORD_FILE
                self.assertTrue(initial.exists())
                self.assertEqual(initial.read_text(encoding='utf-8').strip(), password)
                # Windows 的 chmod 只处理只读位，权限断言只在类 Unix 上有意义。
                if os.name != 'nt':
                    self.assertEqual(auth.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(initial.stat().st_mode & 0o777, 0o600)
                with TestClient(d.app) as client:
                    login = client.post('/api/login', json={'password':password})
                    self.assertEqual(login.status_code, 200)
                    self.assertTrue(login.json()['must_change'])
                    headers = {'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf']}
                    self.assertEqual(client.get('/api/state').status_code, 403)

    def test_initialize_password_honours_explicit_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp)/'nested'/'dashboard-auth.json'
            password = d.initialize_password('explicit-pass-1234', path=auth)
            self.assertEqual(password, 'explicit-pass-1234')
            self.assertTrue(d.password_matches(password, json.loads(auth.read_text(encoding='utf-8'))))
            self.assertEqual((auth.parent/d.INITIAL_PASSWORD_FILE).read_text(encoding='utf-8').strip(), password)

    def test_initialize_password_rejects_short_password_and_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp)/'dashboard-auth.json'
            with patch.object(d, 'AUTH_FILE', auth):
                with self.assertRaises(ValueError):
                    d.initialize_password('too-short')
                self.assertFalse(auth.exists())
                self.assertFalse((auth.parent/d.INITIAL_PASSWORD_FILE).exists())
                d.initialize_password('explicit-pass-1234')
                original = auth.read_text(encoding='utf-8')
                with self.assertRaises(FileExistsError):
                    d.initialize_password('another-pass-1234')
                self.assertEqual(auth.read_text(encoding='utf-8'), original)
                self.assertEqual((auth.parent/d.INITIAL_PASSWORD_FILE).read_text(encoding='utf-8').strip(), 'explicit-pass-1234')

if __name__ == '__main__': unittest.main()
