"""整份测试共用的 NoneBot 引导。

为什么需要它：每个 wiring 用例都要 `nonebot.init()` 再载入 `muicebot`，但 NoneBot
的插件管理器是全局的——同一进程里重复 init + `load_plugin` 会抛
`Plugin already exists`。以前 `python -m unittest discover tests` 因此直接挂掉
十个模块，只能一个一个单独跑，等于「文档里写的跑法」本身就是坏的。

这里做成幂等：已经初始化就复用，已经载入就不再载入；需要超管的模块可以追加
超管号（与 `.env` 里的合并，不覆盖）。

用法：

    from tests._bootstrap import ensure_nonebot
    ensure_nonebot()                 # 普通 wiring 用例
    ensure_nonebot(superusers={'1000'})   # 需要管理员判定时
"""

from __future__ import annotations

import nonebot

_READY = False


def _loaded(name: str) -> bool:
    try:
        plugins = nonebot.get_loaded_plugins()
    except ValueError:  # 还没 init
        return False
    return any(str(getattr(plugin, 'name', '')) == name for plugin in plugins)


def ensure_nonebot(*, superusers=None, plugin: str = 'muicebot'):
    """保证当前进程里 NoneBot 已初始化、指定插件已载入。重复调用无副作用。"""
    global _READY

    if not _READY:
        try:
            nonebot.get_driver()
        except ValueError:
            nonebot.init(**({'superusers': set(superusers)} if superusers else {}))
        if plugin and not _loaded(plugin):
            nonebot.load_plugin(plugin)
        _READY = True

    if superusers:
        # 合并而不是覆盖：.env 里可能已经有真实超管号，覆盖掉会让别的用例
        # 悄悄换了身份判定。
        config = nonebot.get_driver().config
        existing = {str(user) for user in (getattr(config, 'superusers', None) or ())}
        config.superusers = existing | {str(user) for user in superusers}

    return nonebot.get_driver()
