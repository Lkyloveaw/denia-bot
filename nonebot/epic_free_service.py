# 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
# 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
# 详见 docs/third-party/epicfree-NOTICE.md。
"""Epic 免费游戏接口与去重状态。

网络与状态 I/O 都在这里；判定/格式化在 ``epic_free_logic.py``（纯逻辑）。
本模块不 import nonebot，方便接线层之外单测。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

import epic_free_logic as logic

API_URL = 'https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions'
API_PATH = '/freeGamesPromotions'
API_HOSTS = (
    'store-site-backend-static-ipv4.ak.epicgames.com',
    'store-site-backend-static.ak.epicgames.com',
)
NETWORK_ATTEMPTS = 2
RETRY_DELAY = 0.5
DEFAULT_STATE_PATH = Path(__file__).resolve().parent / 'data' / 'epic_free_state.json'
USER_AGENT = 'denia-bot/1.0 (+https://github.com/Lkyloveaw/denia-bot)'


class EpicError(Exception):
    """Epic 数据层可识别错误；接线层捕获后跳过本轮。"""


class EpicHTTPError(EpicError):
    pass


class EpicAPIError(EpicError):
    pass


class EpicEmptyError(EpicError):
    pass


def parse_payload(payload):
    """从响应 JSON 里取 elements；把 200 错误体、空数据都变成可识别异常。"""
    if not isinstance(payload, dict):
        raise EpicAPIError('响应不是 JSON 对象')
    errors = payload.get('errors')
    if isinstance(errors, list) and errors:
        messages = []
        for item in errors:
            if isinstance(item, dict):
                messages.append(str(item.get('serviceResponse') or item.get('message') or item))
            else:
                messages.append(str(item))
        raise EpicAPIError('; '.join(messages) or '接口返回错误')
    data = payload.get('data')
    if not isinstance(data, dict):
        raise EpicAPIError('data 缺失')
    catalog = data.get('Catalog')
    if not isinstance(catalog, dict):
        raise EpicAPIError('Catalog 缺失')
    store = catalog.get('searchStore')
    if not isinstance(store, dict):
        raise EpicAPIError('searchStore 缺失')
    elements = store.get('elements')
    if not elements:
        raise EpicEmptyError('接口没有返回任何游戏')
    if not isinstance(elements, list):
        raise EpicAPIError('elements 格式异常')
    return elements


async def fetch(country='CN', locale='zh-CN', client=None, timeout=15):
    """抓取原始 elements；网络错误会换域名重试，错误分类见 EpicError 子类。"""
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    last_error = None
    try:
        for host in API_HOSTS:
            for attempt in range(NETWORK_ATTEMPTS):
                try:
                    response = await client.get(
                        f'https://{host}{API_PATH}',
                        params={'locale': locale, 'country': country, 'allowCountries': country},
                        headers={'User-Agent': USER_AGENT},
                    )
                except httpx.HTTPError as error:
                    last_error = error
                    if attempt + 1 < NETWORK_ATTEMPTS:
                        await asyncio.sleep(RETRY_DELAY)
                    continue
                if response.status_code != 200:
                    if response.status_code >= 500:
                        last_error = EpicHTTPError(f'HTTP {response.status_code}')
                        if attempt + 1 < NETWORK_ATTEMPTS:
                            await asyncio.sleep(RETRY_DELAY)
                        continue
                    raise EpicHTTPError(f'HTTP {response.status_code}')
                try:
                    payload = response.json()
                except ValueError as error:
                    raise EpicAPIError('响应不是合法 JSON') from error
                return parse_payload(payload)
        if isinstance(last_error, EpicError):
            raise last_error
        raise EpicError(
            f'网络请求失败（{type(last_error).__name__}: {last_error!r}）'
        ) from last_error
    finally:
        if own:
            await client.aclose()


async def fetch_view(country='CN', locale='zh-CN', client=None, now=None, upcoming_limit=3):
    elements = await fetch(country=country, locale=locale, client=client)
    return logic.build_view(elements, now=now, upcoming_limit=upcoming_limit)


class StateStore:
    """``data/epic_free_state.json``：每个推送目标各自保存 last_key。

    首装/重启不补发由调用方用 ``logic.should_push`` 决定；这里只负责读写。
    """

    def __init__(self, path=None):
        self.path = Path(path) if path else DEFAULT_STATE_PATH

    def load(self):
        if not self.path.exists():
            return {'version': 1, 'targets': {}}
        try:
            data = json.loads(self.path.read_text('utf-8'))
        except (OSError, ValueError):
            return {'version': 1, 'targets': {}}
        if not isinstance(data, dict):
            return {'version': 1, 'targets': {}}
        if not isinstance(data.get('targets'), dict):
            data['targets'] = {}
        data.setdefault('version', 1)
        return data

    def save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), 'utf-8')
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)

    def last_key(self, state, target):
        entry = (state.get('targets') or {}).get(str(target))
        return entry.get('last_key') if isinstance(entry, dict) else None

    def last_games(self, state, target):
        entry = (state.get('targets') or {}).get(str(target))
        games = entry.get('last_games') if isinstance(entry, dict) else None
        return games if isinstance(games, list) else []

    def mark(self, state, target, key, games=None, when=None):
        if not isinstance(state.get('targets'), dict):
            state['targets'] = {}
        moment = when or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        record = {
            'last_key': str(key),
            'last_sent_at': moment.astimezone(timezone.utc).isoformat(),
        }
        if games is not None:
            record['last_games'] = [
                {
                    'id': str(game.get('id', '')),
                    'start_iso': str(
                        game.get('start_iso')
                        or game['start'].astimezone(timezone.utc).isoformat()
                    ),
                }
                for game in games
                if game.get('id')
            ]
        state['targets'][str(target)] = record
        return state

    def should_push(self, state, target, games):
        return logic.should_push(self.last_games(state, target), games)

    def claim(self, state, target, key, games=None, when=None):
        self.mark(state, target, key, games=games, when=when)
        self.save(state)
        return True
