"""超级管理员名单：启动时加载，运行中按文件变更热应用。

NoneBot 原本只在启动时从 `.env` 读 `SUPERUSERS`。这个插件把它换成
`data/superusers.json`（面板可改），并把结果写回 `driver.config.superusers`
—— 所有插件的权限判定读的都是这一处，所以改完名单，`限流`、`换模型`、
`查看状态`、`解析开关` 立刻按新名单生效，不用重启。

为什么用轮询而不是文件监听：inotify/watchdog 要多一个依赖，而 30 秒的延迟对
「改完权限」这种低频操作完全够用；轮询只 stat 一个文件，代价可以忽略。
"""

from __future__ import annotations

import asyncio

from nonebot import get_driver, logger
from muicebot.plugin import PluginMetadata

import superuser_settings as settings

__plugin_meta__ = PluginMetadata(
    name='superusers',
    description='从数据文件加载超级管理员名单并热更新',
    usage='在管理面板「超级管理员」里维护；.env 的 SUPERUSERS 仅作首次的初始值',
)

driver = get_driver()

POLL_SECONDS = 30
"""检查间隔。权限变更不是高频操作，30 秒足够，也不值得上文件监听依赖。"""

_env_users: set[str] = set()
_task: asyncio.Task | None = None
_last_stamp: float | None = None


def _stamp() -> float:
    try:
        return settings.PATH.stat().st_mtime
    except OSError:
        return 0.0


def apply() -> tuple[list[str], str]:
    """把当前该用的名单写进 driver 配置，返回 `(名单, 来源)`。"""
    users, source = settings.resolve(_env_users)
    driver.config.superusers = set(users)
    return users, source


def _source_text(source: str) -> str:
    return '面板名单' if source == 'file' else '.env 初始值'


async def watch() -> None:
    global _last_stamp
    while True:
        await asyncio.sleep(POLL_SECONDS)
        stamp = _stamp()
        if stamp == _last_stamp:
            continue
        _last_stamp = stamp
        try:
            users, source = apply()
            logger.info(f'[auth] 超级管理员名单已更新：{len(users)} 人（{_source_text(source)}）')
        except Exception:  # noqa: BLE001 - 热更新失败不该让任务退出
            logger.exception('[auth] 超级管理员名单热更新失败')


@driver.on_startup
async def start() -> None:
    global _env_users, _task, _last_stamp
    # 先把 .env 的值记下来：后面 driver.config.superusers 会被覆盖，
    # 再读就只剩覆盖后的结果了，回退时会误以为「.env 也是这份名单」。
    _env_users = {str(user) for user in (getattr(driver.config, 'superusers', None) or ())}
    users, source = apply()
    _last_stamp = _stamp()
    _task = asyncio.create_task(watch())
    logger.info(
        f'[auth] 超级管理员 {len(users)} 人（{_source_text(source)}）'
        f'｜名单文件 {settings.PATH}'
    )


@driver.on_shutdown
async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
        _task = None
