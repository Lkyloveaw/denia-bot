# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""远程 Playwright 渲染客户端 + B Bulletin 灾害卡片 HTML。

用户已选 B Bulletin；卡片为 900×1200 浅色公告：左色轨、官方抬头、印章、
双栏字段、内联 SVG 示意、正文与来源。首报中性、后续达妮娅由调用方传入 tone。

卡片自包含（内联 CSS/SVG，无外部图片/字体链接）；渲染端默认禁 JS、拦外网。
"""
from __future__ import annotations

import html
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

import disaster_china_logic as china
import disaster_weather_card as weather_card
import disaster_weather_map as weather_map_mod
from disaster_logic import EarthquakeEvent, SOURCE_LABELS, event_lead, local_distance


class RenderError(RuntimeError):
    """渲染服务不可用、返回非图片或 HTTP 错误。"""


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ''), quote=True)


def _time(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return '—'
    text = text.replace('/', '-')
    return text if '北京时间' in text else text + '（北京时间）'


def _fact(label: str, value: Any) -> str:
    return (
        '<div class="fact"><span class="fact-label">'
        f'{_e(label)}</span><b class="fact-value">{_e(value or "—")}</b></div>'
    )


def _facts(rows: list[tuple[str, Any]]) -> str:
    return ''.join(_fact(label, value) for label, value in rows)


def _weather_accent(color: str) -> str:
    return {
        '红色': '#b83b2f', '橙色': '#c2711f', '黄色': '#b68a1e',
        '蓝色': '#2f6f9f', '白色': '#7b7b7b',
    }.get(color, '#b68a1e')


def _typhoon_accent(level: str) -> str:
    if '超强' in level:
        return '#b83b2f'
    if '强' in level:
        return '#c2711f'
    if '热带风暴' in level:
        return '#2f6f9f'
    return '#b68a1e'


def _earthquake_accent(event: EarthquakeEvent) -> str:
    magnitude = event.magnitude or 0
    if magnitude >= 6:
        return '#b83b2f'
    if magnitude >= 5:
        return '#c2711f'
    return '#3d7d5f'


def _weather_region(alert: china.WeatherAlert) -> str:
    return ' · '.join(part for part in (alert.province, alert.city, alert.district) if part)


def _typhoon_visual(typhoon: china.Typhoon) -> str:
    return """
    <svg class="viz" viewBox="0 0 520 300" role="img" aria-label="台风路径与风圈示意">
      <rect class="viz-bg" x="0" y="0" width="520" height="300" rx="10"/>
      <g class="viz-grid">
        <path d="M34 50H486M34 125H486M34 200H486M34 275H486"/>
        <path d="M110 34V284M220 34V284M330 34V284M440 34V284"/>
      </g>
      <circle class="viz-wind" cx="340" cy="150" r="66"/>
      <circle class="viz-wind" cx="340" cy="150" r="42"/>
      <circle class="viz-wind" cx="340" cy="150" r="22"/>
      <polyline class="viz-track" points="64,252 142,228 214,198 278,170 340,150 410,110 468,74"/>
      <circle class="viz-now" cx="340" cy="150" r="7"/>
      <circle class="viz-future" cx="468" cy="74" r="6"/>
      <text class="viz-label" x="352" y="142">当前</text>
      <text class="viz-label" x="478" y="68">预计</text>
      <text class="viz-small" x="34" y="288">路径/风圈为示意，生产接台风图件</text>
    </svg>
    """


def _tsunami_visual(alert: china.TsunamiAlert) -> str:
    return """
    <svg class="viz" viewBox="0 0 520 300" role="img" aria-label="海啸影响沿海示意">
      <rect class="viz-bg" x="0" y="0" width="520" height="300" rx="10"/>
      <path class="viz-coast" d="M34 258 C110 226 158 240 214 210 S326 176 486 202 L486 288 L34 288 Z"/>
      <path class="viz-wave" d="M34 170 C92 154 120 180 174 164 S258 140 316 158 S414 176 486 154"/>
      <path class="viz-wave" d="M34 190 C92 174 120 200 174 184 S258 160 316 178 S414 196 486 174"/>
      <circle class="viz-impact" cx="144" cy="238" r="5"/>
      <circle class="viz-impact" cx="238" cy="218" r="5"/>
      <circle class="viz-impact" cx="326" cy="198" r="5"/>
      <text class="viz-label" x="112" y="266">沿海影响区</text>
      <text class="viz-small" x="34" y="288">官方图件占位，生产接海啸中心图件</text>
    </svg>
    """


def _earthquake_visual(event: EarthquakeEvent) -> str:
    return """
    <svg class="viz" viewBox="0 0 520 300" role="img" aria-label="地震位置示意">
      <rect class="viz-bg" x="0" y="0" width="520" height="300" rx="10"/>
      <path class="viz-map" d="M88 76 L150 54 L212 68 L260 54 L320 76 L378 116 L400 176 L374 228 L322 262 L260 254 L216 278 L160 258 L112 218 L82 164 Z"/>
      <circle class="viz-ring" cx="286" cy="164" r="40"/>
      <circle class="viz-ring" cx="286" cy="164" r="24"/>
      <circle class="viz-now" cx="286" cy="164" r="7"/>
      <text class="viz-label" x="300" y="158">震中</text>
      <text class="viz-small" x="34" y="288">地图示意（非地理精确）· 生产接地图瓦片/列表</text>
    </svg>
    """


BULLETIN_STYLE = """
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{background:#f3efe4;color:#1b1b18;font-family:"Noto Sans CJK SC","Noto Sans SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
.card{width:900px;height:1200px;display:flex;background:#f3efe4;color:#1b1b18;font-family:"Noto Serif CJK SC","Songti SC","Microsoft YaHei",serif;overflow:hidden}
.rail{width:16px;flex:0 0 16px;background:var(--accent)}
.sheet{flex:1;min-width:0;padding:42px 52px 34px;display:flex;flex-direction:column;position:relative}
.mast{display:flex;justify-content:space-between;gap:12px;font-size:11px;letter-spacing:.1em;color:#6b655b;border-bottom:2px solid #1b1b18;padding-bottom:10px}
.title{font-size:40px;line-height:1.28;font-weight:750;margin:18px 0 14px;max-width:620px;letter-spacing:-.01em}
.stamp{position:absolute;top:132px;right:52px;border:4px double var(--accent);color:var(--accent);padding:10px 16px;border-radius:8px;font-size:22px;font-weight:800;letter-spacing:.08em;transform:rotate(-7deg);background:rgba(255,255,255,.28);max-width:220px;text-align:center}
.meta{display:grid;grid-template-columns:1fr 1fr;gap:0 24px;border-top:1px solid #d9d2c3;border-bottom:1px solid #d9d2c3;margin:4px 0 14px}
.fact{display:flex;flex-direction:column;gap:4px;min-width:0;border-bottom:1px solid #e2dbcd;padding:7px 0}
.fact:nth-last-child(-n+2){border-bottom:0}
.fact-label{font-size:10px;letter-spacing:.12em;color:#6b655b;text-transform:uppercase}
.fact-value{font-size:14px;line-height:1.4;font-weight:650;overflow-wrap:anywhere}
.figure{flex:1 1 0;min-height:260px;max-height:700px;margin:4px 0 12px;border:1px solid #d9d2c3;border-radius:10px;overflow:hidden;background:#fffdf7}
.voice{display:inline-block;align-self:flex-start;background:var(--accent);color:#fffdf7;font-size:10px;letter-spacing:.08em;padding:4px 8px;border-radius:4px;margin-bottom:6px}
.lead{font-size:14px;line-height:1.85;margin:0;color:#1b1b18}
.foot{display:flex;justify-content:space-between;gap:14px;border-top:1px solid #d9d2c3;margin-top:auto;padding-top:10px;font-size:10px;color:#6b655b}
.viz{width:100%;height:100%;display:block}
.viz-bg{fill:#fffdf7}.viz-grid{stroke:#e4ddcf;stroke-width:1;fill:none}
.viz-track{fill:none;stroke:var(--accent);stroke-width:3;stroke-linecap:round;stroke-linejoin:round}
.viz-wind{fill:none;stroke:var(--accent);stroke-width:1.5;stroke-dasharray:5 6;opacity:.65}
.viz-now{fill:var(--accent);stroke:#fffdf7;stroke-width:3}.viz-future{fill:#fffdf7;stroke:var(--accent);stroke-width:2}
.viz-label{fill:#1b1b18;font-size:11px;font-family:inherit}.viz-small{fill:#6b655b;font-size:10px;font-family:inherit}
.viz-coast{fill:#fffdf7;stroke:#d9d2c3;stroke-width:1.5}.viz-wave{fill:none;stroke:var(--accent);stroke-width:2;stroke-dasharray:7 6;opacity:.7}
.viz-impact{fill:var(--accent);stroke:#fffdf7;stroke-width:2}.viz-map{fill:#fffdf7;stroke:#d9d2c3;stroke-width:1.5}
.viz-ring{fill:none;stroke:var(--accent);stroke-width:1.5;opacity:.55}
.weather-viz{height:100%;position:relative;background:linear-gradient(135deg,rgba(255,255,255,.55),transparent 65%),#fffdf7;overflow:hidden}
.w-word{position:absolute;left:28px;top:24px;font-size:92px;line-height:.9;font-weight:800;letter-spacing:-.05em;color:var(--accent);font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif}
.w-sub{position:absolute;left:32px;bottom:58px;font-size:22px;font-weight:700;color:#1b1b18}
.w-bars{position:absolute;left:32px;right:32px;bottom:22px;height:28px;display:flex;align-items:flex-end;gap:7px}
.w-bars i{flex:1;background:var(--accent);opacity:.42;border-radius:3px 3px 0 0}
.w-bars i:nth-child(1){height:30%}.w-bars i:nth-child(2){height:55%}.w-bars i:nth-child(3){height:38%}.w-bars i:nth-child(4){height:74%}.w-bars i:nth-child(5){height:48%}.w-bars i:nth-child(6){height:88%}.w-bars i:nth-child(7){height:60%}
""".strip()


def _source(kind: str, item: Any) -> str:
    if kind == 'weather':
        return '中国气象局 / Jian Project'
    if kind == 'tsunami':
        return '自然资源部海啸预警中心 / Jian Project'
    if kind == 'typhoon':
        return '中国气象局 / EQSC'
    return f'{SOURCE_LABELS.get(item.source, item.source)} / Wolfx 公开接口'


def _kicker(kind: str, item: Any) -> str:
    if kind == 'weather':
        return '中国气象局 · 气象预警'
    if kind == 'tsunami':
        return '自然资源部海啸预警中心'
    if kind == 'typhoon':
        return '中国气象局 · 台风路径'
    return '中国地震台网 · ' + ('地震预警' if str(item.source).endswith('_eew') else '地震情报')


def _stamp(kind: str, item: Any, stopped: bool = False) -> str:
    if stopped:
        return '停编'
    if kind == 'weather':
        return f'{item.color}预警' if item.color != '未知' else '气象预警'
    if kind == 'tsunami':
        return item.level or '海啸'
    if kind == 'typhoon':
        return item.level or '台风'
    if str(item.source).endswith('_eew'):
        text = f'第 {item.report_num} 报'
        return text + (' · 最终报' if item.is_final else '')
    return '正式测定' if item.source == 'cenc' else '地震情报'


def _title(kind: str, item: Any, stopped: bool = False) -> str:
    if stopped:
        return '台风停编'
    if kind == 'weather':
        return item.title or item.headline or '气象预警'
    if kind == 'tsunami':
        return item.title or '海啸预警'
    if kind == 'typhoon':
        return f'{item.typhoon_id} {item.display_name}'
    magnitude = f'M{item.magnitude:.1f}' if item.magnitude is not None else '震级调查中'
    return f'{item.place or "震中调查中"} {magnitude}'


def _meta(kind: str, item: Any, config: Mapping[str, Any]) -> list[tuple[str, Any]]:
    if kind == 'weather':
        return [
            ('等级', f'{item.color}预警' if item.color != '未知' else '未知'),
            ('类型', china.weather_type_name(item)),
            ('地区', _weather_region(item) or '—'),
            ('生效', _time(item.effective_at) if item.effective_at else '—'),
        ]
    if kind == 'tsunami':
        return [
            ('等级', item.level or '—'),
            ('报次', f'第 {item.number} 报'),
            ('震中', item.place or '—'),
            ('发震', _time(item.origin_time) if item.origin_time else '—'),
            ('震级', f'M{item.magnitude:.1f}' if item.magnitude is not None else '—'),
            ('深度', f'{item.depth:g} km' if item.depth is not None else '—'),
            ('发布', item.org_unit or '自然资源部海啸预警中心'),
            ('事件', item.alert_id or '—'),
        ]
    if kind == 'typhoon':
        return [
            ('编号', item.typhoon_id or '—'),
            ('等级', item.level or '—'),
            ('中心', f'{item.latitude:.1f}° / {item.longitude:.1f}°' if item.latitude is not None and item.longitude is not None else '—'),
            ('中心气压', f'{item.pressure:g} hPa' if item.pressure is not None else '—'),
            ('最大风速', f'{item.wind_speed:g} m/s' if item.wind_speed is not None else '—'),
            ('移动', ' '.join(part for part in (item.move_direction, str(item.move_speed or '')) if part) or '—'),
            ('更新', _time(item.updated_at) if item.updated_at else '—'),
            ('预计', _time(item.future_time) if item.future_time else '—'),
        ]
    intensity_label = '最大震度' if str(item.source).startswith('jma') else '最大烈度'
    distance = local_distance(item, config)
    return [
        ('来源', SOURCE_LABELS.get(item.source, item.source)),
        ('报次', f'第 {item.report_num} 报' + (' · 最终报' if item.is_final else '') if str(item.source).endswith('_eew') else '正式测定'),
        ('震级', f'M{item.magnitude:.1f}' if item.magnitude is not None else '—'),
        ('位置', item.place or '—'),
        ('发震', _time(item.occurred_at) if item.occurred_at else '—'),
        ('深度', f'{item.depth:g} km' if item.depth is not None else '—'),
        (intensity_label, item.intensity or '—'),
        ('距本地', f'约 {distance:.0f} km' if distance is not None else '—'),
    ]


def _visual(kind: str, item: Any) -> str:
    if kind == 'typhoon':
        return _typhoon_visual(item)
    if kind == 'tsunami':
        return _tsunami_visual(item)
    return _earthquake_visual(item)


def _lead(kind: str, item: Any, tone: str, stopped: bool = False) -> str:
    if stopped:
        if tone == 'character':
            return f'达妮娅看了下：{item.typhoon_id} {item.display_name} 已停编。'
        return f'{item.typhoon_id} {item.display_name} 已停编。'
    if kind == 'weather':
        return china.weather_lead(item, tone)
    if kind == 'tsunami':
        return china.tsunami_lead(item, tone)
    if kind == 'typhoon':
        return china.typhoon_lead(item, tone)
    return event_lead(item, tone)


def _accent(kind: str, item: Any) -> str:
    if kind == 'weather':
        return _weather_accent(item.color)
    if kind == 'typhoon':
        return _typhoon_accent(item.level)
    if kind == 'tsunami':
        return _weather_accent(item.level)
    return _earthquake_accent(item)


def bulletin_html(kind: str, item: Any, config: Mapping[str, Any] | None = None,
                  tone: str = 'neutral', stopped: bool = False,
                  *, weather_map: weather_map_mod.WeatherMap | None = None) -> str:
    """四类 B Bulletin 卡片；所有外部文本转义。"""
    config = config or {}
    if kind not in ('weather', 'tsunami', 'typhoon', 'earthquake'):
        raise ValueError(f'未知灾害类型: {kind}')
    tone = tone if tone in ('neutral', 'character') else 'neutral'
    if kind == 'weather':
        return weather_card.weather_html(item, tone, weather_map)
    accent = _accent(kind, item)
    voice = '<div class="voice">达妮娅 · 后续解读</div>' if tone == 'character' else ''
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>{BULLETIN_STYLE}</style></head>
<body><div class="card" style="--accent:{accent}">
  <div class="rail"></div>
  <main class="sheet">
    <div class="mast"><span>{_e(_kicker(kind, item))}</span><span>公开数据</span></div>
    <h1 class="title">{_e(_title(kind, item, stopped))}</h1>
    <div class="stamp">{_e(_stamp(kind, item, stopped))}</div>
    <section class="meta">{_facts(_meta(kind, item, config))}</section>
    <section class="figure">{_visual(kind, item)}</section>
    {voice}
    <p class="lead">{_e(_lead(kind, item, tone, stopped))}</p>
    <footer class="foot"><span>{_e(_source(kind, item))}</span><span>仅供参考，以官方发布为准</span></footer>
  </main>
</div></body></html>"""


def card_html(event: EarthquakeEvent, config: Mapping[str, Any] | None = None,
              tone: str = 'neutral') -> str:
    return bulletin_html('earthquake', event, config, tone)


def _base_url(url: str) -> str:
    text = str(url or '').strip().rstrip('/')
    parsed = urlsplit(text)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise RenderError('渲染服务地址须以 http(s):// 开头')
    return text


async def render_png(base_url: str, html_text: str, *, width: int = 900,
                     height: int = 1200, timeout: float = 20.0,
                     token: str = '', client: httpx.AsyncClient | None = None) -> bytes:
    import render_client as shared_render
    try:
        return await shared_render.render_html(html_text, url=_base_url(base_url),
            width=width, height=height, timeout_ms=int(timeout*1000),
            source='disaster', token=token, client=client)
    except shared_render.RenderError as exc:
        raise RenderError(str(exc)) from exc


async def health(base_url: str, *, timeout: float = 3.0,
                 client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    import render_client as shared_render
    url = shared_render.base_url() + '/health'
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout, trust_env=False)
    try:
        response = await client.get(url)
        if response.status_code != 200:
            raise RenderError(f'渲染服务返回 HTTP {response.status_code}')
        data = response.json()
        if not isinstance(data, dict):
            raise RenderError('渲染服务返回格式无效')
        return data
    except (httpx.HTTPError, ValueError) as exc:
        raise RenderError(f'渲染服务不可达：{exc}') from exc
    finally:
        if own:
            await client.aclose()


async def render_bulletin(kind: str, item: Any, config: Mapping[str, Any],
                          tone: str = 'neutral', stopped: bool = False,
                          client: httpx.AsyncClient | None = None) -> bytes | None:
    """按面板配置渲染 B 卡；未启用/未配置地址返回 None，失败抛 RenderError。"""
    render = config.get('render') or {}
    if not render.get('enabled'):
        return None
    import render_client as shared_render
    remote_url = shared_render.base_url()
    timeout = float(render.get('timeout_seconds') or 20)
    weather_map = await weather_map_mod.map_for_alert(item, client=client) if kind == 'weather' else None
    return await render_png(
        remote_url,
        bulletin_html(kind, item, config, tone, stopped, weather_map=weather_map),
        height=weather_card.card_height(item, tone) if kind == 'weather' else 1200,
        timeout=timeout,
        client=client,
    )


async def render_event(event: EarthquakeEvent, config: Mapping[str, Any],
                       tone: str = 'neutral',
                       client: httpx.AsyncClient | None = None) -> bytes | None:
    return await render_bulletin('earthquake', event, config, tone, client=client)
