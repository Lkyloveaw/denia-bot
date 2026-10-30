"""数据库迁移 runner

`python -m nonebot_plugin_orm` 要求 NoneBot 已初始化，
而这个包的 __init__ 又需要 driver，所以直接 -m 跑不起来。
这个脚本先 init 再调它的 CLI。

用法：
    .venv\\Scripts\\python.exe migrate_db.py upgrade     # 升级到最新
    .venv\\Scripts\\python.exe migrate_db.py current     # 看当前版本
    .venv\\Scripts\\python.exe migrate_db.py check       # 检查是否一致
"""

import sys

import nonebot

nonebot.init()

# 加载插件，让 ORM 能发现 MuiceBot 的模型定义
nonebot.load_plugin("muicebot")

from nonebot_plugin_orm.__main__ import main as orm_main  # noqa: E402

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    print(f"[迁移] 执行: {cmd}")
    orm_main([cmd])
