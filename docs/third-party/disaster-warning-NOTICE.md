# 第三方来源：astrbot_plugin_disaster_warning

本仓库的「灾害预警」相关代码移植/重写自下列项目。**覆盖范围内的文件按 AGPL-3.0 分发**，不属于本仓库 MIT 授权。

| 项 | 值 |
|---|---|
| 项目 | `Pancakes-Labs/astrbot_plugin_disaster_warning`（灾害预警） |
| 作者 | DBJD-CR & Aloys233（`metadata.yaml`；提交作者 DBJD-CR） |
| 版本 | v1.6.5 |
| 提交 | `20ba182a36dcb58e6f711302813c092ae05a574b`（2026-10-05） |
| 仓库 | <https://github.com/Pancakes-Labs/astrbot_plugin_disaster_warning> |
| 许可证 | AGPL-3.0（全文见 `disaster-warning-LICENSE`） |

## 覆盖范围

以下文件是移植/衍生代码，按 **AGPL-3.0** 分发：

- `nonebot/disaster_config.py`
- `nonebot/disaster_logic.py`
- `nonebot/disaster_sources.py`
- `nonebot/disaster_state.py`
- `nonebot/disaster_render.py`
- `nonebot/disaster_china_logic.py`
- `nonebot/disaster_china_sources.py`
- `nonebot/disaster_tone.py`
- `nonebot/disaster_store.py`
- `nonebot/plugins/disaster_warning.py`
- `nonebot/tests/test_disaster_*.py`

以下文件仍按仓库根目录 `LICENSE`（MIT）授权：

- `nonebot/dashboard.py` / `nonebot/dashboard.html` 中的灾害预警面板接入（沿用 statuscard 先例：面板骨架保持 MIT，通过内部 API 使用 AGPL 模块）；
- `nonebot/gate_logic.py` 的「灾害」帮助分组；
- `docker/playwright-renderer/`（本仓库原创渲染服务，不复制上游模板）；
- `docs/research/prototypes/`（本仓库原创原型/样例）；
- `docs/research/disaster-*.md` 等研究文档；
- `nonebot/tests/test_license_boundary.py`（本仓库原创的许可边界检查）。

注意：`nonebot/tests/test_help_coverage.py` 已因 statuscard 移植按 AGPL 分发（见 `statuscard-NOTICE.md`），不在本条 MIT 例外内。

## 本移植的主要改动

不是逐行搬运，而是按本项目运行环境与用户选择重写；为避免静默合并，相关 port 文件仍按 AGPL 分发：

1. **框架**：AstrBot `Star` / `filter.command` / `MessageChain` → NoneBot2 + MuiceBot（OneBot v11）的 matcher、事件与 `MessageSegment`。
2. **数据源**：Wolfx 免鉴权地震（CENC/JMA）；Jian `lk_→rt_→at_` + WS（气象/中国海啸）；EQSC `ARh.→ATn.` + `/typhoonNMC.json`（台风）。凭证只落本机数据文件/环境变量，不进 git/issue。
3. **出图**：上游 Playwright/Pillow/远程 T2I 模板 → 本仓库自建 HTTP Playwright 渲染服务 + 自写 B Bulletin 卡（900×1200，内联 CSS/SVG，无上游模板）。失败回退文字。
4. **配置/面板**：上游 67 KB `_conf_schema.json` 与 WebUI → `data/disaster_warning.json` + FastAPI 面板；令牌不回显。
5. **事件与文案**：首轮基线、事件 ID/报次去重、JMA 中间报抑制；用户定稿 B 结构 + 首报中性/后续达妮娅。
6. **测试**：本仓库补充的 pytest（纯逻辑、MockTransport、Runtime 接线、B 卡、许可边界）。
7. **裁剪**：不做上游 WebUI 七页、40 条查询命令、telemetry、通知中心、FAN Studio 长期依赖。

## 义务说明

AGPL-3.0 第 13 条：若通过网络向用户提供修改后的程序，须向使用者提供对应源码。本仓库即对应源码；再分发时必须保留本 NOTICE、`disaster-warning-LICENSE`、`disaster-warning-ATTRIBUTION.md` 与上游署名，并按 AGPL-3.0 提供完整源代码。

上游代码与数据的版权归原作者/原机构所有；本仓库不声称拥有上游版权。代码不含 token、密钥、私人数据或上游整包副本。
