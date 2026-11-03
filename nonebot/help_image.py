"""One complete help menu, in the existing protocol-visible PNG cache."""
from math import ceil, sqrt
from pathlib import Path
import unicodedata
import uuid

from card_document import document, text
from render_client import render_card
from gate_logic import display_help_command

COLUMN_WIDTH = 1000
COLUMN_GAP = 24


def _text_units(value):
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in str(value))


def _layout(groups):
    # Estimate text height to keep the complete menu roughly square. The browser
    # balances intact sections across columns; new commands can add more columns.
    height = sum(100 + sum(16 + 29 * max(1, ceil(_text_units(cmd) / 27),
                                        ceil(_text_units(desc) / 60))
                          for cmd, desc in rows) for _, rows in groups)
    columns = max(1, min(len(groups), ceil(sqrt(height / COLUMN_WIDTH))))
    return columns, columns * COLUMN_WIDTH + (columns - 1) * COLUMN_GAP + 68


def build_html(groups, *, title, subtitle, footer):
    groups = [(name, list(rows)) for name, rows in groups]
    columns, width = _layout(groups)
    sections = []
    for name, rows in groups:
        body = ''.join(f'<tr><td class="command text">{text(display_help_command(cmd))}</td>'
                       f'<td class="text">{text(desc)}</td></tr>' for cmd, desc in rows)
        sections.append(f'<section class="panel"><h2 class="accent">{text(name)}</h2>'
                        f'<table>{body}</table></section>')
    blocks = [f'<header><h1>{text(title)}</h1><p class="muted">{text(subtitle)}</p></header>',
              '<div class="help-columns">' + ''.join(sections) + '</div>',
              f'<footer class="text">{text(footer)}</footer>']
    css = (f'.help-columns{{column-count:{columns};column-gap:{COLUMN_GAP}px}}'
           '.help-columns .panel{break-inside:avoid;margin:0 0 18px}'
           '.command{width:290px;font-weight:600}td{vertical-align:top;font-size:20px}')
    return document(blocks, width=width, css=css)


def _output_path():
    from media_parser.core.config import PluginConfig
    cache = PluginConfig().cache_dir
    cache.mkdir(parents=True, exist_ok=True)
    return cache / f'qqbot-help-{uuid.uuid4().hex}.png'


def render_help_image(groups, *, title, subtitle, footer) -> Path:
    groups = [(name, list(rows)) for name, rows in groups]
    _, width = _layout(groups)
    png = render_card(build_html(groups, title=title, subtitle=subtitle, footer=footer),
                      width=width, source='help')
    path = _output_path()
    path.write_bytes(png)
    return path


def render_help_images(groups, *, title, subtitle, footer) -> list[Path]:
    # Keep existing callers compatible; the menu is always delivered as one image.
    return [render_help_image(groups, title=title, subtitle=subtitle, footer=footer)]
