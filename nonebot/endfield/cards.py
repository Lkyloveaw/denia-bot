"""自包含 HTML 卡片（MIT）。

只生成 HTML 字符串：无外链、无图片、无 JS，字体走渲染器已装的 Noto/WQY。
上游 16 套模板是 AGPL 且依赖远程头像/大字体；这里先用通用卡片保证 2a 能出图。
"""

from __future__ import annotations

from html import escape
from typing import Sequence

MAX_LINES = 40
CARD_WIDTH = 900
CARD_HEIGHT = 1200

_STYLE = """
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #0f131b; }
body {
  color: #e8edf7;
  font-family: "Noto Sans CJK SC", "Noto Sans SC", "WenQuanYi Zen Hei",
    "Microsoft YaHei", sans-serif;
}
.card {
  width: 900px;
  min-height: 1200px;
  padding: 48px 52px 40px;
  background:
    radial-gradient(circle at 12% 0%, rgba(91, 141, 239, 0.22), transparent 42%),
    radial-gradient(circle at 100% 100%, rgba(79, 209, 197, 0.14), transparent 45%),
    #0f131b;
}
.kicker {
  color: #7dd3fc;
  font-size: 18px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}
h1 {
  margin: 14px 0 6px;
  font-size: 40px;
  line-height: 1.2;
  font-weight: 700;
}
.subtitle {
  margin: 0 0 30px;
  color: #9aa8bd;
  font-size: 20px;
}
ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
li {
  padding: 13px 0;
  border-bottom: 1px solid rgba(148, 163, 184, 0.18);
  font-size: 22px;
  line-height: 1.55;
  word-break: break-word;
}
li:last-child { border-bottom: none; }
.empty {
  margin-top: 36px;
  color: #9aa8bd;
  font-size: 22px;
}
.footer {
  margin-top: 34px;
  color: #74839a;
  font-size: 16px;
  line-height: 1.6;
}
"""


def build_card(
    title: str,
    lines: Sequence[str],
    *,
    subtitle: str = "",
    footer: str = "",
) -> str:
    """生成自包含卡片 HTML；所有用户内容都转义。"""
    safe_title = escape(str(title or "终末地"))
    safe_subtitle = escape(str(subtitle or ""))
    safe_footer = escape(str(footer or ""))
    items = [str(line) for line in lines if str(line).strip()]
    truncated = len(items) > MAX_LINES
    visible = items[:MAX_LINES]
    body = "".join(f"<li>{escape(line)}</li>" for line in visible)
    if not body:
        body = '<div class="empty">暂无数据。</div>'
    more = (
        f'<div class="footer">已截断，仅显示前 {MAX_LINES} 行。</div>'
        if truncated
        else ""
    )
    footer_html = (
        f'<div class="footer">{safe_footer}</div>' if safe_footer else ""
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN"><head><meta charset="UTF-8">'
        f"<title>{safe_title}</title><style>{_STYLE}</style></head>"
        '<body><div class="card">'
        '<div class="kicker">ENDFIELD</div>'
        f"<h1>{safe_title}</h1>"
        f'<p class="subtitle">{safe_subtitle or "终末地 · 查询结果"}</p>'
        f"<ul>{body}</ul>{more}{footer_html}"
        "</div></body></html>"
    )
