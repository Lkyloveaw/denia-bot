import sys
import unittest
from io import BytesIO
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import weather_image
from PIL import Image

class WeatherImageTest(unittest.TestCase):
    def test_forecast_image_is_png_and_contains_full_wrapped_text(self):
        text = '北京\n2026-10-29 晴 10～20℃\n2026-10-30 多云 8～18℃\n数据来源：和风天气 https://developer.qweather.com/attribution.html'
        output = weather_image.render(text)
        image = Image.open(BytesIO(output))
        self.assertEqual(image.format, 'PNG')
        self.assertGreaterEqual(image.width, 600)
        self.assertGreater(image.height, 250)

    def test_current_conditions_are_supported(self):
        output = weather_image.render('上海\n晴 21℃，体感 20℃\n数据来源：和风天气 https://developer.qweather.com/attribution.html')
        self.assertEqual(Image.open(BytesIO(output)).format, 'PNG')

if __name__ == '__main__': unittest.main()
