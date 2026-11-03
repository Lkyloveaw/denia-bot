"""QQ weather queries and explicitly configured scheduled pushes."""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from nonebot import get_driver, get_bots, logger, on_message
from nonebot.adapters import Event, Bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from muicebot.plugin import PluginMetadata
import gate_logic as gl
import weather_service as weather

__plugin_meta__ = PluginMetadata(name='weather', description='天气查询与推送', usage='天气 城市 / 天气预报 城市')

gl.register_help_rows([
    ('天气 <城市>', '当前天气实况'),
    ('天气预报 <城市>', '今明两天预报'),
])
_command = re.compile(r'^\s*[./]?(天气预报|预报|weather|forecast|天气)\s*(.*?)\s*$', re.I)
_natural = re.compile(r'^(?:请问|帮我看看|查一下|查询)?\s*([\u4e00-\u9fff]{2,12})(?:今天|明天|这几天)?(?:的)?天气(?:怎么样|如何|预报|多少|情况)?[？?。！!]*$')

def parse(text):
    if re.fullmatch(r'(?:今天|明天|现在)?天气(?:怎么样|如何|预报)?[？?。！!]*', text.strip()):
        return '', '明天' in text or '预报' in text
    m = _command.match(text.strip())
    if m:
        city = m.group(2).strip()
        return city, m.group(1).lower() in ('天气预报','预报','forecast')
    m = _natural.match(text.strip())
    if m:
        city = m.group(1).removesuffix('的').removesuffix('今天').removesuffix('明天')
        return city, '明天' in text or '预报' in text or '这几天' in text
    if re.search(r'(?:天气怎么样|天气如何|天气预报)[？?。！!]*$', text):
        return '', False
    return None

async def rule(event: Event):
    text = event.get_plaintext().strip()
    if isinstance(event, GroupMessageEvent) and not event.is_tome() and not _command.match(text):
        return False
    return parse(text) is not None

matcher = on_message(rule=rule, priority=3, block=True)
_recent = {}

async def as_picture(text):
    try:
        import weather_image
        from card_delivery import image_message
        pages = await asyncio.to_thread(weather_image.render_pages, text)
        return image_message(pages)
    except Exception:
        logger.warning('[weather] 图片渲染失败，回退到文字')
        return text

@matcher.handle()
async def handle(event: Event):
    city, forecast = parse(event.get_plaintext())
    if not city:
        await matcher.finish('想查询哪个城市或区县？例如：.天气 北京')
    now = time.monotonic()
    key = str(event.get_user_id())
    if now - _recent.get(key, 0) < 10:
        await matcher.finish('查询太频繁，请稍后再试。')
    _recent[key] = now
    if len(_recent) > 1000:
        _recent.clear()
    try:
        cfg = weather.config()
        result = await weather.query(cfg.get('host',''), cfg.get('key',''), city, forecast)
        result = await as_picture(result)
    except ValueError as e:
        result = str(e) if str(e) != 'API Host 格式不正确' else '天气服务尚未配置，请联系管理员。'
    except Exception:
        logger.warning('[weather] 天气服务请求失败')
        result = '天气暂时查询失败，请稍后再试。'
    await matcher.finish(result)

_task = None
async def worker():
    while True:
        try:
            now = datetime.now(timezone(timedelta(hours=8)))
            for item in weather.list_pushes():
                if item['clock'] != now.strftime('%H:%M') or item['last_date'] == now.date().isoformat():
                    continue
                try:
                    bots = get_bots()
                    if not bots: continue
                    cfg = weather.config()
                    result = await weather.query(cfg.get('host',''),cfg.get('key',''),item['city'],True)
                    result = await as_picture(result)
                    if weather.claim_push(item['id'],now.date().isoformat()):
                        bot = next(iter(bots.values()))
                        if item['kind'] == 'group':
                            await bot.send_group_msg(group_id=int(item['target']),message=result)
                        else:
                            await bot.send_private_msg(user_id=int(item['target']),message=result)
                except Exception:
                    logger.warning('[weather] 一项定时推送失败，请检查配置和连接')
        except Exception:
            logger.warning('[weather] 定时推送失败，请检查配置和连接')
        await asyncio.sleep(30)

@get_driver().on_startup
async def start():
    global _task
    _task = asyncio.create_task(worker())

@get_driver().on_shutdown
async def stop():
    if _task:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
