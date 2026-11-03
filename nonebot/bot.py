"""QQ 机器人入口 —— SnowLuma (OneBot v11) + NoneBot2 + MuiceBot"""

import nonebot
import os
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from conversation_header import configure

# SDK clients created during this process share the ID; each restart gets a new one.
os.environ['OPENAI_CUSTOM_HEADERS'] = configure()

# 初始化 NoneBot（读取 .env）
nonebot.init(command_start={'.'})

driver = nonebot.get_driver()

# 注册 OneBot v11 适配器（SnowLuma 走这个协议）
driver.register_adapter(OneBotV11Adapter)

@nonebot.get_app().get('/dashboard-health', include_in_schema=False)
async def dashboard_health():
    return {
        'service': 'qqbot-nonebot',
        'ready': True,
        'connected': '3892894069' in nonebot.get_bots(),
        'pid': os.getpid(),
        'start_token': os.environ.get('QQBOT_START_TOKEN'),
    }

# MuiceBot —— LLM 聊天 + 人格模板
nonebot.load_plugin("muicebot")

from command_policy import install_command_rules
install_command_rules()

# 本地插件目录由 MuiceBot 在加载时统一处理（它需要 __plugin_meta__）。
# 这里不能再提前 load_plugins，否则插件 import muicebot 时它尚未注册。


if __name__ == "__main__":
    nonebot.run()
