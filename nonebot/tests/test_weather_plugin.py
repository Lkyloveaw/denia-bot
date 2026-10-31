import os
import asyncio
import sys
from pathlib import Path
os.chdir(Path(__file__).resolve().parents[1])
sys.path.insert(0, os.getcwd())
import nonebot
try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot
ensure_nonebot()
from plugins.weather import parse, as_picture

assert parse('天气 北京') == ('北京', False)
assert parse('天气预报 上海') == ('上海', True)
assert parse('北京今天天气怎么样') == ('北京', False)
assert parse('北京明天天气怎么样') == ('北京', True)
assert parse('今天天气怎么样') == ('', False)
assert parse('我喜欢晴天') is None
image = asyncio.run(as_picture('北京\n晴 20℃，体感 18℃\n数据来源：和风天气 https://developer.qweather.com/attribution.html'))
assert image.type == 'image' and image.data['file'].startswith('base64://')
print('weather plugin tests passed')
