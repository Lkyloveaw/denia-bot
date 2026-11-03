"""Self-contained HTML and image preprocessing helpers (MIT). No card drawing."""
from __future__ import annotations
import base64
from html import escape
from io import BytesIO
from pathlib import Path

FONT = '"HYSongYunLangHeiW", "Noto Sans CJK SC", "WenQuanYi Zen Hei", sans-serif'
BASE_CSS = '''
*{box-sizing:border-box}html,body{margin:0;padding:0}body{color:#302d27}
.sheet{padding:34px;background:#f4f2ec;font-family:var(--card-font);font-size:22px;line-height:1.45}
.sheet>div,.sheet>section,.sheet>header,.sheet>footer{margin-bottom:16px}
.sheet>:last-child{margin-bottom:0}.panel{background:#fffefa;border:1px solid #e7e1d7;border-radius:20px;padding:22px}
h1,h2,p{margin:0}h1{font-size:38px;line-height:1.3;margin:10px 0 12px}h2{font-size:24px;margin-bottom:12px}
.muted,footer{color:#817b70;font-size:17px}.accent{color:#b66c53}.text{white-space:pre-wrap;overflow-wrap:anywhere}
.row{display:flex;gap:18px;align-items:center}.row>*{min-width:0}.ellipsis{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
img{max-width:100%;object-fit:contain}table{width:100%;border-collapse:collapse;table-layout:fixed}
td,th{text-align:left;padding:8px 10px;overflow-wrap:anywhere}th{color:#817b70;font-weight:400}
'''

def text(value) -> str:
    return escape(str(value if value is not None else ''), quote=True)

def document(blocks: list[str], *, width=900, css='', theme='') -> str:
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>'
            + ':root{--card-font:'+FONT+'}'+BASE_CSS+css+'</style></head><body>'
            +f'<main data-card class="sheet {theme}" style="width:{int(width)}px">'
            +''.join(blocks)+'</main></body></html>')

def chunks(items, size):
    values = list(items)
    return [values[i:i+size] for i in range(0, len(values), size)]

def paragraphs(value, *, chars=650, class_name='panel text') -> list[str]:
    value = str(value or '')
    return [f'<div class="{class_name}">{text(value[i:i+chars])}</div>' for i in range(0,len(value),chars)]

def image_uri(value: bytes | Path | None, *, width=1200, height=900, quality=84) -> str:
    if value is None:
        return ''
    from PIL import Image, ImageOps
    try:
        raw = value if isinstance(value, bytes) else Path(value).read_bytes()
        with Image.open(BytesIO(raw)) as image:
            if image.width*image.height > 40000000:
                return ''
            image = ImageOps.exif_transpose(image).convert('RGBA')
            matte = Image.new('RGBA', image.size, 'white')
            image = Image.alpha_composite(matte, image).convert('RGB')
            image.thumbnail((width,height),Image.Resampling.LANCZOS)
            output=BytesIO(); image.save(output,'JPEG',quality=quality,optimize=True)
        return 'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode('ascii')
    except (OSError,ValueError,TypeError):
        return ''

def scale_pages(pages: list[bytes], width: int) -> list[bytes]:
    from PIL import Image
    result=[]
    for png in pages:
        with Image.open(BytesIO(png)) as image:
            if image.width == width:
                result.append(png); continue
            image=image.resize((width,round(image.height*width/image.width)),Image.Resampling.LANCZOS)
            output=BytesIO(); image.save(output,'PNG'); result.append(output.getvalue())
    return result
