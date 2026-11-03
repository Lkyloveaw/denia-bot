import sys
sys.path.insert(0, '/opt/qqbot/nonebot'); sys.path.insert(0, '/opt/qqbot/nonebot/parser_deps')
from PIL import Image, ImageDraw
from media_parser.core.config import PluginConfig
from media_parser.core.render import Renderer
cfg = PluginConfig(); r = Renderer(cfg)
w = r.DEFAULT_CARD_WIDTH - 2*r.PADDING
img = Image.new('RGB', (w, 10)); d = ImageDraw.Draw(img)
tests = [
  "Day 26/ You can now easily switch between multiple accounts on mobile, and coming soon to desktop too.",
  "https://help.openai.com/en/articles/20001068-using-multiple-chatgpt-accounts-on-web-and-mobile",
  "Max is still $100/month, and Ultra is still $200. If you're a subscriber, there's nothing you need to do.",
]
for t in tests:
    print(f'\n{t[:60]}')
    for l in r._wrap_text(t, w, r.fontset.text_font):
        real = d.textlength(l, font=r.fontset.text_font.font)
        print(f'  [{real:5.0f}/{"超!" if real>w else "ok "}] {l}')
