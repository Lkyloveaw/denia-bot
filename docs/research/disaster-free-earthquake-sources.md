# 研判：免鉴权地震源（Wolfx CENC/JMA）

研判日期：2026-10-31
上游：`Pancakes-Labs/astrbot_plugin_disaster_warning` v1.6.5（commit `20ba182`）
目标：本仓库 NoneBot2 + MuiceBot（OneBot v11）

## 结论

V1 的「地震附加」先用 **Wolfx 免鉴权 HTTP JSON**，不建 WebSocket：

- 轮询 `cenc_eew.json` / `cenc_eqlist.json` / `jma_eew.json` / `jma_eqlist.json`
- EEW 每 10 秒、列表每 60 秒
- 首轮只做基线，不把历史地震刷屏；列表首轮至少 40 条才设基线，并记录基线时间，之后只推比基线更新的事件
- 只接 CENC（中国地震台网/预警）与 JMA（日本气象厅）；P2P / PancakesAPI / CWA / USGS / GQ 暂不接

## 端点与字段

| 类型 | 端点 | 关键字段 |
|---|---|---|
| CENC EEW | `https://api.wolfx.jp/cenc_eew.json` | `EventID`、`ReportNum`、`OriginTime`、`HypoCenter`、`Latitude`、`Longitude`、`Magnitude`、`Depth`、`MaxIntensity` |
| CENC 列表 | `https://api.wolfx.jp/cenc_eqlist.json` | `No1..No50`；`type`（`reviewed`/`automatic`）、`EventID`、`time`、`placeName`、`magnitude`、`depth`、`latitude`、`longitude`、`intensity` |
| JMA EEW | `https://api.wolfx.jp/jma_eew.json` | `EventID`、`Serial`、`OriginTime`、`Hypocenter`、`Latitude`、`Longitude`、`Magunitude`/`Magnitude`、`Depth`、`MaxIntensity`、`isFinal`、`isCancel`、`isTraining`、`isAssumption` |
| JMA 列表 | `https://api.wolfx.jp/jma_eqlist.json` | `No1..No50`；`EventID`、`time_full`、`location`、`magnitude`、`shindo`、`depth`（如 `30km`）、`latitude`、`longitude` |

实测（2026-10-31）：

- `cenc_eqlist.json` 返回 50 条（`reviewed`），`jma_eqlist.json` 返回 50 条
- `cenc_eew.json` 与 `jma_eew.json` 均能解析出当前事件；字段与上表一致
- 控制台用 GBK 打 🚨 会报 `UnicodeEncodeError`；部署侧沿用 `PYTHONIOENCODING=utf-8`（见 `docs/PITFALLS.md`），与业务代码无关

## 最小集与取舍

接：

- CENC EEW：中国地震预警，首报/更新报；`ReportNum` 变化即视为新报
- CENC 列表：只接 `reviewed`（正式测定），跳过 `automatic`，避免自动测定与正式测定重复
- JMA EEW：跳过 `isCancel` / `isTraining` / `isAssumption`；只推第 1 报与最终报，中间报不刷屏
- JMA 列表：正式地震情报，按 `EventID` 去重

暂不接：

- P2P WebSocket（协议更复杂，Wolfx 已覆盖 JMA）
- PancakesAPI / USGS / Global Quake / CWA（V1 只做最小集，后续另开票）
- 多源融合与同事件跨源合并（现在 CENC 预警、CENC 正式测定、JMA 预警、JMA 情报各自独立）

## 接入策略

- **去重键**：`source:event_id:report_num`；不同源不互相覆盖
- **首轮静默**：四个源各自第一次成功轮询只标记 `seen`，不推送；`/地震预警` 仍可查询缓存
- **列表防回填**：首轮列表少于 40 条不设基线；基线时间写入 `data/disaster_warning_state.json`，后续只推比基线更新的事件
- **过滤**：`filters.earthquake_min_magnitude`（默认 4.5）；本地开启时用 `earthquake_max_distance_km`（默认 1000，0=不限）算 haversine 距离
- **推送**：面板配置的运维群 + 主人私聊；纯文字，失败只记日志、不回补（避免不确定时重发）
- **权限**：查询命令公开；`地震源状态` / `地震测试` 仅主人
- **状态**：`data/disaster_warning_state.json` 保存 `seen`、`initialized`、`baseline_at`、`last_poll`、`errors`、累计推送数，裁剪到最近 1000 条/14 天

## 实现文件

| 文件 | 职责 |
|---|---|
| `nonebot/disaster_logic.py` | 解析、距离/震级过滤、文本格式化、样例事件（无 nonebot/httpx） |
| `nonebot/disaster_sources.py` | Wolfx HTTP 客户端；HTTP 错误/非 JSON → `SourceError` |
| `nonebot/disaster_state.py` | 去重/基线/轮询状态 JSON，原子写 + 600 |
| `nonebot/plugins/disaster_warning.py` | 两个轮询任务、推送、命令、帮助行、启停钩子 |
| `nonebot/tests/test_disaster_*.py` | 逻辑、源、状态、接线、配置、面板用例 |

## 验证

- 2026-10-31：真实 Wolfx 拉取并解析成功；CENC/JMA 列表各 50 条，EEW 字段正确
- 真实 `Runtime` 首轮轮询：4 个源完成基线，缓存 CENC 50 / JMA 50，状态文件落盘
- 定向用例：42 passed；全量：**278 passed / 1 skipped**

## 风险与后续

- 轮询延迟：EEW 最多 10 秒、列表最多 60 秒；需要更低延迟再评估 P2P/WebSocket
- 端点与字段由 Wolfx 维护，可能变更；解析失败只记 `errors`，不阻塞其他源
- 首轮静默意味着重启期间正在发生的 EEW 不推送，但 `/地震预警` 可查当前缓存
- 图片/远程 Playwright、Jian/EQSC 中国三类、容器状态监视仍未接，按地图后续票推进
