"""QQ 机器人入口 —— SnowLuma (OneBot v11) + NoneBot2 + MuiceBot"""

import os
from conversation_header import configure
os.environ['OPENAI_CUSTOM_HEADERS'] = configure()

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 初始化 NoneBot（读取 .env）
nonebot.init()

driver = nonebot.get_driver()

# 注册 OneBot v11 适配器（SnowLuma 走这个协议）
driver.register_adapter(OneBotV11Adapter)

# MuiceBot —— LLM 聊天 + 人格模板
nonebot.load_plugin("muicebot")

# 本地插件目录由 MuiceBot 在加载时统一处理（它需要 __plugin_meta__）。
# 这里不能再提前 load_plugins，否则插件 import muicebot 时它尚未注册。


if __name__ == "__main__":
    nonebot.run()
