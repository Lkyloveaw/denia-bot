# Attribution / 署名

本仓库的 Epic 喜加一功能在**接口事实与行为口径**上参考了下列上游项目；数据抓取、免费判定、推送去重、卡片渲染与面板接线均为本仓库独立重写，没有逐行复制上游代码。

| 项 | 值 |
|---|---|
| 项目 | `CJSen/astrbot_plugin_epic_free_games_notice` |
| 作者 | CJSen |
| 版本 | v0.0.4 |
| 提交 | `8dcd0f93a90cf541322b31a355cf3dccd5b1982a`（2025-12-18） |
| 仓库 | <https://github.com/CJSen/astrbot_plugin_epic_free_games_notice> |
| 查阅日期 | 2026-10-31 |
| 上游许可证 | 仓库**未附 LICENSE 文件**；README 声明 AGPL-3.0，本仓库按该声明视作 AGPL-3.0 处理（见 `epicfree-LICENSE`） |
| 上游的数据来源 | `Soulter/astrbot_plugin_essential`（HEAD `1d7cd936`，GitHub `license: null`）；上游 README 自述取数代码「完全 copy」自该项目 |

上游代码的版权归原作者所有。上游未附许可证文本，本仓库按其在 README 中声明的 AGPL-3.0 补齐许可文件；若上游后续补充许可证，以补充后的版本为准。

## 本仓库的主要修改

- 重写数据层：`httpx` 请求、HTTP/200-错误体/空数据分类、同域名重试与备用域名。
- 重写免费判定：遍历 `promotions.promotionalOffers` 的每一组、每一条 offer，按活跃 0% 窗口判定，不依赖数组顺序。
- 新增集合级去重：键为每款游戏 `id` + 该条 0% offer 的 `startDate`；纯下架不推，同一游戏隔期再免费算新一期。
- 新增 Pillow 卡片与文本兜底；新增「卡片 + 一条全部链接」两条式投递。
- 新增面板配置与「立即推送」触发；查询权限收敛为主人/群主/群管理员。
- 重写测试：真实 CN/US/顺序反转 fixture、状态、渲染、面板与接线用例。

## 本仓库版权

本仓库的修改与新增部分：

- Copyright (C) 2026 Lkyloveaw
- 许可证：AGPL-3.0（仅覆盖 `epic_free_*`、`plugins/epic_free.py` 与对应测试）

再分发时请保留本文件、`epicfree-NOTICE.md`、`epicfree-LICENSE` 与上游署名，并按 AGPL-3.0 提供完整源代码。
