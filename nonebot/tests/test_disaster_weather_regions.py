"""Regression cases for local weather subscriptions and incomplete upstream regions."""

import unittest

import disaster_china_logic as china


LOCAL = {
    'enabled': True,
    'province': '',
    'city': '杭州市滨江区、杭州市上城区、杭州市滨江区、杭州市西湖区、杭州市萧山区',
}


def weather(province='', city='', district='', adcode='', **extra):
    return china.parse_jian_weather({
        'id': 'region-test',
        'title': f'{province}{city}{district}发布大风黄色预警',
        'province': province, 'city': city, 'district': district, 'adcode': adcode,
        **extra,
    })


class WeatherRegionsTest(unittest.TestCase):
    def matches(self, alert, local=None):
        return china.within_weather_filters(alert, {
            'local': LOCAL if local is None else local,
            'filters': {'weather_min_color': '黄色'},
        })

    def test_hainan_province_warning_does_not_bypass_hangzhou_subscription(self):
        alert = weather('海南省', id='46000041600000_20261102113303',
                        title='海南省发布海上雷雨大风黄色预警信号')
        self.assertFalse(self.matches(alert))

    def test_selected_district_passes(self):
        self.assertTrue(self.matches(weather('浙江省', '杭州市', '滨江区', '330108')))

    def test_unselected_hangzhou_district_does_not_match_city_prefix(self):
        self.assertFalse(self.matches(weather('浙江省', '杭州市', '临安区', '330112')))

    def test_region_cases(self):
        cases = [
            (weather('陕西省', adcode='610000'), False),
            (weather(), False),
            (weather('浙江省', '杭州市', '余杭区', '330110'), False),
            (weather('浙江省', '杭州市', '上城区', '330102'), True),
            (weather('浙江省', '杭州市', '西湖区', '330106'), True),
            (weather('浙江省', '杭州市', '萧山区', '330109'), True),
            (weather('江西省', '南昌市', '西湖区', '360103'), False),
            (weather('浙江省', '杭州市', adcode='330112'), False),
            (weather('浙江省', '杭州市'), False),
            (weather('浙江省', '杭州市', adcode='330100'), True),
            (weather('浙江省', '杭州市', id='33010041600000_20261102113303'), True),
        ]
        for alert, expected in cases:
            with self.subTest(title=alert.title, adcode=alert.adcode, id=alert.alert_id):
                self.assertEqual(self.matches(alert), expected)

    def test_city_subscription_includes_all_its_districts(self):
        local = dict(LOCAL, city='杭州')
        self.assertTrue(self.matches(weather('浙江省', '杭州市', '临安区'), local))

    def test_full_path_and_abbreviations(self):
        alert = weather('浙江省', '杭州市', '滨江区')
        for name in ('杭州市滨江区', '杭州滨江', '浙江省杭州市滨江区', '浙江杭州滨江区'):
            with self.subTest(name=name):
                self.assertTrue(self.matches(alert, dict(LOCAL, city=name)))

    def test_province_scope_requires_a_confirmed_parent_path(self):
        alert = weather('浙江省', adcode='330000')
        self.assertTrue(self.matches(alert, dict(LOCAL, city='浙江省杭州市滨江区')))
        # City names alone do not establish province membership without a directory.
        self.assertFalse(self.matches(alert))
        self.assertFalse(self.matches(weather('海南省', adcode='460000'),
                                      dict(LOCAL, city='浙江省杭州市滨江区')))

    def test_missing_province_cannot_bypass_province_subscription(self):
        self.assertFalse(self.matches(weather(city='杭州市', district='滨江区'),
                                      dict(LOCAL, province='浙江省')))

    def test_region_names_are_not_arbitrary_substrings(self):
        self.assertFalse(self.matches(weather('河南省', '新乡市', '原阳县'),
                                      dict(LOCAL, city='阳')))

    def test_no_region_constraints_or_disabled_filter_keep_existing_behavior(self):
        for local in (dict(LOCAL, enabled=False), dict(LOCAL, city='，、;\n')):
            self.assertTrue(self.matches(weather('海南省'), local))


if __name__ == '__main__':
    unittest.main()
