# 第三方来源：astrbot_plugin_epic_free_games_notice

本仓库的「Epic 喜加一」相关代码按 AGPL-3.0 分发，**不属于仓库根目录 MIT 授权的部分**。上游仓库本身未附 LICENSE 文件，但其 README 明确声明 AGPL-3.0；本仓库按该声明保守处理。

| 项 | 值 |
|---|---|
| 项目 | `CJSen/astrbot_plugin_epic_free_games_notice` |
| 作者 | CJSen |
| 版本 | v0.0.4 |
| 提交 | `8dcd0f93a90cf541322b31a355cf3dccd5b1982a`（2025-12-18） |
| 仓库 | <https://github.com/CJSen/astrbot_plugin_epic_free_games_notice> |
| 许可证 | 上游 README 声明 AGPL-3.0，仓库未附 LICENSE；本仓库按 AGPL-3.0 分发（全文见 `epicfree-LICENSE`） |
| 上游的数据来源 | `Soulter/astrbot_plugin_essential`（HEAD `1d7cd936`，同样未附许可证） |

## 覆盖范围

以下文件按 AGPL-3.0 分发：

- `nonebot/epic_free_logic.py`
- `nonebot/epic_free_service.py`
- `nonebot/epic_free_image.py`
- `nonebot/epic_free_config.py`
- `nonebot/plugins/epic_free.py`
- `nonebot/tests/test_epic_free_logic.py`
- `nonebot/tests/test_epic_free_service.py`
- `nonebot/tests/test_epic_free_image.py`
- `nonebot/tests/test_epic_free_config.py`
- `nonebot/tests/test_epic_free_plugin.py`
- `nonebot/tests/test_epic_free_dashboard.py`

`dashboard.py` / `dashboard.html` / `gate_logic.py` 中的 Epic 接线补丁只注册路由、面板区块与帮助行，不含上游代码，仍按仓库 MIT 授权；它们与上述 AGPL 模块共同运行，再分发时请一并保留本 NOTICE。

## 本移植的主要改动

**不是逐行搬运，而是按本项目运行环境重写**：

1. **数据层**：上游用 `aiohttp` 单次请求、只看状态码；这里改 `httpx`，区分网络错误、非 200、HTTP 200 错误体、空数据，并加同域名重试与备用域名。
2. **免费判定**：上游只取 `promotionalOffers[0].promotionalOffers[0]`；这里遍历每一组、每一条 offer，按活跃 0% 窗口判定，并用价格做交叉校验。
3. **调度与去重**：上游按「每周几 + 几点」重复推送；这里改为轮询 + 集合变化去重，首装只记录、重启不补发、纯下架不推。
4. **投递**：上游只有文本；这里新增 Pillow 卡片、文本兜底，以及「卡片 + 一条全部链接」两条式投递。
5. **配置**：上游 `_conf_schema.json` + AstrBot 配置页；这里改为 `data/epic_free.json` + 管理面板，两个进程共享。
6. **权限**：上游查询对所有会话开放；这里按项目决议收敛为主人/群主/群管理员，`Epic 测试` 仅主人。
7. **测试**：新增真实 CN/US 响应与 offer 顺序反转 fixture，覆盖判定、去重、状态、渲染、面板与接线。

## 义务说明

AGPL-3.0 第 13 条要求：若通过网络向用户提供服务，须向使用者提供对应源码。本仓库为私有部署、不对外分发；**再分发时必须保留本 NOTICE、`epicfree-ATTRIBUTION.md`、`epicfree-LICENSE` 与上游署名，并按 AGPL-3.0 提供完整源代码**。修改 `epic_free_*` 文件时不要删除文件头的来源注释。
