#!/usr/bin/env python3
"""PROTOTYPE ONLY — 生成四类灾害卡片原型（固定样例，非生产代码）。

问题：四类灾害消息在群内/面板里应该长什么样？
产出：disaster-cards.html，单文件、无外部资源，?type=&variant= 切换。
三种结构：A Poster / B Bulletin / C Dashboard。
"""
from __future__ import annotations

import html
from pathlib import Path

OUT = Path(__file__).resolve().parent
KINDS = ('weather', 'typhoon', 'tsunami', 'earthquake')
VARIANTS = {'A': 'Poster', 'B': 'Bulletin', 'C': 'Dashboard'}

DATA = {
    'weather': {
        'title': '强对流黄色预警',
        'kicker': '中国气象局 · 气象预警',
        'badge': '黄色',
        'source': '中国气象局 / Jian Project',
        'lead': '铜山区气象台 2026 年 07 月 29 日 12 时 41 分发布强对流黄色预警信号：预计今天午后到上半夜我区部分镇（街道）将出现雷电，并伴有短时强降水、局地 7–9 级雷暴大风等强对流天气。',
        'facts': [
            ('等级', '黄色'), ('类型', '强对流'),
            ('地区', '江苏 · 徐州 · 铜山'), ('生效', '2026-07-29 12:41'),
        ],
    },
    'typhoon': {
        'title': '桦加沙 · 超强台风',
        'kicker': '中国气象局 · 台风路径',
        'badge': '超强台风',
        'source': '中国气象局 / EQSC',
        'lead': '预计 9 月 25 日 23 时以热带低压强度接近北部湾；当前位于西北太平洋，风圈半径较大，路径仍在调整。',
        'facts': [
            ('编号', '2518 · 桦加沙 / RAGASA'), ('等级', '超强台风'),
            ('中心', '北纬 19.5° / 东经 119.9°'), ('中心气压', '920 hPa'),
            ('最大风速', '58 m/s'), ('移动', '西北 20 km/h'),
            ('更新', '2025-09-22 23:00'), ('预计', '09-25 23:00 热带低压'),
        ],
    },
    'tsunami': {
        'title': '海啸黄色警报',
        'kicker': '自然资源部海啸预警中心',
        'badge': '黄色',
        'source': '自然资源部海啸预警中心 / Jian Project',
        'lead': '受日本本州东岸 M7.8 地震影响，预计海啸波将于 18:30 前后到达我国东部沿海；浙江、上海、江苏南部沿海需注意。',
        'facts': [
            ('等级', '黄色'), ('报次', '第 2 报'),
            ('震中', '日本本州东岸'), ('发震', '2026-07-29 12:00'),
            ('震级', 'M7.8'), ('深度', '10 km'),
            ('预计到达', '2026-07-29 18:30'), ('影响', '浙江 / 上海 / 江苏南部沿海'),
        ],
    },
    'earthquake': {
        'title': '四川成都 M6.1',
        'kicker': '中国地震台网 · 地震预警',
        'badge': '第 3 报 · 最终报',
        'source': '中国地震台网 / Wolfx',
        'lead': 'CENC 列表：四川成都 M6.1；云南大理 M4.2；青海海西 M3.6。EEW 首报与最终报优先推送，中间报不刷屏。',
        'facts': [
            ('来源', 'Wolfx · CENC EEW'), ('报次', '第 3 报 · 最终报'),
            ('震级', 'M6.1'), ('位置', '四川成都'),
            ('发震', '2026-10-31 12:00:00'), ('深度', '12 km'),
            ('最大烈度', '6.0'), ('距本地', '约 120 km'),
        ],
    },
}


def esc(value: str) -> str:
    return html.escape(str(value), quote=True)


def fact(label: str, value: str) -> str:
    return f'<div class="fact"><span class="fact-label">{esc(label)}</span><b class="fact-value">{esc(value)}</b></div>'


def tile(label: str, value: str) -> str:
    return f'<div class="c-tile"><span>{esc(label)}</span><b>{esc(value)}</b></div>'


def facts(kind: str, limit: int | None = None) -> str:
    items = DATA[kind]['facts'][:limit] if limit else DATA[kind]['facts']
    return ''.join(fact(label, value) for label, value in items)


def tiles(kind: str, limit: int = 4) -> str:
    return ''.join(tile(label, value) for label, value in DATA[kind]['facts'][:limit])


def weather_visual() -> str:
    return """
    <div class="viz viz-weather">
      <div class="w-word">黄色</div>
      <div class="w-sub">强对流</div>
      <div class="w-alert" aria-hidden="true">⚠</div>
      <div class="w-bars" aria-hidden="true">
        <i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i>
      </div>
    </div>
    """


def typhoon_visual() -> str:
    return """
    <svg class="viz" viewBox="0 0 520 360" role="img" aria-label="台风路径与风圈示意">
      <rect class="viz-bg" x="0" y="0" width="520" height="360" rx="18"/>
      <g class="viz-grid">
        <path d="M40 60H480M40 140H480M40 220H480M40 300H480"/>
        <path d="M120 40V320M240 40V320M360 40V320M480 40V320"/>
      </g>
      <circle class="viz-wind" cx="338" cy="168" r="72"/>
      <circle class="viz-wind" cx="338" cy="168" r="46"/>
      <circle class="viz-wind" cx="338" cy="168" r="24"/>
      <polyline class="viz-track" points="72,292 148,258 214,226 278,196 338,168 404,132 462,92"/>
      <circle class="viz-now" cx="338" cy="168" r="8"/>
      <circle class="viz-future" cx="462" cy="92" r="7"/>
      <text class="viz-label" x="350" y="160">当前</text>
      <text class="viz-label" x="474" y="84">预计</text>
      <text class="viz-small" x="40" y="336">西北太平洋 · 固定样例路径（非实时）</text>
    </svg>
    """


def tsunami_visual() -> str:
    return """
    <svg class="viz" viewBox="0 0 520 360" role="img" aria-label="海啸影响沿海示意">
      <rect class="viz-bg" x="0" y="0" width="520" height="360" rx="18"/>
      <path class="viz-coast" d="M40 306 C120 268 168 286 224 246 S330 206 480 238 L480 336 L40 336 Z"/>
      <path class="viz-wave" d="M40 206 C100 190 126 218 180 202 S260 174 318 194 S414 214 480 190"/>
      <path class="viz-wave" d="M40 226 C100 210 126 238 180 222 S260 194 318 214 S414 234 480 210"/>
      <circle class="viz-impact" cx="150" cy="286" r="6"/>
      <circle class="viz-impact" cx="238" cy="268" r="6"/>
      <circle class="viz-impact" cx="326" cy="248" r="6"/>
      <text class="viz-label" x="126" y="316">浙江</text>
      <text class="viz-label" x="214" y="300">上海</text>
      <text class="viz-label" x="302" y="278">江苏南部</text>
      <text class="viz-small" x="40" y="336">官方图件占位 · 生产接海啸中心图件</text>
    </svg>
    """


def quake_visual() -> str:
    return """
    <svg class="viz" viewBox="0 0 520 360" role="img" aria-label="地震位置示意">
      <rect class="viz-bg" x="0" y="0" width="520" height="360" rx="18"/>
      <path class="viz-map" d="M112 92 L178 66 L244 82 L294 66 L354 92 L410 138 L432 204 L404 264 L348 300 L282 292 L236 318 L178 296 L126 252 L96 192 Z"/>
      <circle class="viz-ring" cx="298" cy="196" r="46"/>
      <circle class="viz-ring" cx="298" cy="196" r="28"/>
      <circle class="viz-now" cx="298" cy="196" r="8"/>
      <text class="viz-label" x="312" y="190">四川成都</text>
      <text class="viz-small" x="40" y="336">地图示意（非地理精确）· 生产接地图瓦片/列表</text>
    </svg>
    """


def visual(kind: str) -> str:
    return {
        'weather': weather_visual,
        'typhoon': typhoon_visual,
        'tsunami': tsunami_visual,
        'earthquake': quake_visual,
    }[kind]()


def variant_a(kind: str) -> str:
    data = DATA[kind]
    return f"""
    <article class="card variant variant-a">
      <div class="a-top"><span class="kicker">{esc(data['kicker'])}</span><span class="badge">{esc(data['badge'])}</span></div>
      <h1 class="a-title">{esc(data['title'])}</h1>
      <div class="a-visual">{visual(kind)}</div>
      <p class="a-lead">{esc(data['lead'])}</p>
      <div class="a-facts">{facts(kind, 6)}</div>
      <div class="a-foot"><span>{esc(data['source'])}</span><span>固定样例 · PROTOTYPE</span></div>
    </article>
    """


def variant_b(kind: str) -> str:
    data = DATA[kind]
    return f"""
    <article class="card variant variant-b">
      <div class="b-rail"></div>
      <div class="b-inner">
        <div class="b-top"><span>{esc(data['kicker'])}</span><span>固定样例 / PROTOTYPE</span></div>
        <h1 class="b-title">{esc(data['title'])}</h1>
        <div class="b-stamp">{esc(data['badge'])}</div>
        <div class="b-meta">{facts(kind)}</div>
        <div class="b-visual">{visual(kind)}</div>
        <p class="b-text">{esc(data['lead'])}</p>
        <div class="b-foot"><span>{esc(data['source'])}</span><span>仅供参考，以官方发布为准</span></div>
      </div>
    </article>
    """


def c_list(kind: str) -> str:
    if kind == 'weather':
        return ''.join(fact(label, value) for label, value in (
            ('防御', '减少户外活动'), ('远离', '临时搭建物 / 树木'),
            ('注意', '雷电、短时强降水'),
        ))
    return ''.join(fact(label, value) for label, value in DATA[kind]['facts'][4:])


def variant_c(kind: str) -> str:
    data = DATA[kind]
    return f"""
    <article class="card variant variant-c">
      <div class="c-top"><span class="c-live">● LIVE SAMPLE</span><span>{esc(data['kicker'])}</span><span>{esc(data['badge'])}</span></div>
      <div class="c-grid">
        <div class="c-visual">{visual(kind)}</div>
        <div class="c-side">{tiles(kind, 4)}</div>
      </div>
      <div class="c-lower">
        <div class="c-text"><h2>发生了什么</h2><p>{esc(data['lead'])}</p></div>
        <div class="c-list">{c_list(kind)}</div>
      </div>
      <div class="c-foot"><span>{esc(data['source'])}</span><span>固定样例 · 非实时 · PROTOTYPE</span></div>
    </article>
    """


CSS = r"""
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;background:#0b1218;color:#f5f1e8}
.proto-head{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:12px 22px;background:#111820;border-bottom:1px solid #26333e}
.proto-tag{display:inline-block;background:#f0c15a;color:#111;font-size:10px;font-weight:800;letter-spacing:.14em;padding:4px 7px;border-radius:4px;margin-right:8px}
.proto-title{font-size:13px;color:#c7d0d8}.proto-title b{color:#fff}
.type-tabs{display:flex;gap:6px;flex-wrap:wrap}
.type-tabs button{border:1px solid #30404c;background:#16212a;color:#aab6c2;padding:6px 11px;border-radius:999px;font-size:12px;cursor:pointer}
.type-tabs button.active{background:#e0a23a;border-color:#e0a23a;color:#111;font-weight:700}
.proto-note{margin-left:auto;color:#7f8c99;font-size:11px}
.stage{max-width:960px;margin:0 auto;padding:26px 20px 120px}
.type{display:none}
body[data-type="weather"] .type-weather{display:block}
body[data-type="typhoon"] .type-typhoon{display:block}
body[data-type="tsunami"] .type-tsunami{display:block}
body[data-type="earthquake"] .type-earthquake{display:block}
.variant{display:none;width:900px;max-width:100%;height:1200px;margin:0 auto;position:relative;overflow:hidden;border-radius:22px;box-shadow:0 30px 90px rgba(0,0,0,.45)}
body[data-variant="A"] .variant-a{display:flex}
body[data-variant="B"] .variant-b{display:flex}
body[data-variant="C"] .variant-c{display:flex}
.type-weather{--accent:#e0a23a;--accent-soft:rgba(224,162,58,.16)}
.type-typhoon{--accent:#e05a47;--accent-soft:rgba(224,90,71,.16)}
.type-tsunami{--accent:#4aa3c7;--accent-soft:rgba(74,163,199,.16)}
.type-earthquake{--accent:#e05a47;--accent-soft:rgba(224,90,71,.16)}
.kicker{font-size:11px;letter-spacing:.18em;color:var(--muted);text-transform:uppercase}
.badge{font-size:12px;font-weight:700;letter-spacing:.06em;color:#061014;background:var(--accent);padding:6px 11px;border-radius:999px;white-space:nowrap}
.fact{display:flex;flex-direction:column;gap:5px;min-width:0}
.fact-label{font-size:10px;letter-spacing:.12em;color:var(--muted);text-transform:uppercase}
.fact-value{font-size:15px;line-height:1.4;font-weight:600;overflow-wrap:anywhere}
.viz{width:100%;height:100%;display:block}
.viz-bg{fill:var(--panel)}
.viz-grid path{fill:none;stroke:var(--line);stroke-width:1}
.viz-track{fill:none;stroke:var(--accent);stroke-width:3;stroke-linecap:round;stroke-linejoin:round}
.viz-wind{fill:none;stroke:var(--accent);stroke-width:1.5;stroke-dasharray:5 6;opacity:.7}
.viz-now{fill:var(--accent);stroke:var(--panel);stroke-width:3}
.viz-future{fill:var(--panel);stroke:var(--accent);stroke-width:2}
.viz-label{fill:var(--ink);font-size:12px;font-family:inherit}
.viz-small{fill:var(--muted);font-size:11px;font-family:inherit}
.viz-coast{fill:var(--panel);stroke:var(--line);stroke-width:1.5}
.viz-wave{fill:none;stroke:var(--accent);stroke-width:2;stroke-dasharray:7 6;opacity:.75}
.viz-impact{fill:var(--accent);stroke:var(--panel);stroke-width:2}
.viz-map{fill:var(--panel);stroke:var(--line);stroke-width:1.5}
.viz-ring{fill:none;stroke:var(--accent);stroke-width:1.5;opacity:.55}
.viz-weather{position:relative;min-height:280px;background:linear-gradient(135deg,var(--accent-soft),transparent 66%),var(--panel);border:1px solid var(--line);overflow:hidden}
.w-word{position:absolute;left:34px;top:30px;font-size:122px;line-height:.9;font-weight:800;letter-spacing:-.06em;color:var(--accent)}
.w-sub{position:absolute;left:38px;bottom:104px;font-size:30px;font-weight:700;color:var(--ink)}
.w-alert{position:absolute;right:36px;top:24px;font-size:110px;line-height:1;color:var(--accent);opacity:.28}
.w-bars{position:absolute;left:38px;right:38px;bottom:34px;height:44px;display:flex;align-items:flex-end;gap:8px}
.w-bars i{flex:1;background:var(--accent);opacity:.5;border-radius:4px 4px 0 0}
.w-bars i:nth-child(1){height:26%}.w-bars i:nth-child(2){height:48%}.w-bars i:nth-child(3){height:34%}.w-bars i:nth-child(4){height:66%}.w-bars i:nth-child(5){height:44%}.w-bars i:nth-child(6){height:82%}.w-bars i:nth-child(7){height:56%}.w-bars i:nth-child(8){height:38%}
.variant-a{--bg:#0b1218;--panel:#121d26;--ink:#f5f1e8;--muted:#9aa7b3;--line:#24333f;background:var(--bg);color:var(--ink);padding:54px 62px 38px;flex-direction:column}
.a-top{display:flex;align-items:center;justify-content:space-between;gap:16px}
.a-title{font-size:52px;line-height:1.14;letter-spacing:-.025em;font-weight:760;margin:24px 0 0;max-width:760px}
.a-visual{flex:1;min-height:0;margin:26px 0 20px;border-radius:18px;overflow:hidden;border:1px solid var(--line)}
.a-lead{font-size:16px;line-height:1.75;color:var(--muted);margin:0 0 18px;max-width:780px}
.a-facts{display:grid;grid-template-columns:1fr 1fr;gap:14px 28px;border-top:1px solid var(--line);padding-top:18px}
.a-foot{display:flex;justify-content:space-between;gap:12px;margin-top:20px;font-size:11px;color:var(--muted)}
.variant-b{--bg:#f3efe4;--panel:#fffdf7;--ink:#1b1b18;--muted:#6b655b;--line:#d9d2c3;background:var(--bg);color:var(--ink);padding:0;font-family:"Noto Serif CJK SC","Songti SC",serif}
.variant-b .w-word{font-size:84px;left:24px;top:22px}
.variant-b .w-sub{left:28px;bottom:64px;font-size:22px}
.variant-b .w-alert{font-size:76px;right:24px;top:16px}
.variant-b .w-bars{left:28px;right:28px;bottom:24px;height:30px}
.b-rail{width:16px;flex:0 0 16px;background:var(--accent)}
.b-inner{flex:1;padding:44px 54px 36px;position:relative;display:flex;flex-direction:column;min-width:0}
.b-top{display:flex;justify-content:space-between;gap:12px;font-size:11px;letter-spacing:.1em;color:var(--muted);border-bottom:2px solid var(--ink);padding-bottom:11px}
.b-title{font-size:40px;line-height:1.3;margin:20px 0 16px;max-width:620px;font-weight:700}
.b-stamp{position:absolute;top:148px;right:52px;border:4px double var(--accent);color:var(--accent);padding:10px 16px;font-size:22px;font-weight:800;letter-spacing:.08em;transform:rotate(-7deg);border-radius:8px;background:rgba(255,255,255,.25)}
.b-meta{display:grid;grid-template-columns:1fr 1fr;gap:0 24px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:6px 0 16px}
.b-meta .fact{border-bottom:1px solid var(--line);padding:8px 0}
.b-meta .fact:nth-last-child(-n+2){border-bottom:0}
.b-meta .fact-label{font-size:10px}.b-meta .fact-value{font-size:15px}
.b-visual{flex:1;min-height:250px;margin:8px 0 14px;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--panel)}
.b-text{font-size:14px;line-height:1.9;margin:0;color:var(--ink)}
.b-foot{display:flex;justify-content:space-between;gap:12px;margin-top:auto;border-top:1px solid var(--line);padding-top:11px;font-size:11px;color:var(--muted)}
.variant-c{--bg:#101418;--panel:#171d23;--ink:#eef2f5;--muted:#8e9ba6;--line:#2a343d;background:var(--bg);color:var(--ink);padding:28px 32px 22px;flex-direction:column}
.c-top{display:flex;justify-content:space-between;align-items:center;gap:12px;border-bottom:1px solid var(--line);padding-bottom:11px;font-size:11px;color:var(--muted)}
.c-live{color:var(--accent);font-weight:800;letter-spacing:.14em}
.c-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:14px;margin-top:14px;flex:1;min-height:0}
.c-visual{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--panel);min-height:0}
.c-side{display:grid;grid-template-columns:1fr 1fr;grid-auto-rows:1fr;gap:10px;min-height:0}
.c-tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:11px 13px;display:flex;flex-direction:column;justify-content:space-between;min-width:0}
.c-tile span{font-size:10px;letter-spacing:.1em;color:var(--muted);text-transform:uppercase}
.c-tile b{font-size:17px;line-height:1.35;overflow-wrap:anywhere}
.c-lower{display:grid;grid-template-columns:1.2fr .8fr;gap:14px;margin-top:14px;min-height:198px}
.c-text,.c-list{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;min-width:0}
.c-text h2{font-size:15px;margin:0 0 8px;color:var(--ink)}
.c-text p{font-size:13px;line-height:1.7;color:var(--muted);margin:0}
.c-list .fact{padding:4px 0}
.c-list .fact-value{font-size:13px}
.c-foot{display:flex;justify-content:space-between;gap:12px;margin-top:12px;font-size:10px;color:var(--muted)}
.proto-bar{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:50;display:flex;align-items:center;gap:10px;background:#f4f0e6;color:#111;border:1px solid #111;border-radius:999px;padding:7px 9px;box-shadow:0 12px 34px rgba(0,0,0,.42)}
.proto-bar button{border:0;background:#111;color:#fff;width:34px;height:34px;border-radius:50%;font-size:17px;cursor:pointer}
.proto-bar-label{font-size:11px;line-height:1.25;min-width:116px;text-align:center}
.proto-bar-label strong{display:block;font-size:9px;letter-spacing:.16em;color:#8a5a12}
"""

JS = r"""
const TYPES = ['weather','typhoon','tsunami','earthquake'];
const VARIANTS = {A:'Poster', B:'Bulletin', C:'Dashboard'};
const params = new URLSearchParams(location.search);
let type = TYPES.includes(params.get('type')) ? params.get('type') : 'weather';
let variant = VARIANTS[params.get('variant')] ? params.get('variant') : 'A';
function render(){
  document.body.dataset.type = type;
  document.body.dataset.variant = variant;
  document.querySelectorAll('[data-type-tab]').forEach(button => {
    button.classList.toggle('active', button.dataset.typeTab === type);
  });
  const label = document.getElementById('variantLabel');
  if (label) label.textContent = variant + ' (' + VARIANTS[variant] + ')';
  const url = new URL(location.href);
  url.searchParams.set('type', type);
  url.searchParams.set('variant', variant);
  history.replaceState(null, '', url);
}
function cycle(step){
  const keys = Object.keys(VARIANTS);
  let index = keys.indexOf(variant);
  index = (index + step + keys.length) % keys.length;
  variant = keys[index];
  render();
}
document.querySelectorAll('[data-type-tab]').forEach(button => {
  button.addEventListener('click', () => { type = button.dataset.typeTab; render(); });
});
document.getElementById('prev').addEventListener('click', () => cycle(-1));
document.getElementById('next').addEventListener('click', () => cycle(1));
window.addEventListener('keydown', event => {
  if (event.target.closest('input,textarea,[contenteditable]')) return;
  if (event.key === 'ArrowLeft') { event.preventDefault(); cycle(-1); }
  if (event.key === 'ArrowRight') { event.preventDefault(); cycle(1); }
});
render();
"""


def build() -> str:
    sections = []
    for kind in KINDS:
        variants = ''.join(fn(kind) for fn in (variant_a, variant_b, variant_c))
        sections.append(f"""
        <section class="type type-{kind}" data-kind="{kind}">
          {variants}
        </section>
        """)
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PROTOTYPE · 灾害预警卡片</title>
<!-- PROTOTYPE ONLY：固定样例、无外部资源、无持久化；不并入生产代码。 -->
<style>{CSS}</style>
</head>
<body data-type="weather" data-variant="A">
<header class="proto-head">
  <div class="proto-title"><span class="proto-tag">PROTOTYPE</span><b>灾害预警卡片</b> · 固定样例</div>
  <nav class="type-tabs" aria-label="灾害类型">
    <button type="button" data-type-tab="weather">气象预警</button>
    <button type="button" data-type-tab="typhoon">台风</button>
    <button type="button" data-type-tab="tsunami">海啸</button>
    <button type="button" data-type-tab="earthquake">地震</button>
  </nav>
  <div class="proto-note">?type=&amp;variant= 可分享 · ← → 切换风格</div>
</header>
<main class="stage">
  {''.join(sections)}
</main>
<div class="proto-bar" role="group" aria-label="风格原型切换">
  <button type="button" id="prev" aria-label="上一个风格">←</button>
  <div class="proto-bar-label"><strong>PROTOTYPE</strong><span id="variantLabel">A (Poster)</span></div>
  <button type="button" id="next" aria-label="下一个风格">→</button>
</div>
<script>{JS}</script>
</body>
</html>
"""
    path = OUT / 'disaster-cards.html'
    path.write_text(page, encoding='utf-8')
    print(f'WROTE {path} ({len(page)} chars)')
    return page


if __name__ == '__main__':
    build()
