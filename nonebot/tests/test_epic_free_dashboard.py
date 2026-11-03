# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient

import dashboard as d
import epic_free_config as epic_config


class EpicFreeDashboardTest(unittest.TestCase):
    def test_epic_route_is_registered(self):
        # Verify routing behavior; include_router may use lazy router wrappers.
        with TestClient(d.app) as client:
            self.assertEqual(client.post('/api/epic', json={}).status_code, 401)
            self.assertEqual(client.get('/api/state').status_code, 401)

    def test_epic_api_requires_login(self):
        with TestClient(d.app) as client:
            self.assertEqual(client.post('/api/epic', json={'action': 'test'}).status_code, 401)

    def test_epic_api_adds_target_with_login_and_csrf(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(d, 'AUTH_FILE', Path(folder) / 'auth'), \
                patch.object(epic_config, 'PATH', Path(folder) / 'epic.json'), \
                patch.object(epic_config, 'TRIGGER_PATH', Path(folder) / 'trigger.json'):
            d.set_password('a-strong-test-password')
            with TestClient(d.app) as client:
                login = client.post('/api/login', json={'password': 'a-strong-test-password'})
                self.assertEqual(login.status_code, 200)
                headers = {'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']}
                self.assertEqual(client.post('/api/epic', json={'action': 'add', 'kind': 'group', 'id': '12345'}).status_code, 403)
                response = client.post('/api/epic', json={'action': 'add', 'kind': 'group', 'id': '12345'}, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(epic_config.read()['targets'], [{'kind': 'group', 'id': '12345'}])
                removed = client.post('/api/epic', json={'action': 'delete', 'kind': 'group', 'id': '12345'}, headers=headers)
                self.assertEqual(removed.status_code, 200)
                self.assertEqual(epic_config.read()['targets'], [])
                self.assertEqual(client.post('/api/epic', json={'action': 'test'}, headers=headers).status_code, 200)
                self.assertTrue(epic_config.has_test_request())
                self.assertIn('epic', client.get('/api/state').json())

    def test_dashboard_html_has_epic_section(self):
        text = (d.ROOT / 'dashboard.html').read_text(encoding='utf-8')
        self.assertIn('id="epic"', text)
        self.assertIn('loadEpic()', text)


if __name__ == '__main__':
    unittest.main()
