# endfield 第三方声明

本仓库的 endfield 插件基于以下上游项目移植/改写：

- 上游：`Entropy-Increase-Team/astrbot_plugin_endfield`
- 版本：v3.0.3
- 提交：`41482b7`（2026-08-24）
- 许可证：AGPL-3.0
- 上游地址：https://github.com/Entropy-Increase-Team/astrbot_plugin_endfield

## 同源移植（AGPL-3.0-only）

以下文件/资源与上游同源衍生，按 AGPL-3.0-only 分发：

- `nonebot/endfield/client.py`（源自 `core/client.py`）
- `nonebot/endfield/store.py`（源自 `core/user.py`）
- `nonebot/endfield/render.py`（源自 `core/render.py`）
- 根 `LICENSE-AGPL-3.0`

当前没有分发上游 16 套模板及图片资源，也没有 `nonebot/endfield/templates/` 目录。

## 新写（MIT）

以下为 denia-bot 新写代码，按仓库 MIT 分发：

- `nonebot/endfield/config.py`
- `nonebot/endfield/logic.py`
- `nonebot/endfield/cards.py`（自包含通用图卡）
- `nonebot/endfield/scheduler.py`
- `nonebot/plugins/endfield.py`
- `nonebot/endfield_panel.py`
- `nonebot/dashboard.py` / `dashboard.html` 的 endfield 增量
- `nonebot/tests/endfield/`

## 官方 NoneBot 插件

`Entropy-Increase-Team/nonebot-plugin-endfield`（v0.2.5 / `526dc466`）**仅作协议与设计参考，未复制代码**。
其仓库 `LICENSE` 为 AGPL-3.0，`pyproject.toml` 写 MIT，许可证存在冲突；在运营方书面澄清前不复制其代码。

## 其他

- HTML→PNG 渲染服务是独立部署的前置服务，本次合并未分发其源码或容器；接口见 `docs/endfield.md`。
- 字体：图卡使用渲染器镜像的 Noto CJK；不内联上游 8 MB 字体。
- 本文件不包含 API Key、framework token、QQ 号等敏感信息。
