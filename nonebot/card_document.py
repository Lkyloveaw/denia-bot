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
[data-render-continued]{font-size:18px;color:#6b645a;padding:0 2px}
'''

def text(value) -> str:
    return escape(str(value if value is not None else ''), quote=True)

def document(blocks: list[str], *, width=900, css='', theme='', page_label='') -> str:
    """page_label：分页时续页顶部显示「标签 · 第 2/3 页」（由渲染服务插入）；单页时不出现。"""
    label = f' data-page-label="{text(page_label)}"' if page_label else ''
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>'
            + ':root{--card-font:'+FONT+'}'+BASE_CSS+css+'</style></head><body>'
            +f'<main data-card{label} class="sheet {theme}" style="width:{int(width)}px">'
            +''.join(blocks)+'</main></body></html>')

def chunks(items, size):
    values = list(items)
    return [values[i:i+size] for i in range(0, len(values), size)]

_SENTENCE_ENDS = '。！？!?；;…'


def split_text(value, chars=650) -> list[str]:
    """Split long text into chunks of at most `chars` for pagination. Prefer paragraph breaks, then
    sentence ends, then commas; only hard-cut when none exist. Newlines at chunk edges are dropped,
    the gap between blocks already separates them."""
    value = str(value or '')
    chunks = []
    while len(value) > chars:
        window = value[:chars]
        cut = window.rfind('\n')
        if cut < chars // 3:
            cut = max(window.rfind(mark) for mark in _SENTENCE_ENDS) + 1
        if cut < chars // 3:
            cut = max(window.rfind(mark) for mark in '，,、 ') + 1
        if cut < chars // 3:
            cut = chars
        chunks.append(value[:cut].rstrip('\n'))
        value = value[cut:].lstrip('\n')
    if value:
        chunks.append(value)
    return [chunk for chunk in chunks if chunk.strip()]


def paragraphs(value, *, chars=650, class_name='panel text') -> list[str]:
    return [f'<div class="{class_name}">{text(chunk)}</div>' for chunk in split_text(value, chars)]

def image_uri(value: bytes | Path | None, *, width=1200, height=900, quality=84, crop=False,
              portrait_fit_height: int | None = None) -> str:
    """Downscale and inline as JPEG. crop=True center-crops to exactly width x height, for grid
    cells shown with object-fit:cover, so thumbnails are not shrunk first and then upscaled
    (which blurs them). With portrait_fit_height, fit an upright portrait inside width × that height
    while keeping its entire frame."""
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
            if portrait_fit_height is not None and image.height > image.width:
                image.thumbnail((width, portrait_fit_height), Image.Resampling.LANCZOS)
            elif crop:
                # Small originals are not upscaled: crop the largest centered region of the target aspect ratio.
                ratio = width / height
                crop_w = min(image.width, image.height * ratio)
                size = (max(1, round(min(width, crop_w))), max(1, round(min(height, crop_w / ratio))))
                image = ImageOps.fit(image, size, Image.Resampling.LANCZOS)
            else:
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
