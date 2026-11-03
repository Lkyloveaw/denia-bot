import asyncio, sys, shutil
sys.path.insert(0, '/opt/qqbot/nonebot'); sys.path.insert(0, '/opt/qqbot/nonebot/parser_deps')
from media_parser.core.config import PluginConfig
from media_parser.core.download import Downloader
from media_parser.core.parsers.twitter import TwitterParser
from media_parser.core.render import Renderer
from media_parser.core.sender import MessageSender

cases = [
  ('纯文字', 'https://x.com/thsottiaux/status/2116405414921384099'),
  ('带图', 'https://x.com/Wuthering_Waves/status/2116002155723444733'),
]
async def main():
    cfg = PluginConfig(); p = TwitterParser(cfg, Downloader(cfg))
    s = MessageSender(cfg, Renderer(cfg))
    pat = next(rx for kw, rx in p.__class__._key_patterns if kw == 'x.com')
    for label, url in cases:
        print(f'\n===== {label} =====')
        try:
            res = await p.parse('x.com', pat.search(url.removeprefix('https://')))
        except Exception as e:
            print('解析失败:', type(e).__name__, e); continue
        print('作者:', res.author.name if res.author else None)
        print('正文:', (res.text or '')[:70])
        print('内容数:', len(list(res.contents)))
        plan = s._build_send_plan(res)
        print('计划: render=%s preview=%s merge=%s' % (plan['render_card'], plan['preview_card'], plan['force_merge']))
        if plan['render_card']:
            path = await Renderer(cfg).render_card(res)
            out = f'/tmp/tw_{label}.png'; shutil.copy2(path, out)
            print('卡片:', out, path.stat().st_size, '字节')
asyncio.run(main())
