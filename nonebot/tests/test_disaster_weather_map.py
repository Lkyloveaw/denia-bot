"""Administrative maps use synthetic geometry here; tests never call the network."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disaster_china_logic as china
import disaster_weather_map as maps


def feature(code, name, parent, x=114.0):
    return {
        'type':'Feature',
        'properties':{'adcode':code,'name':name,'parent':{'adcode':parent},'centroid':[x+.1,37.1]},
        'geometry':{'type':'Polygon','coordinates':[[[x,37],[x+.2,37],[x+.2,37.2],[x,37.2],[x,37]]]},
    }


def collection(*features):
    return {'type':'FeatureCollection','features':list(features)}


SHAHE = feature(130582, '沙河市', 130500)
NEIGHBOR = feature(130503, '信都区', 130500, 114.2)
XINGTAI = feature(130500, '邢台市', 130000)
FIXTURES = {
    '130582.json':collection(SHAHE),
    '130500.json':collection(XINGTAI),
    '130500_full.json':collection(SHAHE, NEIGHBOR),
}


def alert(**updates):
    return china.parse_jian_weather(dict({
        'id':'13058241600000_20261031155053',
        'title':'河北省邢台市沙河市发布大风蓝色预警',
        'province':'河北省','city':'邢台市','district':'沙河市',
    }, **updates))


class WeatherMapTest(unittest.TestCase):
    def test_id_resolves_parent_and_only_highlights_exact_area_then_uses_cache(self):
        async def run(folder):
            requests = []
            def handler(request):
                requests.append(request.url.path)
                return httpx.Response(200, json=FIXTURES[request.url.path.rsplit('/',1)[-1]])
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await maps.map_for_alert(alert(), cache_dir=folder, client=client)
                self.assertEqual(result.name, '沙河市')
                self.assertEqual(result.context, '邢台市')
                svg = result.svg()
                self.assertEqual(svg.count('data-warning-area="true"'), 1)
                self.assertIn('data-adcode="130582"', svg)
                self.assertIn('data-adcode="130503"', svg)
                self.assertIn('预警行政区域示意', svg)
                self.assertNotIn('http', svg)
                self.assertEqual(len(requests), 3)
                cached = await maps.map_for_alert(alert(), cache_dir=folder, client=client)
                self.assertEqual(cached.svg(), svg)
                self.assertEqual(len(requests), 3)
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(Path(folder)))

    def test_direct_admin_county_uses_actual_parent_and_hainan_island_context(self):
        async def run(folder):
            changjiang = feature(469026, '昌江黎族自治县', 460000, 109)
            hainan = feature(460000, '海南省', 100000, 109)
            sansha = feature(460300, '三沙市', 460000, 112)
            data = {'469026.json':collection(changjiang), '460000.json':collection(hainan),
                    '460000_full.json':collection(changjiang, sansha)}
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200,json=data[r.url.path.rsplit('/',1)[-1]])
            )) as client:
                result = await maps.map_for_alert(alert(adcode='469026'), cache_dir=folder, client=client)
                self.assertEqual(result.context, '海南岛')
                self.assertIn('469026', result.svg())
                self.assertNotIn('460300', result.svg())
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(Path(folder)))

    def test_invalid_code_and_missing_boundary_do_not_draw_wrong_area(self):
        async def run(folder):
            requests = []
            def handler(request):
                requests.append(request.url.path)
                return httpx.Response(200, json=collection(NEIGHBOR))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                self.assertIsNone(await maps.map_for_alert(alert(id='unstructured', adcode='../x'), cache_dir=folder, client=client))
                self.assertEqual(requests, [])
                self.assertIsNone(await maps.map_for_alert(alert(), cache_dir=folder, client=client))
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(Path(folder)))

    def test_malformed_cache_and_failed_source_fall_back_without_exception(self):
        async def run(folder):
            (folder/'130582.json').write_text('{broken', encoding='utf-8')
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(503, text='unavailable')
            )) as client:
                self.assertIsNone(await maps.map_for_alert(alert(), cache_dir=folder, client=client))
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(Path(folder)))

    def test_untrusted_labels_are_escaped(self):
        async def run(folder):
            poisoned = feature(130582, '<script>bad</script>', 130500)
            data = dict(FIXTURES, **{'130582.json':collection(poisoned), '130500_full.json':collection(poisoned)})
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200,json=data[r.url.path.rsplit('/',1)[-1]])
            )) as client:
                result = await maps.map_for_alert(alert(), cache_dir=folder, client=client)
                self.assertIn('&lt;script&gt;', result.svg())
                self.assertNotIn('<script>', result.svg())
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(Path(folder)))

    def test_invalid_coordinates_fall_back(self):
        async def run(folder):
            invalid = feature(130582, '沙河市', 130500)
            invalid['geometry']['coordinates'][0][0] = [999,37]
            data = dict(FIXTURES, **{'130500_full.json':collection(invalid)})
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200,json=data[r.url.path.rsplit('/',1)[-1]])
            )) as client:
                self.assertIsNone(await maps.map_for_alert(alert(), cache_dir=folder, client=client))
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(Path(folder)))

    def test_malformed_nested_metadata_falls_back_to_no_map(self):
        async def run(folder, data):
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200,json=data)
            )) as client:
                self.assertIsNone(await maps.map_for_alert(alert(), cache_dir=folder, client=client))
        for invalid in (dict(SHAHE, properties=None), dict(SHAHE, properties=dict(SHAHE['properties'], parent=None))):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as folder:
                asyncio.run(run(Path(folder), collection(invalid)))

    def test_municipality_district_follows_metadata_parent(self):
        async def run(folder):
            district = feature(110101, '东城区', 110000)
            municipality = feature(110000, '北京市', 100000)
            data = {'110101.json':collection(district), '110000.json':collection(municipality),
                    '110000_full.json':collection(district)}
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200,json=data[r.url.path.rsplit('/',1)[-1]])
            )) as client:
                result = await maps.map_for_alert(alert(adcode='110101'), cache_dir=folder, client=client)
                self.assertEqual(result.context, '北京市')
                self.assertEqual(result.name, '东城区')
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(Path(folder)))

    def test_source_timeout_keeps_valid_stale_cache(self):
        import os
        async def run(folder):
            for filename, data in FIXTURES.items():
                path = folder/filename
                path.write_text(json.dumps(data), encoding='utf-8')
                os.utime(path, (1,1))
            def handler(request):
                raise httpx.ReadTimeout('timed out', request=request)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await maps.map_for_alert(alert(), cache_dir=folder, client=client)
                self.assertEqual(result.name, '沙河市')
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(Path(folder)))


if __name__ == '__main__':
    unittest.main()
