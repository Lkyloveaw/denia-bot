# 灾害预警：NoneBot2 接线契约与测试映射

日期：2026-10-31
状态：V1 接线已实现；live 联调待凭证，Docker 容器待部署。
相关：`docs/research/disaster-v1-ux-spec.md`（用户可见规格）、`docs/research/disaster-license-boundary.md`（AGPL 边界）。
上游：`Pancakes-Labs/astrbot_plugin_disaster_warning` v1.6.5，commit `20ba182`。

## 1. 结论

- 纯逻辑（解析/过滤/文案/去重/状态）不 import NoneBot；接线只在 `nonebot/plugins/disaster_warning.py`。
- 命令走 `on_message(priority=1, block=True)`，不依赖 `@`，匹配 8 条核心命令；MuiceBot 的兜底 `.+` 不会抢。
- 后台入口是 4 个具名 task：`disaster-eew`、`disaster-list`、`disaster-jian`、`disaster-eqsc`；`on_startup` 建、`on_shutdown` 取消/等结束/关源/落状态。
- 推送是单群 + 主人私聊；图片在前、文字在后；失败不重发、不补发；首轮只建基线。
- 配置只落 `data/disaster_warning.json`；面板与机器人共读一份；令牌不回显，不读环境变量。
- 面板用 FastAPI + 现有 `authorized`/`mutate`（CSRF）；渲染状态走 `/api/disaster/render-status`。
- 测试以纯逻辑单测 + wiring 打桩为主；无真实网络/真实 QQ 发送。

## 2. 上游 → 本仓库映射

| 上游（AstrBot） | 本仓库（NoneBot2） | 契约 |
|---|---|---|
| `main.py` `DisasterWarningPlugin(Star)` | `nonebot/plugins/disaster_warning.py` | 插件元数据、命令、启动/停止 |
| `Star.initialize()` | `@get_driver().on_startup` `_startup()` | 建 `Runtime`、`prepare()`、`start()` |
| `Star.terminate()` | `@get_driver().on_shutdown` `_shutdown()` | `Runtime.stop()`，清全局 |
| `DisasterService.start/stop` | `Runtime.start/stop` | 四任务、取消、关源、落状态 |
| `asyncio.create_task(disaster_service.start())` | `Runtime.start()` 内 4 个 `create_task` | 启动不阻塞；具名便于诊断 |
| `register_background_task` + done callback | `Runtime.tasks` 列表 | 停机统一取消/`gather` |
| `filter.command` / `MessageChain` | `on_message` + `_command_re` + `MessageSegment` | 前缀 `/`/`.`/无；群/私聊均可 |
| `core/network/websocket/jian_project_*` | `disaster_china_sources.JianSource` | `lk_→rt_→at_`，WS 循环重连 |
| `core/sources/eqsc_*` / `eqsc_typhoon_poll_service` | `EqscSource` + `_eqsc_loop` | `ARh.→ATn.`，HTTP 轮询 |
| `core/sources/wolfx`（地震） | `disaster_sources.WolfxSource` | 免鉴权 HTTP，10/60 秒 |
| `core/parsers/*` | `disaster_logic` / `disaster_china_logic` | 纯函数，输入 dict/JSON |
| `core/app/runtime/disaster_service_dedup` | `disaster_state` + `disaster_tone` | 事件 key、首轮基线、首报/后续 |
| `StartupSilenceCoordinator` | 首轮基线（EEW/list/Jian all/EQSC） | V1 不做定时静默窗口 |
| `web_admin/` WebUI | `dashboard.py` + `dashboard.html` | 一块配置/渲染状态，不做七页 |
| 上游 telemetry / asyncio 异常钩子 | 不做 | V1 只用 logger + 状态命令 |
| 上游 Playwright/Pillow 模板 | `docker/playwright-renderer/` + `disaster_render` | 自建 HTTP 渲染 + B 卡，禁 JS/外网 |
| `_conf_schema.json`（67 KB） | `disaster_config.py` + JSON | 白名单 patch、令牌遮蔽、原子写 |
| 上游 40 条命令 | 8 条 | 其余另起地图 |

不复制上游整包、WebUI、模板、AstrBot 胶水；详见 AGPL NOTICE。

## 3. 事件入口与生命周期契约

### 3.1 启动

1. 模块导入时：注册 `.help` 行、编译命令正则、建 `matcher`。**不做 IO**，因为 `.help` 可能早于 `on_startup`。
2. `on_startup`：
   - `Runtime()` 读配置/状态；
   - `prepare()` 确保 `data/` 存在、重读配置/状态；
   - `start()` 建 4 个 task。
3. 若 `on_startup` 抛异常，NoneBot 启动失败；V1 不在钩子里吞异常（避免“看似启动、实际无任务”）。风险见 §9。

### 3.2 四个循环

| task | 周期 | 入口 | 异常隔离 |
|---|---|---|---|
| `disaster-eew` | 10 秒 | Wolfx CENC/JMA EEW | try/except，记日志，不退出 |
| `disaster-list` | 60 秒 | Wolfx CENC/JMA 列表 | try/except；空/半页不建基线 |
| `disaster-jian` | WS 长连，重连 5→120 秒 | Jian `weather/nmefc-tsunami` | 整段 try/except；`CancelledError` 重抛 |
| `disaster-eqsc` | 默认 120 秒（30–600） | EQSC 台风 | 整段 try/except；配置读失败也不死 |

- 每轮先 `reload_config()`；面板改配置最多等一个周期生效。
- 推送失败不抛到循环外；循环不因单次源错误退出。
- `asyncio.CancelledError` 必须重抛，保证停机。
- EQSC 配置读取异常会 `record_poll('eqsc', error=...)` 并落状态，然后按默认间隔重试。

### 3.3 停机

`Runtime.stop()`：

1. `cancel()` 所有 task；
2. `gather(return_exceptions=True)`；
3. 清空 `tasks`；
4. 仅当 `_owns_source` 时关闭 Wolfx（`_close_source` 吞异常，避免停机被源拖死）；
5. `prune` + 保存状态文件。

- `start()` 幂等：已有 tasks 直接返回。
- 注入的 `source` 由调用方负责关闭；测试验证不越权。
- `on_shutdown` 只清 `_runtime` 全局；不重建。

## 4. 命令契约

| 命令 | 权限 | 参数 | 行为 |
|---|---|---|---|
| `地震列表` | 所有人 | `cenc|jma`、1–20 | 缓存列表；空态提示 |
| `地震预警` | 所有人 | — | 最近 2 条 EEW |
| `地震源状态` | 主人 | — | Wolfx 轮询/错误/推送计数 |
| `地震测试` | 主人 | — | 样例地震文字（不推送） |
| `气象预警` | 所有人 | 1–20，默认 3 | 最近气象缓存 |
| `台风` | 所有人 | — | 活跃台风，最多 5 |
| `海啸` | 所有人 | 1–20，默认 2 | 最近海啸缓存 |
| `灾害源状态` | 主人 | — | Wolfx/Jian/EQSC 状态 |

- matcher：`on_message(rule=_rule, priority=1, block=True)`；只匹配 8 条，不拦普通聊天。
- 前缀：`/`、`.` 或无；不要求 `@`；群/私聊同一入口。
- 主人：`get_driver().config.superusers`（与 statuscard 同源）。
- 帮助：`gate_logic.register_help_rows` 的「地震」「灾害」两组，导入时注册。
- 查询命令只读缓存/内存，不触网；`地震测试` 只发样例，不写状态。

## 5. 推送契约

- 目标：`push.enabled` + `group_id` + `owner_id`（数字）；两个都发，去重后目标。
- 消息：`MessageSegment.image(png) + '\n' + text`；无图只发文字。
- 失败：任一目标成功即返回 True；失败目标只记日志，不重试、不补发；重启不补。
- 去重：分发前 `state.seen` 标记；过滤/无目标/推送失败都不回补。
- 首轮：EEW/list 首次只建 seen/基线；Jian `all` 首帧只标记；EQSC 首轮只建活跃表。
- 口吻：首报中性、后续达妮娅；图片与文字同 tone；见 UX 规格。
- 无 bot/无目标：返回 False，不抛异常；不阻塞循环。

## 6. 配置契约

- 路径：`data/disaster_warning.json`；机器人/面板共用；配置原子写 + 权限 600。
- 状态：`data/disaster_warning_state.json`；seen、initialized、baseline_at、last_poll、errors、counts。
- 默认：启用；Jian/EQSC/Wolfx 开；气象黄、台风 1200km、海啸信息、地震 4.5/1000km；本地关；推送目标空。
- 令牌：`sources.jian.token` / `sources.eqsc.token` 只在文件；`public_view()` 只回 `token_configured` / 到期日；面板留空保持原值，显式 `clear_*_token` 才清空。
- 白名单：`enabled`、`sources`、`render`、`local`、`push`、`filters`、clear token；未知字段忽略；非法值 400。
- 热加载：4 个循环每轮 `reload_config()`；命令读当前 `runtime.config`；Jian token 刷新回调立即落盘。
- 环境变量：V1 **不读** env；令牌只走 JSON/面板。需要 env 时另开决策，避免两套来源。
- 不把 token 写日志、issue、聊天；状态命令只显示是否配置。

## 7. 面板契约

| 路由 | 方法 | 鉴权 | 作用 |
|---|---|---|---|
| `/api/disaster` | GET | `authorized(req)` | 公开配置视图（无 token） |
| `/api/disaster` | POST | `mutate(req)` | 白名单 patch + 保存 |
| `/api/disaster/render-status` | GET | `authorized(req)` | 渲染服务健康：configured/ok/ready/message |

- `authorized`：管理员 session + 初始密码强制修改检查。
- `mutate`：session + `X-CSRF-Token` + Origin/Host 同源校验。
- 渲染状态只读 `health()`；渲染关闭/未配置直接返回，不抛 500。
- 面板与机器人共读同一 JSON；面板写入后循环下一轮生效。
- 面板不改令牌明文回显；不提供任意文件读写。

## 8. 测试映射

| 文件 | 覆盖 | 方式 |
|---|---|---|
| `test_disaster_config.py` | 默认/归一化/patch/token 遮蔽/权限/原子写 | 纯逻辑 + tmp |
| `test_disaster_logic.py` | CENC/JMA 解析、距离、过滤、文案、tone | 固定样例 |
| `test_disaster_sources.py` | Wolfx HTTP/错误 | MockTransport |
| `test_disaster_state.py` | seen/基线/prune/保存 | tmp |
| `test_disaster_render.py` | B 卡四类、转义、渲染客户端/健康 | HTML 断言 + mock |
| `test_disaster_store.py` | 事件身份/PK、推送记录、24h/7d、30 天清理、损坏自愈 | SQLite + tmp |
| `test_disaster_wiring.py` | matcher 优先级/block、命令解析、管理员、首轮基线、JMA 抑制、图片附件、目标/状态、**start/stop 生命周期、owned source 关闭异常、EQSC 配置异常存活** | FakeSource + FakePush + tmp |
| `test_disaster_china_logic.py` | Jian 气象/海啸、EQSC 台风、过滤/文案/tone | 固定样例 |
| `test_disaster_china_sources.py` | Jian 换票/WS/EQSC token/台风 | MockTransport/假连接 |
| `test_disaster_china_wiring.py` | `all` 基线、颜色/地区/距离、台风更新/停编、口吻、状态 | FakeSource/FakeJian |
| `test_disaster_tone.py` | 首报/后续判定、事件 id | 纯逻辑 |
| `test_disaster_panel.py` | GET/POST/CSRF/render-status | FastAPI TestClient |
| `test_help_coverage.py` | 8 命令 + 两组帮助 | 导入 + 帮助表 |
| `test_license_boundary.py` | 22 个 AGPL 头、NOTICE/LICENSE/README、MIT 文件不冒充 | 文件断言 |

- 公共引导：`tests/_bootstrap.py::ensure_nonebot()`，幂等 init/load，合并超管。
- 本轮新增 3 个生命周期用例；全量见最新 pytest（当前 **504 passed / 3 failed / 1 skipped**，18 subtests；3 failed 均为并发 Epic/游戏用例，不在 disaster；disaster 定向 **43 passed**）。
- 无真实 QQ 发送、无真实凭证、无 Docker：真实发送只在 `_push` 里，wiring 用 FakePush；live 另做验收。

## 9. 风险与未决

| 风险 | 现状 | 处理 |
|---|---|---|
| `on_startup` 异常导致启动失败 | 不吞异常 | 保留快速失败；日志/部署检查；不静默降级 |
| 推送无重试/补发 | 只发一次，任一成功即 True | V1 明确不补；验收时查目标/网络 |
| 只取 `get_bots()` 第一个 bot | 单 bot V1 | 多 bot 另开地图 |
| 事件/查询缓存只在内存 | 重启后空，列表重新基线 | 「事件身份、去重、存储与统计口径」另做 |
| 令牌过期/刷新失败 | Jian 5→120s 重连；EQSC 错误记录 | live 联调；状态命令看 errors |
| 命令读配置时效 | 循环每轮 reload | 面板改后最多等一个周期 |
| 无真实发送测试 | FakePush 打桩 | 验收群真实发送（「验收、灰度与回滚」） |
| Docker 未本机构建 | compose/健康接口已有 | 「远程 Playwright 容器与面板容器状态监视」部署验证 |
| 多源融合/静默时段 | V1 不做 | 见 UX 规格 Out of scope |
| 上游 AGPL | 20 文件头 + NOTICE | 再分发保留；见边界文档 |

## 10. 验收清单

- [x] 四任务具名、start 幂等、stop 取消/关源/落状态
- [x] 每循环异常隔离，单次源错误不退出
- [x] 8 命令、matcher priority 1/block、管理员判定、帮助行
- [x] 首轮基线、seen 去重、JMA 中间报抑制、停编
- [x] 群+主人推送、图片前文字后、失败不补
- [x] JSON 配置白名单、令牌遮蔽、面板 GET/POST/CSRF、渲染状态
- [x] 纯逻辑/wiring/panel/license 测试；无真实网络
- [ ] live Jian/EQSC（凭证）
- [ ] Docker 容器部署/验收群真实发送（「远程 Playwright 容器与面板容器状态监视」/「验收、灰度与回滚」）
- [ ] 事件存储/统计（「事件身份、去重、存储与统计口径」）
