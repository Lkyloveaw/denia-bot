# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。改为共享同机 Playwright 渲染。
from card_document import document, text, chunks
from render_client import render_document, single_page
import epic_free_logic as logic
WIDTH=760

def build_html(free, upcoming, width=WIDTH):
    blocks=['<header><p class="accent">EPIC / 喜加一</p><h1>本周免费游戏</h1><p class="muted">北京时间 · 数据来源 Epic Games Store</p></header>']
    for game in free or ():
        price=f'原价：{game["original_price"]} → 免费' if game.get('original_price') else '价格：免费'
        blocks.append('<section class="panel">'
            +f'<h2 class="text">{text(game.get("title"))}</h2>'
            +f'<p class="muted description">{text(game.get("description"))}</p>'
            +f'<p class="accent">{text(price)}</p><p>截止：{text(logic.format_beijing(game["end"]))}（北京时间）</p>'
            +f'<p class="muted text">领取：{text(game["url"])}</p></section>')
    if not free:
        blocks.append('<section class="panel">本周暂无免费游戏。</section>')
    for game in list(upcoming or ())[:3]:
        blocks.append(f'<section class="panel"><h2 class="accent">即将免费</h2><p class="text">{text(game["title"])}</p>'
            +f'<p>开始：{text(logic.format_beijing(game["start"]))}（北京时间）</p>'
            +f'<p class="muted text">领取：{text(game["url"])}</p></section>')
    blocks.append('<footer>换期才推送 · 时间均为北京时间</footer>')
    return document(blocks,width=width,css='.description{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}p{margin:6px 0}')

def render_pages(free, upcoming, width=WIDTH):
    return render_document(build_html(free,upcoming,width),width=width,source='epic')

def render(free, upcoming, width=WIDTH):
    return single_page(render_pages(free,upcoming,width))
