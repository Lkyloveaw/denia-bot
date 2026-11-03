# Attribution / 署名

本项目的灾害预警模块基于 / 参考以下项目二次开发：

- **项目**：astrbot_plugin_disaster_warning（灾害预警）
- **作者**：DBJD-CR & Aloys233
- **仓库**：https://github.com/Pancakes-Labs/astrbot_plugin_disaster_warning
- **版本/提交**：v1.6.5 / `20ba182a36dcb58e6f711302813c092ae05a574b`（2026-10-05）
- **许可证**：AGPL-3.0（全文见 `disaster-warning-LICENSE`）
- **上游数据源**：Jian Project、EQSC、Wolfx、FAN Studio 等；本仓库 V1 只接 Jian/EQSC/Wolfx，FAN 不作为长期依赖。

上游代码与数据的版权归原作者/原机构所有。本仓库按上游协议与行为重写，不是上游整包副本；为避免静默合并，覆盖范围内的 port 文件按 AGPL-3.0 分发。

## 本仓库的主要修改

- 重写 NoneBot2 + MuiceBot 接线、配置、状态与面板；
- 新增 Wolfx 免鉴权地震、Jian 气象/海啸、EQSC 台风客户端；
- 新增 B Bulletin 卡片与自建 Playwright 渲染服务；
- 新增首报/后续口吻、过滤、去重、停编与查询命令；
- 新增本仓库 pytest 与许可边界检查。

## 本仓库版权

本仓库的修改与新增部分：

- Copyright (C) 2026 Lkyloveaw
- 许可证：AGPL-3.0（仅覆盖 `disaster-warning-NOTICE.md` 列出的移植文件）

再分发时请保留本文件、`disaster-warning-LICENSE`、`disaster-warning-NOTICE.md` 与上游署名，并按 AGPL-3.0 提供完整源代码。

## 数据署名

灾害数据来自中国气象局、自然资源部海啸预警中心、中国地震台网、日本气象厅及 EQSC/Wolfx 等公开渠道；数据本身不因本仓库代码许可而改变，使用时应遵守各数据源条款并以官方发布为准。
