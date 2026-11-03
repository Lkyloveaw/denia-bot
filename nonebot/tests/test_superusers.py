"""超级管理员名单：校验、存储、热应用与面板接口。

分三层验：`superuser_settings` 是纯逻辑；`plugins/superusers` 负责把名单写进
driver 配置并按 mtime 热更新；面板接口是另一条写路径，要保证鉴权、校验和回退
都成立。
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot

ensure_nonebot()

import superuser_settings as settings
from plugins import superusers as plugin


class NormalizeTest(unittest.TestCase):
    def test_accepts_list_and_hand_typed_string(self):
        self.assertEqual(settings.normalize(['123456', ' 789012 ']), ['123456', '789012'])
        self.assertEqual(settings.normalize('123456, 789012'), ['123456', '789012'])
        self.assertEqual(settings.normalize('123456，789012'), ['123456', '789012'], '全角逗号也要认')

    def test_deduplicates_keeping_order(self):
        self.assertEqual(settings.normalize(['123456', '789012', '123456']), ['123456', '789012'])

    def test_rejects_non_qq_values(self):
        for bad in (['abc'], ['123'], ['012345'], ['123456789012345678901'], [123]):
            with self.assertRaises(ValueError, msg=str(bad)):
                settings.normalize(bad)

    def test_empty_is_valid(self):
        self.assertEqual(settings.normalize([]), [])
        self.assertEqual(settings.normalize(''), [])
        self.assertEqual(settings.normalize(None), [])

    def test_limits_count(self):
        with self.assertRaises(ValueError):
            settings.normalize([str(100000 + index) for index in range(settings.MAX_USERS + 1)])


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'superusers.json'

    def tearDown(self):
        self.folder.cleanup()

    def test_missing_file_means_none(self):
        self.assertIsNone(settings.load(self.path))

    def test_roundtrip_and_permissions(self):
        saved = settings.save(['123456'], self.path)
        self.assertEqual(saved, ['123456'])
        self.assertEqual(settings.load(self.path), ['123456'])
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(payload['users'], ['123456'])

    def test_corrupt_file_treated_as_missing(self):
        self.path.write_text('{broken', encoding='utf-8')
        self.assertIsNone(settings.load(self.path))
        users, source = settings.resolve(['999999'], self.path)
        self.assertEqual(source, 'env', '坏文件要回退 .env，而不是让所有人都失去权限')
        self.assertEqual(users, ['999999'])

    def test_empty_file_is_authoritative(self):
        settings.save([], self.path)
        users, source = settings.resolve(['999999'], self.path)
        self.assertEqual(source, 'file')
        self.assertEqual(users, [], '空名单是用户的明确选择，不能被 .env 覆盖')

    def test_resolve_prefers_file(self):
        settings.save(['111111'], self.path)
        self.assertEqual(settings.resolve(['222222'], self.path), (['111111'], 'file'))

    def test_reset_falls_back_to_env(self):
        settings.save(['111111'], self.path)
        self.assertTrue(settings.reset(self.path))
        self.assertFalse(settings.reset(self.path))
        self.assertEqual(settings.resolve(['222222'], self.path), (['222222'], 'env'))

    def test_read_env_superusers(self):
        env = Path(self.folder.name) / '.env'
        env.write_text('SUPERUSERS=["123456","789012"]\nOTHER=1\n', encoding='utf-8')
        self.assertEqual(settings.read_env_superusers(env), ['123456', '789012'])

        env.write_text('SUPERUSERS=123456,789012\n', encoding='utf-8')
        self.assertEqual(settings.read_env_superusers(env), ['123456', '789012'], '非 JSON 写法也要兜底')

        env.write_text('# SUPERUSERS=["123456"]\n', encoding='utf-8')
        self.assertEqual(settings.read_env_superusers(env), [], '注释掉的配置不算')

        self.assertEqual(settings.read_env_superusers(Path(self.folder.name) / 'nope'), [])


class PluginApplyTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'superusers.json'
        self.patches = [patch.object(settings, 'PATH', self.path)]
        for item in self.patches:
            item.start()
        self.driver = plugin.driver
        self.original = set(getattr(self.driver.config, 'superusers', None) or ())
        plugin._env_users = {'999999'}

    def tearDown(self):
        plugin._env_users = set()
        plugin._last_stamp = None
        self.driver.config.superusers = self.original
        for item in self.patches:
            item.stop()
        self.folder.cleanup()

    def test_apply_uses_env_when_no_file(self):
        users, source = plugin.apply()
        self.assertEqual((users, source), (['999999'], 'env'))
        self.assertEqual(self.driver.config.superusers, {'999999'})

    def test_apply_switches_to_file_and_back(self):
        settings.save(['123456'])
        users, source = plugin.apply()
        self.assertEqual((users, source), (['123456'], 'file'))
        self.assertEqual(self.driver.config.superusers, {'123456'})

        settings.reset()
        users, source = plugin.apply()
        self.assertEqual((users, source), (['999999'], 'env'))
        self.assertEqual(self.driver.config.superusers, {'999999'})

    def test_builtin_superuser_checker_follows_the_list(self):
        """NoneBot 的 `permission=SUPERUSER` 底层判定器读同一个 config，必须跟着变。

        `SUPERUSER` 本身是依赖注入包装，直接调用要用 matcher 的上下文变量；
       这里验它底下的 `SuperUser` 判定器——那才是真正读 `config.superusers` 的地方。
        """
        from types import SimpleNamespace

        from nonebot.permission import SuperUser

        class FakeBot:
            config = self.driver.config
            adapter = SimpleNamespace(get_name=lambda: 'OneBot V11')

        class FakeEvent:
            def __init__(self, user_id):
                self._user_id = user_id

            def get_user_id(self):
                return self._user_id

        checker = SuperUser()
        settings.save(['123456'])
        plugin.apply()
        self.assertTrue(asyncio.run(checker(FakeBot(), FakeEvent('123456'))))
        self.assertFalse(asyncio.run(checker(FakeBot(), FakeEvent('000001'))))

    def test_watcher_picks_up_changes(self):
        """改文件后不必重启：watcher 按 mtime 重新应用。"""

        async def scenario():
            plugin.POLL_SECONDS = 0.02
            settings.save(['111111'])
            # 设成「比文件旧」的戳，让第一轮就判定为有变化并应用
            plugin._last_stamp = 0.0
            task = asyncio.create_task(plugin.watch())
            try:
                await asyncio.sleep(0.05)
                self.assertEqual(self.driver.config.superusers, {'111111'})

                settings.save(['222222', '333333'])
                await asyncio.sleep(0.1)
                self.assertEqual(self.driver.config.superusers, {'222222', '333333'})
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                plugin.POLL_SECONDS = 30

        asyncio.run(scenario())


class PanelApiTest(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        import dashboard as d

        self.d = d
        self.TestClient = TestClient
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'superusers.json'
        self.old_auth = d.AUTH_FILE
        d.AUTH_FILE = Path(self.folder.name) / 'auth.json'
        d.set_password('a-strong-test-password')
        self.patches = [
            patch.object(settings, 'PATH', self.path),
            patch.object(settings, 'read_env_superusers', lambda path=None: ['888888']),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.d.AUTH_FILE = self.old_auth
        self.folder.cleanup()

    def login(self, client):
        response = client.post('/api/login', json={'password': 'a-strong-test-password'})
        self.assertEqual(response.status_code, 200)
        return {'Origin': 'http://testserver', 'X-CSRF-Token': response.json()['csrf']}

    def test_requires_login(self):
        with self.TestClient(self.d.app) as client:
            self.assertEqual(client.get('/api/superusers').status_code, 401)

    def test_reads_env_when_no_file(self):
        with self.TestClient(self.d.app) as client:
            self.login(client)
            data = client.get('/api/superusers').json()
        self.assertEqual(data['users'], ['888888'])
        self.assertEqual(data['source'], 'env')
        self.assertEqual(data['max'], settings.MAX_USERS)

    def test_save_requires_csrf_and_creates_file(self):
        with self.TestClient(self.d.app) as client:
            headers = self.login(client)
            self.assertEqual(client.post('/api/superusers', json={'users': ['123456']}).status_code, 403)

            saved = client.post('/api/superusers', headers=headers, json={'users': ['123456', '789012']})
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()['users'], ['123456', '789012'])

            data = client.get('/api/superusers').json()
            self.assertEqual(data['source'], 'file')
            self.assertEqual(data['users'], ['123456', '789012'])
        self.assertTrue(self.path.exists())

    def test_rejects_invalid_values(self):
        with self.TestClient(self.d.app) as client:
            headers = self.login(client)
            for bad in (['abc'], ['123'], 'not-a-list'):
                response = client.post('/api/superusers', headers=headers, json={'users': bad})
                self.assertEqual(response.status_code, 400, bad)

    def test_reset_returns_to_env(self):
        with self.TestClient(self.d.app) as client:
            headers = self.login(client)
            client.post('/api/superusers', headers=headers, json={'users': ['123456']})
            response = client.post('/api/superusers', headers=headers, json={'reset': True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['reset'])
            data = client.get('/api/superusers').json()
        self.assertEqual(data['source'], 'env')
        self.assertEqual(data['users'], ['888888'])
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
