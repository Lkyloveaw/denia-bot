# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。
"""Epic 免费游戏的纯逻辑：免费判定、即将免费、去重键、文本。

设计要点（来自研判文档）：

- 同一国家连续请求，offer 数组顺序会翻转，所以必须遍历所有组与所有 offer，
  禁止取 `[0]`。
- 免费 = 当前活跃的 0% offer；`discountPrice == 0` 只用于交叉校验，不能单独
  作判据（永久免费条目也是 0，但没有活跃窗口）。
- 去重键必须带 0% offer 的 `startDate`，否则同一游戏隔周再免费会漏推。

本模块不 import nonebot / httpx，可在秒级单测。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
STORE_BASE = 'https://store.epicgames.com/zh-CN'
DEFAULT_DESCRIPTION_LIMIT = 80


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(value):
    """把任意 datetime 归一到带时区的 UTC；None 用当前时间。"""
    if value is None:
        return utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_datetime(value):
    """Epic 的 ISO 时间（可能带 6 位小数、``Z``）→ 带时区 UTC；坏值返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first(mapping, key, default=None):
    if not isinstance(mapping, dict):
        return default
    value = mapping.get(key)
    return default if value is None or value == '' else value


def _is_zero_percent(offer):
    setting = offer.get('discountSetting') if isinstance(offer, dict) else None
    try:
        return int((setting or {}).get('discountPercentage')) == 0
    except (TypeError, ValueError):
        return False


def _window(offer):
    start = parse_datetime(_first(offer, 'startDate'))
    end = parse_datetime(_first(offer, 'endDate'))
    if start is None or end is None or end < start:
        return None
    return start, end


def _page_slug(element):
    for mapping in element.get('offerMappings') or ():
        slug = _first(mapping, 'pageSlug')
        if slug:
            return str(slug)
    catalog = element.get('catalogNs') or {}
    for mapping in catalog.get('mappings') or ():
        slug = _first(mapping, 'pageSlug')
        if slug:
            return str(slug)
    return ''


def _store_url(element):
    slug = _page_slug(element)
    return f'{STORE_BASE}/p/{slug}' if slug else f'{STORE_BASE}/'


def _offers(element, field, now):
    """遍历 field 下每一组、每一条 0% offer，返回满足条件的 (start, end)。"""
    promotions = element.get('promotions')
    if not isinstance(promotions, dict):
        return []
    found = []
    for group in promotions.get(field) or ():
        if not isinstance(group, dict):
            continue
        for offer in group.get('promotionalOffers') or ():
            if not isinstance(offer, dict) or not _is_zero_percent(offer):
                continue
            window = _window(offer)
            if window is None:
                continue
            start, end = window
            if field == 'promotionalOffers' and start <= now <= end:
                found.append((start, end))
            elif field == 'upcomingPromotionalOffers' and start > now:
                found.append((start, end))
    return found


def normalize(element, start, end):
    """把一条 Epic element 归一成卡片/文本共用的字典。"""
    price = ((element.get('price') or {}).get('totalPrice') or {})
    fmt = price.get('fmtPrice') or {}
    original = _first(fmt, 'originalPrice')
    if original is None:
        original = _first(price, 'originalPrice')
    return {
        'id': str(_first(element, 'id', '')),
        'title': str(_first(element, 'title', '未知游戏')),
        'description': str(_first(element, 'description', '')),
        'original_price': '' if original is None else str(original),
        'currency': str(_first(price, 'currencyCode', '')),
        'start': start,
        'end': end,
        'start_iso': start.astimezone(timezone.utc).isoformat(),
        'end_iso': end.astimezone(timezone.utc).isoformat(),
        'url': _store_url(element),
    }


def select_free(elements, now=None):
    """当前免费：遍历全部 offer 组与条目，取活跃 0% 窗口。"""
    now = as_utc(now)
    games = {}
    for element in elements or ():
        if not isinstance(element, dict):
            continue
        offers = _offers(element, 'promotionalOffers', now)
        if not offers:
            continue
        start, end = min(offers, key=lambda item: item[0])
        game = normalize(element, start, end)
        if not game['id']:
            continue
        current = games.get(game['id'])
        if current is None or game['start'] < current['start']:
            games[game['id']] = game
    return sorted(games.values(), key=lambda game: (game['start'], game['title']))


def select_upcoming(elements, now=None, limit=3):
    """即将免费：未来 0% 窗口；当前已免费的不重复列。"""
    now = as_utc(now)
    games = {}
    for element in elements or ():
        if not isinstance(element, dict):
            continue
        if _offers(element, 'promotionalOffers', now):
            continue
        offers = _offers(element, 'upcomingPromotionalOffers', now)
        if not offers:
            continue
        start, end = min(offers, key=lambda item: item[0])
        game = normalize(element, start, end)
        if not game['id']:
            continue
        current = games.get(game['id'])
        if current is None or game['start'] < current['start']:
            games[game['id']] = game
    result = sorted(games.values(), key=lambda game: (game['start'], game['title']))
    if limit is None:
        return result
    return result[:max(0, int(limit))]


def game_key(game):
    """单款游戏的稳定键：id + 0% offer 的 startDate。"""
    start = game.get('start_iso')
    if not start:
        start = game['start'].astimezone(timezone.utc).isoformat()
    return f"{game.get('id', '')}|{start}"


def game_keys(games):
    return {game_key(game) for game in games or () if game.get('id')}


def dedupe_key(games):
    """当前免费集合的稳定摘要：每款 ``id|startDate`` 排序后 sha256；空集合返回 ''。"""
    parts = sorted(game_keys(games))
    if not parts:
        return ''
    return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()


def should_push_key(last_key, current_key):
    """键级比较：首装（None/空）只记录不发送；空集合不推送。"""
    return bool(current_key) and bool(last_key) and current_key != last_key


def should_push(last_games, current_games):
    """集合级比较：首装、空集合、纯下架都不推；新增或换期才推。

    ``last_games`` 是上一次记录的完整游戏集合（可以是带 ``start_iso`` 的旧字典），
    ``current_games`` 是本次 ``select_free`` 的结果。纯下架不更新状态，这样同一
    期里被临时下架又回来的游戏不会重复推。
    """
    current = game_keys(current_games)
    if not current:
        return False
    last = game_keys(last_games)
    if not last:
        return False
    if current == last:
        return False
    if current < last:  # 纯下架：没有新增，不推
        return False
    return True


def format_beijing(value):
    return value.astimezone(BEIJING).strftime('%Y-%m-%d %H:%M')


def truncate(text, limit=DEFAULT_DESCRIPTION_LIMIT):
    text = ' '.join(str(text or '').split())
    if limit is None or len(text) <= limit:
        return text
    return text[:max(0, limit - 1)].rstrip() + '…'


def format_text(free, upcoming, description_limit=DEFAULT_DESCRIPTION_LIMIT):
    """卡片渲染失败时的文本兜底：与卡片同字段，一款一段。"""
    lines = ['【EPIC 喜加一】', 'EPIC: https://store.epicgames.com/zh-CN/']
    if not free:
        lines.append('本周暂无免费游戏。')
    for game in free or ():
        lines.append('')
        lines.append(f"【{game['title']}】")
        description = truncate(game.get('description'), description_limit)
        if description:
            lines.append(f'简介：{description}')
        original = str(game.get('original_price') or '').strip()
        if original:
            lines.append(f'原价：{original} → 免费')
        else:
            lines.append('价格：免费')
        lines.append(f"截止：{format_beijing(game['end'])}（北京时间）")
        lines.append(f"领取：{game['url']}")
    if upcoming:
        lines.append('')
        lines.append('【即将免费】')
        for game in upcoming:
            lines.append(f"{game['title']} · 开始：{format_beijing(game['start'])}（北京时间）")
            lines.append(f"领取：{game['url']}")
    return '\n'.join(lines)


def format_links(free, upcoming, include_upcoming=True):
    """把所有领取链接收进同一条消息；无链接返回空串。

    当前免费一款一行；`include_upcoming` 打开时把「即将免费」附在后面。
    这里只负责文本，发送端保证整段只发一条消息。
    """
    lines = []
    if free:
        lines.append('【EPIC 领取链接】')
        for game in free:
            line = f"{game.get('title', '')} {game.get('url', '')}".strip()
            if line:
                lines.append(line)
    if include_upcoming and upcoming:
        if not lines:
            lines.append('【EPIC 领取链接】')
        lines.append('【即将免费】')
        for game in upcoming:
            line = f"{game.get('title', '')} {game.get('url', '')}".strip()
            if line:
                lines.append(line)
    return '\n'.join(lines)


def build_view(elements, now=None, upcoming_limit=3):
    """接线层用的一站式视图：当前免费、即将免费、去重键。"""
    now = as_utc(now)
    free = select_free(elements, now)
    upcoming = select_upcoming(elements, now, upcoming_limit)
    return {'free': free, 'upcoming': upcoming, 'key': dedupe_key(free)}
