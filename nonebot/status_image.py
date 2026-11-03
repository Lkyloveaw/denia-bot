# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
# 修改：D 浅色泡泡 HTML/CSS/SVG 模板，同机 Playwright 排版，Pillow 仅处理素材及输出缩放。
from pathlib import Path
import math
from card_document import document, text, image_uri, chunks, scale_pages
from render_client import RenderError, render_document, single_page

_BACKGROUND_FILE=Path(__file__).resolve().parent/'resources/statuscard/denia-bubbles.jpg'
COLORS={'good':'#308977','warn':'#a77135','bad':'#bb4162'}
STYLE='''
.sheet{padding:50px;background:linear-gradient(#f4eef8,#f2ebf7);color:#352b49;font-size:18px}
.sheet>div,.sheet>section,.sheet>header{margin-bottom:22px}.panel{background:#fffcff;border-color:#e5d9ed;border-radius:20px;padding:20px}
.muted,footer{color:#6f5e82}.accent{color:#a65a89}h2{font-size:22px;font-weight:400}.hero{position:relative;height:483px}
.hero .art{position:absolute;left:-50px;top:-50px;width:1170px;height:840px;max-width:none;background-size:1430px auto;background-repeat:no-repeat;background-position:-140px 100px;mask-image:linear-gradient(to right,#0000000a 280px,#000 660px),linear-gradient(transparent 100px,#000 250px,#000 600px,transparent 820px);mask-composite:intersect;pointer-events:none}
.hero .identity{position:relative;z-index:1}.brand{display:flex;justify-content:space-between;font-size:18px}.avatar{width:64px;height:64px;border-radius:50%;object-fit:cover;background:#f0e3f0;display:flex;align-items:center;justify-content:center;color:#a65a89;font-size:28px}.identity>.row{margin-top:54px;gap:18px}.nick{font-size:58px;margin:16px 0 0;max-width:440px}.subtitle{font-size:24px}.online{display:inline-block;border:1px solid;border-radius:24px;padding:8px 22px;background:#fffcff;font-size:20px;margin:24px 0 12px}.meta{font-size:19px;margin:8px 0;max-width:440px}
.resources{position:relative;display:flex;gap:16px}.resource{flex:1;text-align:center;padding:12px 18px 18px}.resource svg{width:156px;height:156px;display:block;margin:auto}.detail{font-size:17px;color:#6f5e82}.resource h2{margin:4px 0}.disk{display:grid;grid-template-columns:175px 1fr 205px 75px;gap:15px;align-items:center;margin-top:12px}.track{height:16px;background:#ebe2f2;border-radius:10px;overflow:hidden}.fill{height:100%;border-radius:10px}.speeds{display:flex;gap:30px;font-size:34px;margin:6px 0 18px}.speeds>div{flex:1}.netmeta{font-size:17px;line-height:1.8}.columns{display:flex;gap:22px}.columns>section{flex:1;min-width:0}td,th{padding:6px 0;font-size:17px}.process td:first-child,.process th:first-child{width:58%}.process td:not(:first-child),.process th:not(:first-child){width:21%}.system-row{display:flex;gap:18px;justify-content:space-between;margin:15px 0;font-size:17px}.system-row span:first-child{color:#7e6c90;flex-shrink:0}.stats{display:flex;gap:12px}.stat{flex:1;padding:14px;text-align:center}.stat strong{display:block;font-size:25px;font-weight:400}.stat span{font-size:16px;color:#6f5e82}.foot{display:flex;justify-content:space-between;font-size:16px}.sites td{padding:8px 0}.sites td:first-child{width:55%}.sites td:last-child{text-align:right}.chart{width:100%;height:124px;display:block}.report-title{font-size:44px}.report-row{display:flex;gap:28px;margin:10px 0}.report-row span:first-child{width:260px;color:#6f5e82;flex-shrink:0}.report-row span:last-child{overflow-wrap:anywhere}.report-table td,.report-table th{padding:8px;font-size:17px}
'''
def number(value):
    try:
        n=float(value or 0)
        return n if math.isfinite(n) else 0
    except (TypeError,ValueError): return 0
def percent(value): return max(0,min(100,number(value)))
def shown(value): return text('--' if value is None else value)
def color(level, default='#308977'): return COLORS.get(str(level),default)
def trend(series, colors):
    values=[[max(0,number(v)) for v in row] for row in series]
    peak=max([v for row in values for v in row] or [1]) or 1
    lines=[]
    for row,stroke in zip(values,colors):
        if len(row)<2: continue
        points=' '.join(f'{i*1000/(len(row)-1):.2f},{100-v/peak*95:.2f}' for i,v in enumerate(row))
        lines.append(f'<polyline points="{points}" fill="none" stroke="{stroke}" stroke-width="3"/>')
    return '<svg class="chart" viewBox="0 0 1000 124" preserveAspectRatio="none"><path d="M0 33H1000M0 66H1000M0 100H1000" stroke="#e5d9ed"/>'+''.join(lines)+'</svg>'

def human_speed(value):
    value=number(value)
    for unit in ('B/s','KB/s','MB/s','GB/s'):
        if value<1024 or unit=='GB/s':
            return f'{value:.1f} {unit}'
        value/=1024

def build_status_html(view):
    bot=view.get('bot') or {}; network=view.get('network') or {}; compact=bool(view.get('compact'))
    background=image_uri(_BACKGROUND_FILE,width=1600,height=900,quality=84)
    avatar=image_uri(bot.get('avatar'),width=96,height=96)
    portrait=f'<img class="avatar" src="{avatar}">' if avatar else f'<div class="avatar">{text(str(bot.get("nick") or "B")[:1])}</div>'
    online=bot.get('online'); online_text='在线' if online is True else '离线' if online is False else '状态未知'
    online_color=COLORS['good'] if online is True else COLORS['bad'] if online is False else '#7e6c90'
    blocks=[f'<header class="hero"><div class="art" style="background-image:url(\'{background}\')"></div><div class="identity">'
        +f'<div class="brand"><span class="accent">DENIA / SERVER STATUS</span><span class="muted">{text(view.get("generated_at"))}</span></div>'
        +f'<div class="row">{portrait}<span class="accent">星穹观测</span></div><h1 class="nick ellipsis">{text(bot.get("nick") or "Bot")}</h1>'
        +f'<p class="subtitle muted">服务器状态</p><span class="online" style="color:{online_color}">● {online_text}</span>'
        +f'<p class="meta muted">已运行 {shown(view.get("uptime"))}</p>'
        +(f'<p class="meta muted ellipsis">QQ {text(bot["self_id"])}</p>' if bot.get('self_id') else '')
        +(f'<p class="meta muted ellipsis">{text(bot["protocol"])} {text(bot.get("protocol_version"))}</p>' if bot.get('protocol') else '')+'</div></header>']
    rings=[]
    for item in (view.get('resources') or [])[:3]:
        pct=percent(item.get('percent')); level=item.get('level') or ('bad' if pct>=90 else 'warn' if pct>=70 else 'good')
        accent={'cpu':'#308977','memory':'#a65a89','swap':'#9381c2'}.get(item.get('key'),'#308977')
        stroke=color(level,accent) if level in {'bad','warn'} else accent
        rings.append(f'<section class="panel resource"><svg viewBox="0 0 160 160"><circle cx="80" cy="80" r="66" stroke="#ebe2f2" stroke-width="12" fill="none"/>'
            +f'<circle cx="80" cy="80" r="66" stroke="{stroke}" stroke-width="12" fill="none" pathLength="100" stroke-dasharray="{pct} 100" transform="rotate(-90 80 80)" stroke-linecap="round"/>'
            +f'<text x="80" y="90" text-anchor="middle" fill="#352b49" font-size="32">{pct:.1f}%</text></svg><h2>{text(item.get("title"))}</h2><p class="detail ellipsis">{text(item.get("detail"))}</p></section>')
    if rings: blocks.append('<div class="resources">'+''.join(rings)+'</div>')
    for batch in chunks(view.get('disks') or [],12):
        rows=[]
        for disk in batch:
            c=color(disk.get('level'))
            rows.append(f'<div class="disk"><span class="ellipsis">{shown(disk.get("path"))}</span><div class="track"><div class="fill" style="width:{percent(disk.get("percent"))}%;background:{c}"></div></div>'
                +f'<span class="muted ellipsis">{shown(disk.get("used"))} / {shown(disk.get("total"))}</span><span style="color:{c}">{shown(disk.get("percent_text"))}</span></div>')
        blocks.append('<section class="panel"><h2>磁盘</h2>'+''.join(rows)+'</section>')
    io=view.get('disk_io') or {}; history=network.get('chart') or []
    graph=trend([[row.get('up',0) for row in history],[row.get('down',0) for row in history]],['#308977','#a65a89']) if history else ''
    if history:
        peak=max(max(number(row.get('up')),number(row.get('down'))) for row in history)
        graph=f'<div class="row muted" style="justify-content:space-between;margin-top:12px"><span>峰值 {human_speed(peak)}</span><span><span style="color:#308977">↑ 上行</span>　<span class="accent">↓ 下行</span></span></div>'+graph
    blocks.append('<section class="panel"><h2>网络</h2><div class="speeds">'
        +f'<div style="color:#308977">↑ {shown(network.get("up"))}</div><div class="accent">↓ {shown(network.get("down"))}</div></div>'
        +f'<p class="netmeta muted">本次开机累计 ↑ {shown(network.get("up_total"))} ↓ {shown(network.get("down_total"))}<br>'
        +f'{text(network.get("month") or "本月")}流量 ↑ {shown(network.get("month_up"))} ↓ {shown(network.get("month_down"))}<br>'
        +f'磁盘 IO 读 {shown(io.get("read"))} · 写 {shown(io.get("write"))} <span style="float:right">{text(network.get("interface"))}</span></p>'+graph+'</section>')
    for batch in chunks(view.get('websites') or [],15):
        rows=''.join(f'<tr><td class="ellipsis">{text(site.get("name") or "站点")}</td><td style="color:{COLORS["good"] if site.get("ok") else COLORS["bad"]}">{shown(site.get("status"))}</td><td>{shown(site.get("delay"))}</td></tr>' for site in batch)
        blocks.append('<section class="panel"><h2>网站延迟</h2><table class="sites">'+rows+'</table></section>')
    process=view.get('process') or {}; top=(process.get('top') or [])[:5 if compact else 10]
    rows=''.join(f'<tr><td class="ellipsis">{i+1}. {shown(item.get("name"))}</td><td>{shown(item.get("cpu"))}</td><td>{shown(item.get("mem"))}</td></tr>' for i,item in enumerate(top))
    proc='<section class="panel"><h2>Top 进程</h2>'+f'<p class="muted">共 {shown(process.get("total"))} 个 · 运行中 {shown(process.get("running"))}</p>'
    proc+='<table class="process"><tr><th>进程</th><th>CPU</th><th>内存</th></tr>'+rows+'</table>'+('' if top else '<p>暂无进程数据</p>')+'</section>'
    system=[] if compact else view.get('system') or []
    systems=['<section class="panel"><h2>系统信息</h2>'+''.join(f'<div class="system-row"><span>{text(row.get("label"))}</span><span class="ellipsis">{text(row.get("value"))}</span></div>' for row in batch)+'</section>' for batch in chunks(system,20)]
    blocks.append('<div class="columns">'+proc+(systems[0] if systems else '')+'</div>')
    blocks.extend(systems[1:])
    blocks.append('<div class="stats">'+''.join(f'<section class="panel stat"><strong>{shown(bot.get(key))}</strong><span>{label}</span></section>' for key,label in [('friends','好友'),('groups','群聊'),('members','群员'),('recv','收到'),('sent','回复')])+'</div>')
    blocks.append(f'<footer class="foot"><span>{text(view.get("footer") or "本机容器渲染 · 数据不出本机")}</span><span class="accent">达妮娅 · 浅色泡泡</span></footer>')
    return document(blocks,width=1170,css=STYLE)

def build_report_html(report):
    blocks=[f'<header><p class="accent">DENIA · REPORT</p><h1 class="report-title">{text(report.get("title") or "日报")}</h1><p class="muted">{text(report.get("subtitle"))} {text(report.get("host"))}</p></header>']
    for section in report.get('sections') or []:
        pieces=[]
        for batch in chunks(section.get('rows') or [],18):
            pieces.append(''.join(f'<div class="report-row"><span>{text(row[0])}</span><span>{text(row[1])}</span></div>' for row in batch))
        table=section.get('table') or {}
        for batch in chunks(table.get('rows') or [],18):
            pieces.append('<table class="report-table"><tr>'+''.join(f'<th>{text(v)}</th>' for v in table.get('headers') or [])+'</tr>'
                +''.join('<tr>'+''.join(f'<td>{text(cell)}</td>' for cell in row)+'</tr>' for row in batch)+'</table>')
        chart=section.get('chart') or {}; points=chart.get('points') or []
        if len(points)>1:
            pieces.append(trend([[row[1] for row in points]],['#a65a89'])+f'<p class="muted">{text(chart.get("label"))} · 峰值 {max(number(row[1]) for row in points):.1f}{text(chart.get("unit"))}</p>')
        if section.get('note'): pieces.append(f'<p class="muted text">{text(section["note"])}</p>')
        for i,piece in enumerate(pieces or ['']):
            blocks.append(f'<section class="panel"><h2 class="accent">{text(section.get("title"))}{" · 续" if i else ""}</h2>{piece}</section>')
    blocks.append(f'<footer>{text(report.get("footer"))}</footer>')
    return document(blocks,width=1170,css=STYLE)

def render_status_card_pages(view, *, width=1170):
    return scale_pages(render_document(build_status_html(view),width=1170,source='statuscard'),max(760,min(2880,int(width))))
def render_report_pages(report, *, width=1170):
    return scale_pages(render_document(build_report_html(report),width=1170,source='status_report'),max(760,min(2880,int(width))))
def render_status_card(view, *, width=1170):
    return single_page(render_status_card_pages(view,width=width))
def render_report(report, *, width=1170):
    return single_page(render_report_pages(report,width=width))
