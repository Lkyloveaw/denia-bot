"""Weather bulletin layout approved from the lightning and wind map previews."""
from __future__ import annotations

import html
import math
import re

import disaster_china_logic as china
from disaster_weather_map import WeatherMap


STYLE = """
*{box-sizing:border-box}html,body{margin:0;padding:0}body{width:900px;background:#f3efe5;color:#292b28;font-family:"Noto Sans CJK SC","WenQuanYi Micro Hei","Microsoft YaHei",sans-serif}
.card{position:relative;width:900px;height:var(--height);padding:44px 54px 32px 66px;background:#f7f4ec;border-left:13px solid var(--accent);display:flex;flex-direction:column}
.top{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #373c35;padding-bottom:17px;font-size:17px;color:#615f57;letter-spacing:1px}
.badge{border:1px solid #c9c4b7;border-radius:5px;padding:5px 10px;font-size:13px;letter-spacing:1px;color:#6a675f}
.eyebrow{font-size:14px;letter-spacing:1px;color:#82796b;margin:28px 0 9px}
.place{font-size:37px;font-weight:750;line-height:1.4;margin:0 0 23px;letter-spacing:-.5px;overflow-wrap:anywhere;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
.hero{flex-shrink:0;background:var(--soft);border:1px solid var(--border);border-radius:18px;min-height:294px;display:grid;grid-template-columns:148px 160px minmax(0,1fr);align-items:center;padding:20px 22px;gap:18px}
.icon{width:148px;height:148px}.icon svg{width:100%;height:100%;display:block}
.hero-copy{min-width:0}.hero h2{font-size:54px;line-height:1.25;margin:0 0 15px;font-weight:800;letter-spacing:3px;color:#272c29;overflow-wrap:anywhere}.hero h2.long-type{font-size:31px;letter-spacing:0}
.level{display:inline-flex;align-items:center;gap:8px;border:1.5px solid var(--accent);color:var(--ink);background:#ffffff77;border-radius:7px;padding:7px 10px;font-size:23px;font-weight:700;white-space:nowrap}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--dot)}
.map-panel{min-width:0;align-self:stretch;border-left:1px solid var(--border);padding-left:18px;display:flex;flex-direction:column;justify-content:center}
.map-heading{display:flex;justify-content:space-between;align-items:center;gap:8px;color:#6a6d61;font-size:12px;letter-spacing:1px;padding:0 8px}.map-heading b{font-size:13px;color:#565d54;letter-spacing:1px;font-weight:600;max-width:75%;overflow-wrap:anywhere}
.map-art{display:block;width:100%;height:199px;margin:3px 0 2px}.map-caption{display:flex;justify-content:center;align-items:center;gap:6px;font-size:11px;letter-spacing:.5px;color:#777769;line-height:1.5}
.map-key{display:inline-block;width:9px;height:9px;background:var(--dot);border:1px solid var(--accent);border-radius:2px}.map-empty{height:199px;display:flex;flex-direction:column;justify-content:center;align-items:center;color:#8a877a;font-size:14px;gap:9px}.map-empty small{font-size:11px}
.facts{display:grid;grid-template-columns:1fr 1fr;column-gap:28px;margin-top:23px;border-top:1px solid #d9d3c5;border-bottom:1px solid #d9d3c5}
.fact{padding:15px 0 17px;min-width:0}.label{display:block;font-size:13px;letter-spacing:1px;color:#7e786d;margin-bottom:6px}.value{font-size:19px;line-height:1.5;font-weight:650;overflow-wrap:anywhere;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
.record{padding-top:25px}.section-title{display:flex;align-items:center;gap:10px;font-size:16px;color:#767064;margin:0 0 12px;font-weight:500;letter-spacing:1px}.section-title:before{content:"";width:4px;height:17px;background:var(--accent)}
.record p{font-size:21px;line-height:1.75;margin:0;color:#373b35;overflow-wrap:anywhere}.note{font-size:12px;line-height:1.8;color:#888173;margin:13px 0 0}
.foot{margin-top:auto;padding-top:17px;border-top:1px solid #d9d3c5;display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#827c70;gap:14px}.foot b{font-weight:500;color:#676b61}
"""

LIGHTNING = '''<path d="M62 121c-18 0-29-12-29-27 0-16 12-28 28-29 4-21 22-36 44-36 24 0 42 15 47 37 19-2 35 11 35 28 0 16-12 27-29 27" fill="#fffdf5" stroke="var(--accent)" stroke-width="6" stroke-linecap="round"/>
<path d="M111 91 82 140h28l-10 43 48-65h-29l13-27Z" fill="var(--dot)" stroke="var(--accent)" stroke-width="4" stroke-linejoin="round"/>
<path d="m56 143-9 13m112-14 10 14" stroke="var(--accent)" stroke-width="5" stroke-linecap="round"/>'''
WIND = '''<g fill="none" stroke="var(--accent)" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"><path d="M40 86h98c29 0 29-39 6-39-10 0-16 6-17 14"/><path d="M31 111h141c27 0 28-36 5-36-9 0-14 4-17 12"/><path d="M46 136h78c33 0 33 40 8 40-10 0-17-6-17-15"/><path d="M61 160h19" opacity=".48"/></g>'''
GENERIC = '''<path d="M110 40 184 171H36Z" fill="#fffdf5" stroke="var(--accent)" stroke-width="7" stroke-linejoin="round"/><path d="M110 86v40" stroke="var(--accent)" stroke-width="10" stroke-linecap="round"/><circle cx="110" cy="148" r="6" fill="var(--accent)"/>'''


def _e(value: str) -> str:
    return html.escape(value, quote=True)


def _body(alert: china.WeatherAlert, tone: str) -> tuple[str, bool]:
    text = ' '.join(china.weather_lead(alert, tone).split())
    return (text[:599]+'…', True) if len(text) > 600 else (text, False)


def _region(alert: china.WeatherAlert) -> str:
    parts = [p for p in (alert.province, alert.city, alert.district) if p and p not in ('省直辖县级行政区划', '自治区直辖县级行政区划', '市辖区')]
    region = ''.join(dict.fromkeys(parts))
    if not region:
        region = re.split(r'发布|升级|变更|解除|继续', alert.title or alert.headline, maxsplit=1)[0]
    return region[:80] or '气象预警'


def card_height(alert: china.WeatherAlert, tone: str = 'neutral') -> int:
    text, _ = _body(alert, tone)
    return 920 + max(0, math.ceil(len(text)/30)-2)*38 + (60 if len(_region(alert)) > 18 else 0)


def weather_html(alert: china.WeatherAlert, tone: str = 'neutral', weather_map: WeatherMap | None = None) -> str:
    palettes = {
        '黄色': ('#ad8322','#856417','#fcf4d9','#e9d596','#d7a925'),
        '蓝色': ('#397c9e','#306d8d','#eaf3f8','#bcd5e3','#4b91b7'),
        '橙色': ('#b96a22','#91501a','#fcf0df','#e7c399','#df983c'),
        '红色': ('#b24438','#92372f','#faeae5','#e4b7ac','#d96b59'),
    }
    palette = palettes.get(alert.color, ('#787b70','#62665d','#eeeee6','#d0d2c5','#979e88'))
    variables = ';'.join('--'+key+':'+value for key,value in zip(('accent','ink','soft','border','dot'),palette))
    hazard = china.weather_type_name(alert)
    if hazard not in china.WEATHER_TYPE_WORDS:
        hazard = '气象预警'
    icon = LIGHTNING if hazard in ('雷电', '强对流') else WIND if '大风' in hazard else GENERIC
    icon = f'<svg viewBox="0 0 220 220" aria-label="{_e(hazard)}类型示意" role="img"><circle cx="110" cy="110" r="100" fill="var(--dot)" opacity=".12"/>{icon}</svg>'
    level = alert.color+'预警' if alert.color != '未知' else '等级待确认'
    context = _e(weather_map.context) if weather_map else '发布地区'
    map_art = weather_map.svg() if weather_map else '<div class="map-empty"><span>暂无行政边界</span><small>请以官方发布地区为准</small></div>'
    map_panel = f'<aside class="map-panel"><div class="map-heading"><span>区域定位</span><b>{context}</b></div>{map_art}<div class="map-caption"><span class="map-key"></span>预警行政区域示意</div></aside>'
    region = _region(alert)
    body, shortened = _body(alert, tone)
    published = alert.issue_at or alert.effective_at
    date_text = f'发布于 {_e(published)}（北京时间）' if published else '发布时间未提供'
    heading = '达妮娅 · 后续解读' if tone == 'character' else '气象预警'
    body_label = '预警内容' if alert.description else '预警标题'
    more = '<p class="note">完整内容见随附文字。</p>' if shortened else ''
    attribution = '行政边界：阿里云 DataV · 局部示意' if weather_map else '类型图标为示意 · 颜色表示预警等级'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>{STYLE}</style></head>
<body><article class="card" style="{variables};--height:{card_height(alert,tone)}px">
<header class="top"><span>{heading}</span><span class="badge">公开数据</span></header>
<div class="eyebrow">{date_text}</div><h1 class="place">{_e(region)}</h1>
<section class="hero"><div class="icon">{icon}</div><div class="hero-copy"><h2 class="{'long-type' if len(hazard)>2 else ''}">{_e(hazard)}</h2><div class="level"><span class="dot"></span>{_e(level)}</div></div>{map_panel}</section>
<section class="facts"><div class="fact"><span class="label">预警地区</span><div class="value">{_e(region)}</div></div><div class="fact"><span class="label">信息来源</span><div class="value">中国气象局 / Jian Project</div></div></section>
<section class="record"><h3 class="section-title">{body_label}</h3><p>{_e(body)}</p></section>{more}
<p class="note">地图高亮为发布地区的行政范围，不代表雷暴或大风的实际覆盖范围。</p>
<footer class="foot"><b>{attribution}</b><span>以官方发布为准</span></footer>
</article></body></html>'''
