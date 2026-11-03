# 灾害预警：事件身份、去重、存储与统计口径（V1）

日期：2026-10-31
状态：V1 已实现（SQLite + Runtime + 面板 API）。
上游参考：`Pancakes-Labs/astrbot_plugin_disaster_warning` v1.6.5，`core/domain/event_identity.py`、`core/services/identity/event_deduplication_service.py`、`core/storage/database_manager.py`；目标端先例：`nonebot/status_store.py`。

## 1. 用户已确认的决策

| 决策 | 结论 |
|---|---|
| 保留范围 | **30 天**；存所有进入分发的事件（含 filtered/ignored） |
| 跨源合并 | **不合并**；按各源自己的 key，CENC/JMA/Wolfx 可各推一条 |
| 统计展示 | 后端 API + 面板最近事件 + 24h/7d 计数 |
| 推送记录 | **每个目标一行**成功/失败；V1 不重试、不补发 |
| 训练/取消报 | **不推送，入库标 `ignored`**，保留审计 |

## 2. 事件身份

| 灾种 | key | 说明 |
|---|---|---|
| 地震 | `source:event_id:report_num` | 同源同报唯一；不同报是不同行 |
| 气象 | `jian:weather:alert_id:<hash>` | hash = effective/title/description；更新即新 key |
| 海啸 | `jian:tsunami:alert_id:number:<hash>` | 报次 + 标题/等级/发震时间 |
| 台风 | `eqsc:typhoon:id:updated:lat:lon:wind` | 轨迹/强度变化即新 key |
| 台风停编 | `eqsc:typhoon:id:stopped:updated` | 停编单独一行 |

- **不跨源合并**：同一物理地震被 CENC/JMA 同时报时，各推各的；这是 V1 明确取舍，避免时间/地点/震级聚类误并。
- P2P/PancakesAPI 尚未接入；未来接入时沿用“源 + 源 event_id + 报次”，不引入全局事件 id。
- `seen` 仍放在 JSON 状态（快速去重）；SQLite 只做历史/统计，不作为去重唯一来源。重启时 `seen` 与库同时存在，已推送事件不会因库丢失而重推——除非状态文件也被清空（与 V1 既有语义一致）。

## 3. 去重规则

- 同源同报：`seen` 命中即跳过，不重复入库。
- 同源多报：地震按 `report_num` 分行；JMA EEW 第 2..N-1 报标 `ignored`（首报/最终报推送）；气象/海啸更新生成新 key；台风每次轨迹/强度变化生成新 key。
- 训练/取消：`is_training` / `is_cancel` 直接标 `ignored`，不推送；`detail` 写“训练报/取消报”。
- 解除/停编：海啸“解除”仍按正常事件处理（`within_tsunami_filters` 放行）；台风 `isActive=false` 走停编 key。
- 首轮基线：只写 `seen`，**不写事件库**，避免一次 50 条历史列表被当成 24h 事件；这是“所有处理事件”的边界解释，需要审计基线时另开能力。
- 跨源：不互斥；同源优先。

## 4. SQLite schema（V1）

路径：`nonebot/data/disaster_warning.sqlite3`（与 JSON 配置/状态同目录；文件权限 600）。
`PRAGMA user_version=1`；WAL + `synchronous=NORMAL`；每个操作独立连接、退出关闭（Windows 上不锁库）。

### events

| 列 | 类型 | 说明 |
|---|---|---|
| `event_key` | TEXT PK | 上表 key |
| `kind` | TEXT | `earthquake/weather/tsunami/typhoon/typhoon_stop` |
| `source` | TEXT | `cenc_eew/jma_eew/jian/eqsc/...` |
| `event_id` | TEXT | 源事件 id |
| `report_num` | INTEGER | 报次；无则 0 |
| `title` | TEXT | 标题/名称 |
| `place` | TEXT | 地点/地区 |
| `magnitude` | REAL | 震级/海啸震级；无则 NULL |
| `occurred_at` | TEXT | 发震/生效/更新时间 |
| `status` | TEXT | `pushed/failed/filtered/ignored` |
| `tone` | TEXT | `neutral/character` |
| `detail` | TEXT | 训练报/JMA 中间报/等级等短说明 |
| `first_seen` | REAL | 首次入库 |
| `last_seen` | REAL | 最近处理；**status 更新不刷新**，保证 30 天保留 |
| `pushes/push_ok/push_fail` | INTEGER | 目标级计数 |

索引：`last_seen`、`(kind,last_seen)`。
不存 token、原始请求体、完整上游报文；`detail` 截 200 字。

### pushes

| 列 | 说明 |
|---|---|
| `id` | 自增 |
| `event_key` | 关联事件 |
| `ts` | 尝试时间 |
| `target_type` | `group/private` |
| `target` | 群号/QQ 号 |
| `ok` | 1 成功 / 0 失败 |
| `error` | 异常摘要，截 300 字；成功为空 |

索引：`event_key`、`ts`。每目标一行；失败不重试。

### source_health

`source` PK、`last_ok`、`last_error`、`error_at`。
由 `Runtime._save_state()` 从 JSON 状态的 `last_poll/errors` 同步；用于面板/排障，不替代 JSON 状态。

## 5. 统计口径

`EventStore.stats(days=7)`：

- `windows.24h` / `windows.7d`：
  - `events` 总数；`pushed/failed/filtered/ignored` 按状态计数；
  - `by_kind`：每个灾种的状态计数；
  - `push_ok/push_fail`：推送目标数（不是事件数）。
- `recent(limit)`：按 `last_seen DESC`，最多 50 条；字段与 events 一致。
- `source_health` 只做状态，不参与趋势。
- 计数单位：**事件行**按 `event_key`，不是物理地震；同一地震多报会多行。推送成功/失败按目标行。
- 面板 API：`GET /api/disaster/history?limit=20&days=7`（`authorized`），返回 `stats` + `events`；面板显示 24h/7d 计数和最近事件。`days` 当前用于校验/展示，窗口固定 24h/7d。

## 6. 保留与自愈

- `Runtime.prepare()` 启动时 `prune(KEEP_DAYS=30)`：删除 `last_seen < now-30d` 的事件，以及早于 cutoff 的 pushes / 孤儿 pushes；源健康保留。
- 不用 `VACUUM`；SQLite 自动复用页。需要压缩可后续加。
- 启动时若库损坏：移至 `disaster_warning.sqlite3.bak-<ts>`（含 `-wal/-shm`），重建空库；机器人不因历史库损坏而失败。
- 目录不可写/建库失败：`ready=False`，事件历史不可用，但不阻塞推送；状态命令/面板返回 `ready=false`。
- 迁移：V1 是首个 schema；用 `CREATE TABLE IF NOT EXISTS` + `user_version=1`。未来加列走 `_ensure_schema`/`ALTER TABLE`；旧库缺列时补默认，不重建。

## 7. Runtime 接入

- `Runtime.__init__` 建 `EventStore(data/disaster_warning.sqlite3)`；`prepare()` 裁剪；`stop()` 关闭（当前无持久连接，保留接口）。
- `_dispatch`：`seen` 后先记 `filtered`，训练/取消→`ignored`，过滤→保持 `filtered`，JMA 中间报→`ignored`，无目标→`filtered`，推送后→`pushed/failed`。
- 气象/海啸/台风/停编同样先 `filtered`，再按过滤/目标/推送更新；`event_key` 传给 `_push`。
- `_push`：每个目标成功/失败写 `pushes`；一个目标成功即返回 True（事件 `pushed`），部分失败仍有 `push_fail` 行。
- 推送 override（测试/其他插件）不写目标行，但事件状态仍按返回值更新。
- `status()` 增加 `history`；`地震源状态`/`灾害源状态` 增加“事件库：24h…/7d…”一行。
- 面板 `GET /api/disaster/history` 只读；`POST` 仍走配置白名单/CSRF。

## 8. 测试映射

| 文件 | 覆盖 |
|---|---|
| `test_disaster_store.py` | 身份/PK、状态更新不改 last_seen、推送记录、24h/7d、30 天清理、源健康、损坏自愈、无 token 列 |
| `test_disaster_wiring.py` | 事件记录、训练/取消 `ignored`、过滤 `filtered`、每目标推送、生命周期 |
| `test_disaster_china_wiring.py` | 气象入库、台风/停编、首报/后续 |
| `test_disaster_panel.py` | history 空库/有事件/推送计数、鉴权 |

当前全量见最新 pytest；本轮新增 store 8 + wiring 4 + panel 2 个用例。验证：disaster 定向 **43 passed**；全量 **504 passed / 3 failed / 1 skipped**，3 failed 均为并发 Epic/游戏用例，不在 disaster。

## 9. 风险与限制

- **不跨源合并**会重复：CENC/JMA 同时报同一地震时，两条推送、两行事件；这是用户明确选择，后续若要合并需另开决策。
- `seen` 与 SQLite 双写：极端情况下状态文件丢失但事件库还在，重启会重新入库/重推；V1 接受。
- 首轮基线不入库：24h/7d 统计从实际分发开始，不包含启动前历史。
- push 记录在 `_push` 内同步写 SQLite；事件量大时仍是毫秒级，但高并发下可能成为瓶颈；V1 单群 + 主人可接受。
- 面板 `history` 每次打开新连接；WAL 并发读不阻塞写；极端锁冲突会返回 `ready=false`/空列表。
- 训练/取消报入库会增加行数；30 天保留 + 低频事件可控。
- 多 bot/多会话、跨源融合、重试队列不在 V1。
- 30 天是保留上限，不是“完整历史”；需要归档另做导出。

## 10. 决策回填

- 30 天 + 所有分发事件；不跨源合并；API+面板；每目标推送记录；训练/取消 `ignored` 不推送。
- schema V1：events/pushes/source_health；`user_version=1`。
- 关闭本票后「验收、灰度与回滚」可继续，但 live 仍需凭证/容器。
