# AGPL 隔离与移植边界：灾害预警

日期：2026-10-31
上游：`Pancakes-Labs/astrbot_plugin_disaster_warning` v1.6.5，commit `20ba182a36dcb58e6f711302813c092ae05a574b`，作者 DBJD-CR & Aloys233，**AGPL-3.0**。
先例：`docs/third-party/statuscard-NOTICE.md`（同一仓库的 AGPL 隔离方式）。

## 结论

- 灾害预警的 **port 文件按 AGPL-3.0** 分发，带统一文件头。
- 面板接入、帮助分组、自建 Playwright 渲染服务、原型/研究文档保持 **MIT**。
- 不复制上游整包；不把上游 WebUI、模板、AstrBot 胶水、telemetry、40 条命令带进仓库。
- 上游许可证全文：`docs/third-party/disaster-warning-LICENSE`。
- 来源、覆盖范围与改动：`docs/third-party/disaster-warning-NOTICE.md`。
- 数据/机构署名：`docs/third-party/disaster-warning-ATTRIBUTION.md`。
- README 许可段已加例外；再分发必须保留 NOTICE/LICENSE/ATTRIBUTION 与上游署名，并按 AGPL-3.0 提供完整源码。

## 文件头模板

所有 AGPL port 文件开头使用：

```python
# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md
```

- 新文件如果直接复制/改写上游代码，必须带这个头。
- 只参考协议事实、完全自写的文件（如 docker renderer、原型）保持 MIT，不冒充 AGPL。
- 不要在 AGPL 文件里粘贴上游整段代码；需要时按协议/行为重写并注明来源。

## AGPL 覆盖文件（22）

| 文件 | 说明 |
|---|---|
| `nonebot/disaster_config.py` | JSON 配置/白名单/令牌遮蔽 |
| `nonebot/disaster_logic.py` | Wolfx 地震解析/过滤/文案 |
| `nonebot/disaster_sources.py` | Wolfx HTTP |
| `nonebot/disaster_state.py` | 去重/基线状态 |
| `nonebot/disaster_render.py` | B Bulletin 四类卡 + 渲染客户端 |
| `nonebot/disaster_china_logic.py` | Jian 气象/海啸 + EQSC 台风纯逻辑 |
| `nonebot/disaster_china_sources.py` | Jian 换票/WS + EQSC token/台风 |
| `nonebot/disaster_tone.py` | 首报/后续口吻判定 |
| `nonebot/disaster_store.py` | SQLite 事件/推送/源健康/统计 |
| `nonebot/plugins/disaster_warning.py` | NoneBot2 接线/任务/命令 |
| `nonebot/tests/test_disaster_*.py` | 上述模块的 pytest |

## MIT / 本仓库原创

| 文件 | 说明 |
|---|---|
| `nonebot/dashboard.py` / `dashboard.html` 的灾害面板接入 | 面板骨架 MIT；通过内部 API 使用 AGPL 模块（与 statuscard 同先例） |
| `nonebot/gate_logic.py` 的「灾害」帮助分组 | 仅增分组/指令表 |
| `docker/playwright-renderer/` | 自建渲染服务，原创造型；不复制上游模板 |
| `docs/research/prototypes/` | 自写 A/B/C 原型、固定样例、PNG |
| `docs/research/disaster-*.md` | 研究/规格文档 |
| `nonebot/tests/test_license_boundary.py` | 本仓库原创许可边界测试 |

注：`nonebot/tests/test_help_coverage.py` 已因 statuscard 移植按 AGPL 分发（见 `statuscard-NOTICE.md`），不在本条 MIT 例外。

## 上游参考文件（只读研判，不复制进仓库）

- `core/parsers/weather_parser.py` / `tsunami_parser.py` / `typhoon_parser.py`
- `core/domain/typhoon/typhoon_event_adapter.py`
- `core/network/websocket/jian_project_connection_policy.py`
- `core/network/http/eqsc_token_manager.py` / `eqsc_typhoon_client.py`
- `core/services/eqsc/eqsc_typhoon_poll_service.py`
- `core/sources/source_router.py` / `source_catalog.py`
- `docs/EQSC API.md`

这些文件用于确认鉴权链、字段名、首帧/基线、停编语义与 EQSC 台风响应；未整段复制。

## 本移植的边界

| 上游 | 本仓库 | 许可 |
|---|---|---|
| AstrBot `Star`/`filter`/`MessageChain` | NoneBot2 matcher/事件/`MessageSegment` | AGPL port |
| 上游 36 源/多平台融合 | V1 只接 Wolfx + Jian + EQSC | AGPL port |
| 上游 Playwright/Pillow 模板 | 自建 HTTP Playwright + B 卡（内联 CSS/SVG） | AGPL port（渲染器本体 MIT） |
| 上游 WebUI 七页 | 本仓库 FastAPI 面板一块 | MIT 接入 |
| 上游 40 条命令 | V1 8 条核心命令 | AGPL port |
| 上游 telemetry/通知中心 | 不做 | — |
| FAN Studio | 只作为历史参考，不做长期依赖 | — |

## 义务与检查清单

- [x] `docs/third-party/disaster-warning-LICENSE`（AGPL-3.0 全文）
- [x] `docs/third-party/disaster-warning-NOTICE.md`（来源/覆盖/改动/义务）
- [x] `docs/third-party/disaster-warning-ATTRIBUTION.md`（作者/数据署名）
- [x] README 许可段例外
- [x] 22 个 port 文件统一头
- [x] `test_license_boundary.py` 防回归
- [x] 不复制上游整包；不含 token/私人数据
- [ ] 再分发时保留 NOTICE/LICENSE/ATTRIBUTION 并按 AGPL-3.0 提供源码（部署者责任）
- [ ] 若新增直接复制/改写的上游文件，补头、补 NOTICE、补测试

AGPL-3.0 第 13 条：通过网络提供修改版时，须向使用者提供对应源码。本仓库自身即对应源码；私有部署也建议保留完整仓库与 NOTICE。
