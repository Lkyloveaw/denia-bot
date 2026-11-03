"""Weather and group-topic HTML cards rendered by the shared Playwright container."""
from card_document import document, text, paragraphs
from render_client import render_document, single_page

def build_html(value, heading='WEATHER  /  天气', subtitle='和风天气 · 天气信息'):
    lines=[line.strip() for line in str(value).splitlines() if line.strip()]
    if len(lines)<2:
        raise ValueError('天气内容为空')
    sources=[line for line in lines[1:] if line.startswith('数据来源：')]
    entries=[line for line in lines[1:] if not line.startswith('数据来源：')]
    if not entries:
        raise ValueError('天气内容为空')
    blocks=[f'<header><p class="accent">{text(heading)}</p><h1 class="text">{text(lines[0])}</h1><p class="muted">{text(subtitle)}</p></header>']
    for line in entries:
        blocks.extend(paragraphs(line, chars=500))
    blocks.extend(paragraphs('\n'.join(sources),class_name='muted text'))
    return document(blocks,width=760)

def render_pages(value, heading='WEATHER  /  天气', subtitle='和风天气 · 天气信息'):
    return render_document(build_html(value,heading,subtitle),width=760,
                           source='group_topics' if heading.startswith('GROUP') else 'weather')

def render(value, heading='WEATHER  /  天气', subtitle='和风天气 · 天气信息'):
    return single_page(render_pages(value,heading,subtitle))
