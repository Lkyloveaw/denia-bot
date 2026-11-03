# 研判：Jian Project（气象/海啸）与 EQSC（台风）接入

研判日期：2026-10-31
上游参考：`Pancakes-Labs/astrbot_plugin_disaster_warning` v1.6.5（commit `20ba182`）
目标：NoneBot2 + MuiceBot（OneBot v11）

## 结论

V1 采用 **mock-first** 接入，真实凭证到位后只换 live 客户端，不改解析/过滤/文案：

- **Jian**：WebSocket 混合源，负责中国气象预警 + 中国海啸；`lk_ → rt_ → at_` 换票，`all` 首帧只做基线。
- **EQSC**：HTTP 轮询，负责台风；`ARh. → ATn.` 换票，`/typhoonNMC.json` 取列表。
- 纯逻辑在 `nonebot/disaster_china_logic.py`，网络在 `nonebot/disaster_china_sources.py`，接线在 `nonebot/plugins/disaster_warning.py`。
- 当前无凭证，**未做 live 联调**；Jian 的 WS 外层、字段、EQSC 响应以 2026-10 上游代码与 `docs/EQSC API.md` 为准，mock 测试覆盖成功/错误/基线/去重/停编。

## Jian Project

### 鉴权链

| 步骤 | 请求 | 响应 |
|---|---|---|
| 登录密钥换长期 Token | `POST https://auth.sismotide.top/api/refresh`，`Authorization: Bearer lk_...` | `{"ok":true,"token":"rt_...","expires_after_min":259200,"max_connections":3}` |
| 长期 Token 换访问 Token | `POST https://auth.sismotide.top/api/access`，`Authorization: Bearer rt_...` | `{"ok":true,"token":"at_...","expires_after_sec":3600}` |
| 建立 WS | `wss://api.sismotide.top/all?key=at_...`，同时带 `X-API-Key: at_...` | 服务端推送 JSON |

- `lk_` 一次性，约 5 分钟有效；`rt_` 约 180 天；`at_` 约 1 小时。
- 错误码：`4101/4102` 登录密钥无效/过期，`4201/4202` 长期 Token 无效/过期，`4004` 访问 Token 过期，`4005` 并发连接满（默认 3）。
- **持久化时机**：`lk_ → rt_` 成功后立刻回写 `sources.jian.token`，不能等 WS 连上；否则 access/WS 失败会浪费一次性 `lk_`。实现用 `JianAuth(on_refresh=...)` 回调。
- WS 心跳 30 秒；断开后指数退避重连，访问 Token 过期/失效时重新换 access。

### 消息外层

```json
{"type":"weather","Data":{...}}
```

- `type=all`：首帧全量快照，**只做基线，不推送**；实现会尝试从 `weather` / `nmefc-tsunami` 子节点解析出当前事件并标记已见。
- `type=heartbeat` / `pong`：过滤。
- `type=weather`：中国气象局气象预警。
- `type=nmefc-tsunami`：自然资源部海啸预警中心。
- 其他 `cenc` / `cea` / `jma-eew` / `usgs` 等不接，V1 地震走 Wolfx。
- 业务体优先取 `Data`；兼容 `data` / `payload`，以及 `Data` 为列表。

### 气象预警字段

上游 `weather_parser.py` 读取：

| 字段 | 用途 |
|---|---|
| `id` | 事件 ID；尾部常带 `_YYYYMMDDHHMMSS`，可提取发布时间 |
| `title` / `headline` | 标题/副标题；标题缺失时 headline 兜底 |
| `description` | 正文 |
| `effective` | 生效时间 |
| `weather_type` / `weatherType` / `alertCode` / `code` | CMA 预警编码 |
| `province` / `city` / `district` / `adcode` | 地区 |
| `latitude` / `longitude` | 坐标（如有） |

V1 的颜色从标题/副标题/正文提取，顺序 `白色 < 蓝色 < 黄色 < 橙色 < 红色`；无法识别时按“黄色”阈值处理（避免误杀）。`filters.weather_min_color` 默认黄色；`local.enabled` 时按省市关键词双向包含过滤，地区和预警地区任一缺失时不过度拦截。

### 中国海啸字段

上游 `tsunami_parser.py` 的 Jian 分支是扁平字段：

| 字段 | 用途 |
|---|---|
| `id` / `code` | 事件 ID；缺失时用 `number + title + place + originTime` 生成稳定回退 ID |
| `title` / `headline` | 标题；缺失且有 level 时生成“海啸{level}警报” |
| `level` | `信息` / `蓝色` / `黄色` / `橙色` / `红色` / `解除` |
| `originTime` | 发震时间 |
| `number` | 报次 |
| `latitude` / `longitude` / `depth` / `magnitude` | 震中参数 |
| `place` | 震中地名 |
| `orgUnit` | 发布机构 |
| `description` / `htmlUrl` | 正文/详情 |

V1 兼容 FAN 的 `warningInfo` / `timeInfo` / `shockInfo` 嵌套。`filters.tsunami_min_level` 默认“信息”；`解除` 始终放行，避免只收到警报收不到解除。

## EQSC

### 鉴权与台风接口

- Base URL：`https://equake.top`
- RefreshToken：`ARh.` 开头，EQuake 设置 → 通用 → EQSC API 配置获取。
- 换 AccessToken：`GET /createAccessToken`，`Authorization: Bearer <ARh...>`；响应是纯文本 `ATn....,3600`（最后一个逗号切分）。AccessToken 约 1 小时，提前 60 秒刷新。
- 台风列表：`GET /typhoonNMC.json`，`Authorization: Bearer <ATn...>`；可选 `id=2518`（四位：年份后 2 位 + 编号 2 位）。
- 列表最多约 20 个最新台风，**包含历史/停编**；`isActive` 才是活跃标志。

响应：

```json
{"typhoon":[{
  "id":"2518","nameEN":"RAGASA","nameCN":"桦加沙","isActive":false,
  "historyTrack":[{"time":"2025/09/22 23:00:00","type":"SuperTY","typeNameCN":"超强台风",
    "latitude":19.5,"longitude":119.9,"pressure":920,"windSpeed":58,
    "speed":"NULL","direction":"NULL","directionCN":"NULL",
    "windCircle":{"30KTS":{"NE":480,"SE":340,"SW":340,"NW":480}}}],
  "futureTrack":[{"time":"2025/09/25 23:00:00","typeNameCN":"热带低压",
    "latitude":21.9,"longitude":106.1,"pressure":1003,"windSpeed":12}]
}]}
```

- `historyTrack` 顺序不保证；V1 按解析后的时间排序取最后一条作为“最新观测”，不取数组末项。
- `speed` 可能是数值、`STNR`（几乎停滞）或 `NULL`；`directionCN` 可能为 `NULL`。
- `futureTrack` 取时间最早的一条作为“预计”展示。
- 轮询默认 120 秒，钳制 30–600 秒；上游有 300 秒客户端缓存，V1 不做额外缓存，按轮询间隔拉取。
- 首轮只做基线；后续按 `id + 最新轨迹时间 + lat/lon + windSpeed` 指纹去重；活跃转停编推一次停编通知。

## 实现映射

| 文件 | 职责 |
|---|---|
| `nonebot/disaster_china_logic.py` | `WeatherAlert` / `TsunamiAlert` / `Typhoon`、解析、颜色/等级/地区/距离过滤、文案、去重键、`all` 基线遍历 |
| `nonebot/disaster_china_sources.py` | `JianAuth` / `JianConnection` / `JianProjectSource` / `JianSource`；`EqscSource` |
| `nonebot/plugins/disaster_warning.py` | `_jian_loop` / `_eqsc_loop`、`handle_jian_message`、`poll_typhoons`、推送/去重/状态、4 个命令 |
| `nonebot/gate_logic.py` | `.help` 新增「灾害」分组 |
| `nonebot/tests/test_disaster_china_*.py` | 纯逻辑、MockTransport、Runtime 接线 |

命令：

- `气象预警 [数量]`：最近气象预警（Jian）
- `台风`：当前活跃台风（EQSC）
- `海啸 [数量]`：最近海啸预警（Jian）
- `灾害源状态`：Wolfx/Jian/EQSC 状态与最近错误（仅主人）

令牌只落 `nonebot/data/disaster_warning.json`（原子写、chmod 600）；面板只回显 `token_configured` / 到期日，不回显明文。

## 验证

- `test_disaster_china_logic.py`：气象解析/颜色/省市过滤/文案；海啸等级/解除；台风最新轨迹/距离/停编。
- `test_disaster_china_sources.py`：`lk_→rt_→at_`、一次性 `lk_` 不二次兑换、`rt_` 直接换 access、EQSC `ARh.→ATn.`、台风列表、错误响应。
- `test_disaster_china_wiring.py`：`all` 基线不补推、增量去重、颜色/地区过滤、台风首轮基线/轨迹更新/停编、状态文本。
- 全量：**344 passed / 1 skipped**（2026-10-31，含并发 Epic 用例）。
- **未 live 验证**：没有 Jian/EQSC 凭证；Jian WS 真实首帧结构、EQSC 真实 token/台风列表需凭证到位后复核。

### 凭证到位后的 first-live checklist

1. 面板填 Jian 令牌（`lk_` 或 `rt_`），保存；启动机器人；确认日志出现“Jian 长期 Token 已更新并保存”，面板显示已配置。
2. 等 Jian `all` 首帧；确认没有历史气象/海啸补推；等一条真实增量，检查颜色/地区/文案。
3. 面板填 EQSC `ARh.`；`/灾害源状态` 看 EQSC 已配置；`/台风` 有活跃台风；首轮不补推，5 分钟后轨迹更新推一次。
4. 观察 1 小时：Jian access 过期重连；EQSC access 自动续期；错误进 `/灾害源状态`。
5. 不要把 token 写进 git、issue、截图或日志。

## 风险/未决

- Jian `all` 首帧真实结构未知；基线用“类型提示 + 字段特征”解析，可能漏标个别事件，首次 live 需观察是否补推旧事件。
- Jian 气象颜色从文本提取；无颜色时按黄色阈值处理，可能与官方严重度不完全一致。
- EQSC 列表包含历史；`isActive` 缺失时按活跃处理，首次 live 要确认字段存在。
- EQSC `speed`/`directionCN` 的 `NULL`/`STNR` 已按文本处理；风圈半径暂不展示。
- V1 中国三类只发文字，不出图；卡片/地图由 #31 原型票决定。
- 上游 AGPL：本实现按协议事实重写，未复制上游代码；上游署名与许可边界仍由 #29 处理。

## 上游依据

- `core/network/websocket/jian_project_connection_policy.py`
- `core/sources/source_router.py`、`core/sources/source_catalog.py`
- `core/parsers/weather_parser.py`、`core/parsers/tsunami_parser.py`
- `core/services/eqsc/eqsc_typhoon_poll_service.py`
- `core/network/http/eqsc_token_manager.py`、`core/network/http/eqsc_typhoon_client.py`
- `core/domain/typhoon/typhoon_event_adapter.py`
- `docs/EQSC API.md`
