"""Render weather-service text as a compact PNG, without storing files."""
import sys
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError:
    # Debian's system Pillow is compatible with the server's matching Python 3.12.
    sys.path.append('/usr/lib/python3/dist-packages')
    from PIL import Image, ImageDraw, ImageFont

FONT_PATHS = (
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    'C:/Windows/Fonts/msyh.ttc',
    'C:/Windows/Fonts/simhei.ttf',
)

def font(size):
    for path in FONT_PATHS:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise RuntimeError('没有可用的中文字体')

def wrap(draw, text, font_obj, width):
    lines, current = [], ''
    for char in text:
        if current and draw.textlength(current + char, font=font_obj) > width:
            lines.append(current)
            current = ''
        current += char
    if current:
        lines.append(current)
    return lines or ['']

def render(text, heading='WEATHER  /  天气', subtitle='和风天气 · 天气信息'):
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError('天气内容为空')
    width = 760
    title_font, label_font, body_font, source_font = (font(n) for n in (38, 19, 25, 16))
    measure = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    title = wrap(measure, lines[0], title_font, 620)
    source = next((line for line in lines[1:] if line.startswith('数据来源：')), '')
    entries = [line for line in lines[1:] if not line.startswith('数据来源：')]
    if not entries:
        raise ValueError('天气内容为空')
    entries = [wrap(measure, line, body_font, 620) for line in entries]
    source_lines = wrap(measure, source, source_font, 620) if source else []
    header_height = 132 + 50 * len(title)
    row_heights = [max(72, 28 * len(row) + 30) for row in entries]
    height = header_height + sum(row_heights) + 90 + 23 * len(source_lines)
    canvas = Image.new('RGB', (width, height), '#f4f2ec')
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((18, 18, width-18, height-18), radius=26, fill='#fffefa', outline='#e7e1d7', width=2)
    draw.rounded_rectangle((46, 45, 98, 97), radius=16, fill='#cf795a')
    draw.ellipse((66, 65, 78, 77), outline='white', width=2)
    for x1, y1, x2, y2 in ((72,55,72,60),(72,82,72,87),(56,71,61,71),(83,71,88,71),
                           (60,59,63,62),(81,80,84,83),(60,83,63,80),(81,62,84,59)):
        draw.line((x1,y1,x2,y2), fill='white', width=2)
    draw.text((114, 47), heading, font=label_font, fill='#a45f45')
    y = 103
    for line in title:
        draw.text((52, y), line, font=title_font, fill='#302d27')
        y += 50
    draw.text((52, y+5), subtitle, font=label_font, fill='#898376')
    y = header_height
    for row, row_height in zip(entries, row_heights):
        draw.rounded_rectangle((46, y, width-46, y+row_height-8), radius=12, fill='#f8f5ef')
        for index, line in enumerate(row):
            draw.text((65, y+12+index*28), line, font=body_font, fill='#454039')
        y += row_height
    y += 17
    for line in source_lines:
        draw.text((52, y), line, font=source_font, fill='#817b70')
        y += 23
    out = BytesIO()
    canvas.save(out, format='PNG', optimize=True)
    return out.getvalue()
