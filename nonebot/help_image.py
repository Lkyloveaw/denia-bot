"""`.help` 的图片菜单渲染。

把指令表画成一张分组卡片，比一屏折行错位的纯文本好读得多。设计取向：

- 按功能分组（对话 / 知识库 / 限流 / 管理），每组一条标题带色块
- 每条指令一行：左边命令（等宽感靠字号拉开），右边说明，说明过长自动折行
- 顶部一个标题区，底部一行页脚写当前生效的配置 —— 列表要说真话

渲染失败一律返回 None，调用方回退纯文本，不能让 `.help` 变成没有回应。
"""

from __future__ import annotations

from pathlib import Path

from nonebot import logger

FONT_PATH = Path(__file__).resolve().parent / 'media_parser' / 'core' / 'resources' / 'HYSongYunLangHeiW-1.ttf'
"""复用解析器的字体资源，避免再打包一份中文字体。"""

# Claude 风格配色
BG = (250, 249, 245)
PAPER = (255, 254, 250)
INK = (41, 39, 33)
MUTED = (129, 126, 115)
LINE = (232, 229, 220)
ACCENT = (198, 110, 78)
ACCENT_SOFT = (247, 234, 226)

WIDTH = 760
PAD = 34
TITLE_SIZE = 34
SUB_SIZE = 16
GROUP_SIZE = 18
CMD_SIZE = 16
DESC_SIZE = 15
LINE_HEIGHT = 26


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except OSError:
        return ImageFont.load_default()


def _wrap_tokens(draw, text: str, font, max_width: int) -> list[str]:
    """命令折行：优先在空格处断开，不把一个参数名劈成两半。

    `限流 上限 <群号> <QQ号> <次数|不限>` 逐字折会变成 `限流 上限 <群号> <QQ号> <次` /
    `数|不限>`，很难读。这里按空白切词，超长单词才退化成逐字拆。
    """
    tokens = text.split(' ')
    lines: list[str] = []
    current = ''
    for token in tokens:
        trial = f'{current} {token}' if current else token
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
            continue
        if current:
            lines.append(current)
            current = ''
        # 单个词本身就超宽：逐字拆
        piece = ''
        for ch in token:
            if draw.textlength(piece + ch, font=font) <= max_width or not piece:
                piece += ch
            else:
                lines.append(piece)
                piece = ch
        current = piece
    if current:
        lines.append(current)
    return lines or ['']


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    """按像素宽度折行；中文没有空格，只能逐字量。"""
    if not text:
        return ['']
    lines: list[str] = []
    current = ''
    for ch in text:
        trial = current + ch
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def render_help_image(
    groups: list[tuple[str, list[tuple[str, str]]]],
    *,
    title: str,
    subtitle: str,
    footer: str,
) -> Path | None:
    """画成 PNG 并返回路径；任何异常都返回 None。

    `groups` 是 [(组名, [(命令, 说明), …]), …]，组名带 '·' 前缀的按普通组处理。
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    try:
        font_title = _font(TITLE_SIZE)
        font_sub = _font(SUB_SIZE)
        font_group = _font(GROUP_SIZE)
        font_cmd = _font(CMD_SIZE)
        font_desc = _font(DESC_SIZE)
        font_foot = _font(DESC_SIZE - 2)

        # ---- 先量一遍高度 ----
        cmd_col = 268                      # 命令列宽：容得下最长的一条限流指令
        desc_x = PAD + cmd_col + 18
        desc_w = WIDTH - desc_x - PAD

        probe = Image.new('RGB', (WIDTH, 10), BG)
        pdraw = ImageDraw.Draw(probe)

        head_h = 118
        group_gap = 34
        row_gap = 12
        body_h = 0
        layout: list[tuple] = []
        for name, rows in groups:
            body_h += group_gap + 30
            layout.append(('group', name, body_h))
            for cmd, desc in rows:
                lines = _wrap(pdraw, desc, font_desc, desc_w)
                cmd_lines = _wrap_tokens(pdraw, cmd, font_cmd, cmd_col - 12)
                desc_h = max(LINE_HEIGHT, len(lines) * (DESC_SIZE + 8))
                cmd_h = len(cmd_lines) * (CMD_SIZE + 7)
                h = max(desc_h, cmd_h)
                body_h += h + row_gap
                layout.append(('row', cmd, lines, h, body_h))

        foot_h = 56
        height = head_h + body_h + foot_h + PAD

        img = Image.new('RGB', (WIDTH, height), BG)
        draw = ImageDraw.Draw(img)

        # ---- 顶部标题区 ----
        draw.text((PAD, 30), title, font=font_title, fill=INK)
        draw.text((PAD, 76), subtitle, font=font_sub, fill=MUTED)
        draw.rectangle([PAD, head_h - 1, WIDTH - PAD, head_h], fill=LINE)

        # ---- 分组与条目 ----
        y = head_h
        for item in layout:
            if item[0] == 'group':
                _, name, _ = item
                y += group_gap
                draw.rectangle([PAD, y + 3, PAD + 4, y + 22], fill=ACCENT)
                draw.text((PAD + 14, y), name, font=font_group, fill=ACCENT)
                y += 30
            else:
                _, cmd, lines, h, _ = item
                # 命令过长时拆成两行，否则会压到说明那一列上
                cmd_lines = _wrap_tokens(pdraw, cmd, font_cmd, cmd_col - 12)
                for i, cl in enumerate(cmd_lines):
                    draw.text((PAD, y + i * (CMD_SIZE + 7)), cl, font=font_cmd, fill=INK)
                cmd_h = len(cmd_lines) * (CMD_SIZE + 7)
                row_h = max(h, cmd_h)
                # 说明：首行用正文色，续行用次要色
                for i, line in enumerate(lines):
                    draw.text((desc_x, y + i * (DESC_SIZE + 8)), line,
                              font=font_desc, fill=INK if i == 0 else MUTED)
                y += row_h
                draw.rectangle([PAD, y + 2, WIDTH - PAD, y + 3], fill=(243, 241, 235))
                y += row_gap

        # ---- 页脚 ----
        draw.rectangle([PAD, height - foot_h, WIDTH - PAD, height - foot_h + 1], fill=LINE)
        draw.text((PAD, height - foot_h + 20), footer, font=font_foot, fill=MUTED)

        out = Path('/tmp') / 'qqbot-help.png'
        try:
            img.save(out, 'PNG')
        except OSError:
            out = Path('help.png').resolve()
            img.save(out, 'PNG')
        return out
    except Exception:  # noqa: BLE001 - 渲染失败必须回退文本
        logger.exception('[help] 菜单图片渲染失败')
        return None
