import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from bot_monitor import LinuxSystemdRestart, Monitor, RestartController, WindowsRestart, snowluma_candidates


class MonitorTests(unittest.TestCase):
    def test_unknown_injected_identity_is_not_reported_as_absent(self):
        async def run():
            def handler(request):
                if request.url.port == 8080: return httpx.Response(404)
                if request.url.port == 8091: return httpx.Response(200)
                if request.url.path == '/api/login': return httpx.Response(200, json={'token':'test-token'})
                return httpx.Response(200, json={'list':[{'injected':True, 'uin':'0'}]})
            with tempfile.TemporaryDirectory() as tmp:
                password=Path(tmp)/'password'; password.write_text('test', encoding='utf-8')
                status = await Monitor(password_file=password, transport=httpx.MockTransport(handler)).status()
                self.assertEqual(status['bot']['state'], 'unknown')
                self.assertIsNone(status['snowluma']['injected'])
        asyncio.run(run())

    def test_status_distinguishes_injected_target_and_bot_connection(self):
        async def run():
            def handler(request):
                if request.url.port == 8080:
                    return httpx.Response(200, json={'service':'qqbot-nonebot', 'ready':True, 'connected':True})
                if request.url.port == 8091:
                    return httpx.Response(200, json={'ok':True})
                if request.url.path == '/api/login':
                    return httpx.Response(200, json={'token':'test-token'})
                if request.url.path == '/api/processes':
                    return httpx.Response(200, json={'list':[{'injected':True, 'uin':'3892894069', 'connected':True}]})
                return httpx.Response(404)
            with tempfile.TemporaryDirectory() as tmp:
                password = Path(tmp)/'password'
                password.write_text('test', encoding='utf-8')
                monitor = Monitor(password_file=password, transport=httpx.MockTransport(handler))
                status = await monitor.status()
                self.assertEqual(status['bot']['state'], 'ready')
                self.assertTrue(status['bot']['connected'])
                self.assertEqual(status['snowluma']['injected'], True)
                self.assertTrue(status['snowluma']['connected'])
        asyncio.run(run())

    def test_restart_blocks_until_verified_ready(self):
        async def run():
            release = asyncio.Event()
            async def worker():
                await release.wait()
            controller = RestartController(worker)
            self.assertTrue(await controller.start())
            self.assertFalse(await controller.start())
            self.assertTrue(controller.running)
            release.set()
            await controller.task
            self.assertFalse(controller.running)
        asyncio.run(run())

    def test_restart_failure_remains_locked_until_ready(self):
        async def run():
            async def fail(): raise RuntimeError('启动失败')
            controller = RestartController(fail)
            self.assertTrue(await controller.start())
            await controller.task
            self.assertTrue(controller.running)
            self.assertFalse(await controller.start())
            controller.observe_ready()
            self.assertFalse(controller.running)
        asyncio.run(run())

    def test_worker_waits_for_new_process_identity(self):
        async def run():
            fake = type('FakeMonitor', (), {})()
            fake.bot_health = AsyncMock(side_effect=[{'state':'ready', 'start_token':'old', 'pid':1}, {'state':'ready', 'start_token':'old'}, {'state':'ready', 'start_token':'new'}])
            worker = WindowsRestart(fake)
            def launch(): worker.expected_token = 'new'
            with patch.object(worker, 'stop_and_start', side_effect=launch), patch('bot_monitor.asyncio.sleep', new_callable=AsyncMock):
                await worker()
            self.assertEqual(fake.bot_health.await_count, 3)
        asyncio.run(run())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows only')
    def test_restart_preflight_and_launch_use_only_fixed_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            botdir = root/'nonebot'; (botdir/'.venv'/'Scripts').mkdir(parents=True)
            (root/'tools').mkdir(); (root/'tools'/'restart-nonebot.ps1').touch()
            worker = WindowsRestart(None)
            with patch('bot_monitor.ROOT',root), patch('bot_monitor.BOT_DIR',botdir), patch('bot_monitor.subprocess.run') as stop, patch('bot_monitor.subprocess.Popen') as start:
                with self.assertRaises(RuntimeError): worker.stop_and_start()
                stop.assert_not_called()
                (botdir/'bot.py').touch(); (botdir/'.venv'/'Scripts'/'python.exe').touch()
                stop.return_value = Mock(returncode=0)
                worker.stop_and_start()
                self.assertEqual(start.call_args.args[0], [str(botdir/'.venv'/'Scripts'/'python.exe'), 'bot.py'])
                self.assertEqual(start.call_args.kwargs['cwd'], str(botdir))
                self.assertEqual(start.call_args.kwargs['env']['QQBOT_START_TOKEN'], worker.expected_token)


    @unittest.skipIf(sys.platform == 'win32', 'systemd only')
    def test_systemd_backend_is_used_on_linux(self):
        with patch('bot_monitor.shutil.which', return_value='/usr/bin/systemctl'), \
             patch('bot_monitor.subprocess.run', return_value=Mock(returncode=0, stdout='', stderr='')):
            worker = LinuxSystemdRestart(None, unit='qqbot.service')
            self.assertTrue(worker.available)

    def test_systemd_backend_reports_unavailable_without_the_unit(self):
        runner = Mock(return_value=Mock(returncode=1, stdout='', stderr='Unit not found'))
        worker = LinuxSystemdRestart(None, unit='missing.service', runner=runner)
        with patch('bot_monitor.sys.platform', 'linux'), patch('bot_monitor.shutil.which', return_value='/usr/bin/systemctl'):
            self.assertFalse(worker.available)
        self.assertNotIn('restart', runner.call_args.args[0])

    def test_systemd_restart_failure_surfaces_stderr(self):
        def runner(args, **kwargs):
            if args[:2] == ['systemctl', 'cat']: return Mock(returncode=0, stdout='', stderr='')
            return Mock(returncode=1, stdout='', stderr='Job for qqbot.service failed\nmore')
        worker = LinuxSystemdRestart(None, unit='qqbot.service', runner=runner)
        with patch('bot_monitor.sys.platform', 'linux'), patch('bot_monitor.shutil.which', return_value='/usr/bin/systemctl'):
            self.assertTrue(worker.available)
            with self.assertRaises(RuntimeError) as caught:
                worker.stop_and_start()
        self.assertIn('Job for qqbot.service failed', str(caught.exception))

    def test_systemd_worker_waits_for_a_new_main_pid(self):
        async def run():
            fake = type('FakeMonitor', (), {})()
            fake.bot_health = AsyncMock(side_effect=[
                {'state':'ready', 'pid':46943},
                {'state':'unknown'},
                {'state':'ready', 'pid':46943},
                {'state':'ready', 'pid':51000},
            ])
            worker = LinuxSystemdRestart(fake, unit='qqbot.service', runner=Mock(return_value=Mock(returncode=0, stdout='MainPID=46943', stderr='')))
            with patch.object(worker, 'stop_and_start'), patch('bot_monitor.asyncio.sleep', new_callable=AsyncMock):
                await worker()
            self.assertEqual(fake.bot_health.await_count, 4)
        asyncio.run(run())


    def test_snowluma_falls_back_to_lan_address_when_loopback_refuses(self):
        """容器把端口绑在内网 IP 上时，只探 127.0.0.1 会误报 offline。"""
        async def run():
            def handler(request):
                if request.url.host == '127.0.0.1':
                    raise httpx.ConnectError('refused', request=request)
                if request.url.path == '/api/login':
                    return httpx.Response(200, json={'token':'test-token'})
                if request.url.path == '/api/processes':
                    return httpx.Response(200, json={'list':[{'injected':True, 'uin':'3892894069', 'connected':True}]})
                return httpx.Response(200, text='webui')
            with tempfile.TemporaryDirectory() as tmp:
                password = Path(tmp)/'password'
                password.write_text('test', encoding='utf-8')
                monitor = Monitor(password_file=password, transport=httpx.MockTransport(handler))
                with patch('bot_monitor.snowluma_candidates', return_value=['http://127.0.0.1:5099', 'http://192.0.2.226:5099']):
                    result = await monitor.snowluma()
                self.assertEqual(result['state'], 'online')
                self.assertEqual(result['injected'], True)
                self.assertEqual(monitor.snowluma_base, 'http://192.0.2.226:5099')
        asyncio.run(run())

    def test_snowluma_reports_offline_only_when_every_candidate_fails(self):
        async def run():
            def handler(request):
                raise httpx.ConnectError('refused', request=request)
            with tempfile.TemporaryDirectory() as tmp:
                password = Path(tmp)/'password'
                password.write_text('test', encoding='utf-8')
                monitor = Monitor(password_file=password, transport=httpx.MockTransport(handler))
                with patch('bot_monitor.snowluma_candidates', return_value=['http://127.0.0.1:5099', 'http://192.0.2.226:5099']):
                    result = await monitor.snowluma()
                self.assertEqual(result['state'], 'offline')
                self.assertIsNone(monitor.snowluma_base)
        asyncio.run(run())

    def test_explicit_snowluma_url_wins_over_derived_candidates(self):
        with patch('bot_monitor.SNOWLUMA_URL', 'http://example.internal:5099'):
            self.assertEqual(snowluma_candidates(), ['http://example.internal:5099'])


    def test_candidate_rejected_mid_connection_does_not_abort_discovery(self):
        """host.docker.internal 这类地址会抛 RemoteProtocolError 而不是 ConnectError。"""
        async def run():
            def handler(request):
                if request.url.host == 'host.docker.internal':
                    raise httpx.RemoteProtocolError('Server disconnected without sending a response.')
                if request.url.path == '/api/login':
                    return httpx.Response(200, json={'token':'test-token'})
                if request.url.path == '/api/processes':
                    return httpx.Response(200, json={'list':[{'injected':True, 'uin':'3892894069', 'connected':True}]})
                return httpx.Response(200, text='webui')
            with tempfile.TemporaryDirectory() as tmp:
                password = Path(tmp)/'password'
                password.write_text('test', encoding='utf-8')
                monitor = Monitor(password_file=password, transport=httpx.MockTransport(handler))
                with patch('bot_monitor.snowluma_candidates', return_value=['http://host.docker.internal:5099', 'http://192.0.2.226:5099']):
                    result = await monitor.snowluma()
                self.assertEqual(result['state'], 'online')
                self.assertEqual(monitor.snowluma_base, 'http://192.0.2.226:5099')
        asyncio.run(run())

    def test_missing_password_reports_running_but_unverifiable(self):
        """没有 WebUI 密码时仍应判为「在跑」，并说明为什么看不到注入状态。"""
        async def run():
            def handler(request):
                return httpx.Response(200, text='webui')
            monitor = Monitor(password_file=Path('/nonexistent/password'), transport=httpx.MockTransport(handler))
            with patch('bot_monitor.snowluma_candidates', return_value=['http://192.0.2.226:5099']):
                result = await monitor.snowluma()
            self.assertEqual(result['state'], 'online')
            self.assertIsNone(result['injected'])
            self.assertEqual(result['reason'], 'no-password')
            self.assertIn('snowluma-webui-pass', result['detail'])
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
