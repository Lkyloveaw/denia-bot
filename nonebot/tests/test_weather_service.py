import asyncio
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import weather_service as w


class WeatherTest(unittest.TestCase):
    def test_lookup_then_forecast(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path.endswith('/city/lookup'):
                return httpx.Response(200, json={'code':'200','location':[{'name':'北京','adm1':'北京','country':'中国','lat':'39.92','lon':'116.41'}]})
            return httpx.Response(200, json={'days':[{'forecastStartTime':'2026-10-29T00:00+08:00','temperatureMax':{'value':20},'temperatureMin':{'value':10},'daytime':{'condition':{'text':'晴'}}}], 'metadata':{'attributions':[]}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await w.query('abc.qweatherapi.com', 'secret', '北京', True, client)
        result = asyncio.run(run())
        self.assertIn('北京', result)
        self.assertIn('晴', result)
        self.assertEqual(calls[1].url.path, '/weather/v1/daily/39.92/116.41')
        self.assertEqual(calls[0].headers['X-QW-Api-Key'], 'secret')

    def test_host_is_restricted(self):
        with self.assertRaises(ValueError):
            w.validate_host('evil.example.com')

    def test_push_defaults_off_and_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'weather.db'
            self.assertEqual(w.list_pushes(path), [])
            w.add_push(path, 'group', '123', '北京', '08:00')
            self.assertEqual(w.list_pushes(path)[0]['target'], '123')

if __name__ == '__main__':
    unittest.main()
