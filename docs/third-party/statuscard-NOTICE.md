# 第三方来源：astrbot_plugin_statuscard

本仓库的「服务器状态卡」相关代码移植自下列项目。**这些文件与上游同源衍生，按 AGPL-3.0 分发**，不属于本仓库 MIT 授权的部分。

| 项 | 值 |
|---|---|
| 项目 | `SX0YYYY/astrbot_plugin_statuscard`（服务器状态卡） |
| 作者 | 烬辞 |
| 版本 | v2.18.8 |
| 提交 | `fa6d0f566c7b387d1f0d76c12531e03f2d36d52e`（2026-10-30） |
| 仓库 | <https://github.com/SX0YYYY/astrbot_plugin_statuscard> |
| 许可证 | AGPL-3.0（全文见 `statuscard-LICENSE`） |
| 上游自己的上游 | `exynos967/astrbot_plugin_picstatus`（薄暝），见 `statuscard-ATTRIBUTION.md` |

## 覆盖范围

以下文件是移植/衍生代码，按 AGPL-3.0 分发：

- `nonebot/status_logic.py`
- `nonebot/status_collect.py`
- `nonebot/status_store.py`
- `nonebot/status_image.py`
- `nonebot/status_monitor.py`
- `nonebot/status_llm.py`
- `nonebot/status_config.py`
- `nonebot/plugins/statuscard.py`
- `nonebot/tests/test_status_*.py`

其余代码（对话、记忆、知识库、群话题、天气、链接解析、面板骨架等）仍按仓库根目录 `LICENSE`（MIT）授权。

## 本移植的主要改动

不是逐行搬运，而是按本项目的运行环境重写：

1. **出图**：上游把整页 HTML 发给远程 T2I 服务渲染；这里改成 **纯本地 Pillow 直绘**，主机名、进程名、磁盘等信息不出本机。
2. **统计口径**：上游读 AstrBot 的 `platform_stats` / `provider_stats` / `message_stats` 三张表；这里改为包装 `bot.call_api` 计发送、`on_message` 计接收、复用本仓库既有的 `token_logic` 账本，并通过包装 `Muice().model.ask` 统计 LLM 延迟与成功率。
3. **框架接线**：AstrBot 的 `Star` / `filter.command` / `MessageChain` 换成 NoneBot2 的 `on_message` matcher 与 `bot.call_api`。
4. **配置**：32 KB 的 `_conf_schema.json` 精简为 `data/statuscard.json`，面板可改关键项并支持热加载。
5. **面板**：AstrBot WebUI 内嵌页改为本仓库 `dashboard.py` / `dashboard.html` 的「服务器状态」页。
6. **裁剪**：去掉多平台、多机器人、通知中心分群路由、Komari Webhook、远程 T2I 开关（与「数据不出本机」相冲突）、中英双语。

## 义务说明

AGPL-3.0 第 13 条要求：若通过网络向用户提供服务，须向使用者提供对应源码。本仓库为私有部署、不对外分发，但**再分发时必须整包保留本 NOTICE、`statuscard-LICENSE` 与上游署名，并按 AGPL-3.0 提供完整源代码**。
