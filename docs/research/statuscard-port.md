# 研判：astrbot_plugin_statuscard → NoneBot2 移植

研判日期：2026-10-30
上游：`SX0YYYY/astrbot_plugin_statuscard`，版本 **v2.18.8**（提交 `fa6d0f566c7b387d1f0d76c12531e03f2d36d52e`，2026-10-30）
目标：本仓库 `NoneBot2 + MuiceBot`（SnowLuma / OneBot v11）
决定：**全量移植**，出图**纯本地 Pillow**，配色走**达妮娅主题**

---

## 零、结论速览

| 问题 | 结论 |
|---|---|
| 值得移植吗？ | **值得，但要改写而不是搬运**。采集与判定逻辑质量高，框架胶水层必须重写 |
| 能直接复制多少？ | 约 **60%** 的采集/格式化/判定逻辑可复用，**40%** 的 AstrBot 胶水层必须重写 |
| 最大的技术冲突 | **出图**：上游依赖远程 T2I 服务，本项目全程本地 Pillow |
| 第二冲突 | **统计口径**：上游读 AstrBot 的 `platform_stats` / `provider_stats` / `message_stats`，本项目没有这些表 |
| 许可证风险 | 上游 **AGPL-3.0**，本仓库 MIT。移植代码必须保留 AGPL 署名与许可，不能静默并入 MIT 部分 |
| 工作量 | 全量约 **3.5–4k 行**（含测试），分四期；一期（状态卡）即可日常使用 |

---

## 一、上游是什么

一句话：把服务器的 CPU / 内存 / 磁盘 / 网络 / 进程画成一张卡发到 QQ，外加告警、服务监测、日报周报和一个 Web 看板。

能力清单（按价值排序）：

1. **状态卡出图** —— 头像、昵称、协议端版本、运行时长、好友/群/群员/收发、CPU/内存/SWAP 环形图、系统信息、磁盘占用、实时上下行与月流量、网络折线、磁盘读写、网站延迟、进程 Top 表
2. **资源与掉线告警** —— 阈值 + 连续 N 次 + 冷却 + 聚合窗口 + 恢复通知；掉线检测区分「确认离线」与「探测失败」
3. **每日定时状态图** —— 每天定点把整张卡推到群里，漏发可补发
4. **服务可用性监测** —— 网站 / 端口 / Docker 容器 / systemd 服务 / 进程五类，异常与恢复推群
5. **趋势日报 / 周报** —— 资源、异常、QQ、Token、LLM 服务质量、网站延迟、Top 进程七个可勾选模块
6. **Web 看板** —— 概览 / 历史 / 事件 / 日报四标签，CSV 导出
7. **通知中心** —— 按类型分群路由
8. **脱敏与诊断** —— 主机名/进程/网络/QQ 四类脱敏，`状态卡诊断` 排障命令

代码构成（22.8k 行，其中测试 5.7k）：

| 层 | 文件 | 行数 | 与本项目的关系 |
|---|---|---|---|
| 采集 | `collectors.py` | 2023 | **可大量复用**：psutil 与格式化几乎与框架无关 |
| 渲染 | `t2i_renderer.py` + `templates/*.jinja` | 155 + HTML/CSS | **不采用**：远程 T2I 路线，本项目改 Pillow |
| 本地降级卡 | `local_card.py` | 314 | **可作底子**：已经是 Pillow 直绘 |
| 告警 | `alerts.py` + `aggregator.py` | 1775 + 274 | 语义复用，状态机精简重写 |
| 监测 | `monitor.py` | 1106 | 五类探测逻辑直接搬 |
| 日报 | `report.py` / `report_store.py` / `sampler.py` | 1549 / 894 / 398 | 表结构与聚合口径复用 |
| 页面 | `page_api.py` + `pages/status/*` | 987 + HTML/JS | **重写**：接到本项目 dashboard |
| 胶水 | `main.py` / `delivery.py` / `onebot_receipt.py` | 1026 / 311 / 254 | **重写**：Star/事件/MessageChain → matcher/call_api |

---

## 二、AstrBot 耦合面（量化）

19 个源文件引用 `astrbot`，但耦合等级差别很大：

| 耦合形态 | 文件数 | 处理方式 |
|---|---|---|
| 仅 `from astrbot.api import logger` | 11 | 换 `nonebot.logger`，一行 |
| 读 AstrBot 数据目录 | 5 | 换成本项目 `data/` |
| 读 AstrBot 数据库（`data_v4.db` 等） | 3 | **必须换数据源**（见 §4.2） |
| `Star` / `filter.command` / `MessageChain` | 1（`main.py`） | 重写为 NoneBot matcher |
| Web API 注册（`astrbot.api.web`） | 1（`page_api.py`） | 重写为 FastAPI 路由 |

结论：**框架耦合集中在 `main.py` 与 `page_api.py` 两个文件**，其余是轻量替换。真正的重活不是「去 AstrBot 化」，而是「补上 AstrBot 免费提供而 NoneBot 没有的东西」——数据库统计、Web 配置页、消息发送回执。

---

## 三、模块映射表

| 上游 | 目标文件 | 处理 |
|---|---|---|
| `collectors.py` 的 psutil 部分 | `status_logic.py` | 抽取纯函数，去掉 `context`/`event` 参数 |
| `collectors.py` 的 OneBot 部分 | `plugins/statuscard.py` | `_get_aiocqhttp_client` → `nonebot.get_bots()` |
| `collectors.py` 的 `_apply_redaction` | `status_logic.py` | 直接复用语义 |
| `local_card.py` | `status_image.py` | 重做为达妮娅主题完整卡（环形图、折线、进程表） |
| `alerts.py` 状态机 | `status_logic.py` | 提炼为纯函数 `evaluate_rule()`，I/O 留在接线层 |
| `alerts.py` 掉线检测 | `plugins/statuscard.py` | 用 `get_status` 的 `online` 字段 |
| `monitor.py` 五类探测 | `status_monitor.py` | 直接搬，去掉 AstrBot 配置读取 |
| `report_store.py` | `status_store.py` | 同样的 SQLite 表结构，精简列 |
| `report.py` 聚合 | `status_logic.py` + `plugins/statuscard.py` | 聚合纯函数 + 采样循环 |
| `provider_quality.py` | `status_llm.py` | 换数据源：包 `Muice().model.ask` |
| `delivery.py` / `onebot_receipt.py` | `plugins/statuscard.py` | `bot.call_api("send_group_msg")` + 回执 |
| `page_api.py` / `pages/status` | `dashboard.py` / `dashboard.html` | 新增「状态」标签页 |
| `i18n.py` | 不移植 | 本项目全中文，命令提示直接写死中文 |
| `version.py` | 不移植 | 改为读本项目版本 |
| `_conf_schema.json`（32 KB） | `data/statuscard.json` | 精简配置 + 面板编辑 |

---

## 四、四个必须改口径的地方

### 4.1 出图：远程 T2I → 本地 Pillow

上游把整张 HTML 发给 `t2i.soulter.top` 渲染成图，意味着**主机名、进程名、磁盘、网络全都在第三方服务器上出现过**。本项目的取向（README「安全须知」、语料与人格不出本机）与之直接冲突。

改法：`status_image.py` 用 Pillow 直绘。代价是没有 CSS 排版那么精细，收益是零外部依赖、零信息外泄、离线可用、出图 ~100ms 而不是几秒。上游的 `local_card.py` 已经证明 Pillow 能画出可读的环形图与进程表。

### 4.2 统计口径：AstrBot 数据库 → 自建

| 指标 | 上游来源 | 本项目替代 |
|---|---|---|
| 发送数 | `data_v4.db` 的 `platform_stats` | 包 `bot.call_api`，统计成功发出的 `send_*` |
| 接收数 | `astrbot_plugin_message_stats` 的群 JSON | 插件内 `on_message` 计数 |
| Token 消耗 | `data_v4.db` 的 provider 统计 | **已有** `token_logic.TokenLedger`（天 × 模型） |
| LLM 服务质量 | `provider_stats` 表 | 包 `Muice().model.ask`：延迟、成功/失败、首字延迟 |
| 好友/群/群员 | OneBot API | 同左，无需改 |

包 `bot.call_api` 是唯一侵入点，但它只作用于 bot 实例、只累加计数、失败不影响发送；拿不到就退回 hook 计数并在诊断里标明口径。

### 4.3 配置：AstrBot 配置页 → 本项目 JSON + 面板

上游 `_conf_schema.json` 有 32 KB、上百个字段，其中大半是 AstrBot 平台适配与多机器人预留。本项目单机单号，配置精简为 `nonebot/data/statuscard.json`，默认值由代码给出，面板可改的关键项挂到「状态」标签页。

### 4.4 权限：AstrBot admin → superusers

上游所有命令仅管理员。本项目沿用 `driver.config.superusers`（`gate.py` 同款），群管理不放开——状态卡会暴露主机信息，只给主人。

---

## 五、许可证与署名

上游是 **AGPL-3.0**，本仓库是 **MIT**。两者不能静默合并：把 AGPL 代码搬进 MIT 文件会让整份文件变成 AGPL，并触发 §13 的网络服务源码提供义务。

处理方式（与 `media_parser/` 的先例一致）：

1. 移植文件头部注明来源仓库、版本、提交号、原作者、AGPL-3.0
2. 仓库内保留上游 `LICENSE`，放在 `docs/third-party/statuscard-LICENSE`
3. README 的第三方许可段补一条
4. 本项目为私有部署、不对外分发二进制，AGPL 的实际义务落在「向服务使用者提供源码」；自用场景可控，但**再分发时必须整包按 AGPL 提供**

这不是形式主义：上游 `LICENSE` 与 `ATTRIBUTION.md` 明确要求「再分发时保留上游署名与完整源代码」。

---

## 六、分期计划

| 期 | 交付 | 预估 |
|---|---|---|
| **V1 状态卡** | 采集、Pillow 出图、`查看状态`、`状态卡诊断`、采样持久化、脱敏 | 已完成主体 |
| **V2 告警** | 资源阈值、连续次数、冷却、恢复、掉线/恢复、每日定时状态图 | 已完成主体 |
| **V3 监测与报表** | 五类服务探测、每日日报、每周周报、Token 与 LLM 质量段 | 已完成主体 |
| **V4 面板** | 「状态」标签页：概览、历史趋势、事件、日报摘要 | 已完成主体 |

每期都遵循本项目惯例：`*_logic.py` 纯逻辑（无 `nonebot` / `muicebot` 导入，可秒级单测）+ `plugins/*.py` 只做接线 + `tests/` 覆盖。

---

## 七、验证方式

1. **纯逻辑单测**：格式化、阈值判定、聚合、脱敏、目标解析、时间窗口
2. **渲染冒烟**：给定固定 `view` 断言 PNG 输出非空、尺寸正确、字体缺字形不崩
3. **存储单测**：临时 SQLite 建表、写入、聚合、清理
4. **接线用例**：打桩验证 matcher/hook 真的挂上、非管理员被拒
5. **本机实跑**：`查看状态` 出图、`状态卡诊断` 输出、面板取数

---

## 八、风险清单

| 风险 | 影响 | 对策 |
|---|---|---|
| Pillow 中文字体缺失 | 卡片中文变方框 | 复用 `media_parser` 内置字体，多候选回退 |
| `bot.call_api` 包装影响发送 | 可能吞异常 | 只计数、`finally` 透传、异常时静默降级 |
| 采样表无限增长 | 磁盘 | 保留天数裁剪（资源 30 天 / 事件 90 天） |
| 定时任务与 weather 的 worker 抢时序 | 重复推送 | 复用 `claim_push` 式的「先占后再发」 |
| AGPL 被误并入 MIT | 许可冲突 | 文件头 + 独立 LICENSE + README 声明 |
| 状态卡暴露主机信息 | 隐私 | 默认勾选主机名与进程两项脱敏 |

---

## 九、与上游的差异（有意为之）

- 不提供远程 T2I 开关：**只走本地**，干净且不可能误把信息发出去
- 不做多机器人 / 多平台实例：本项目单号
- 不做通知中心分群路由：单机一个运维群足够，配置项越少越不容易配错
- 不做 Komari Webhook：与本项目 `bot_monitor` 的职责重叠，且是暴露面
- 状态卡主题固定达妮娅配色，不提供浅色切换：视觉统一优先

---

## 十、上游承认的坑（移植时保留其结论）

上游 README 里有几条踩坑结论值得原样保留：

1. **掉线与探测失败必须分开**：NapCat 容器重启时 `get_status` 必然失败，此时报「掉线」是误报
2. **磁盘百分比用 `已用/总容量`**，不用 `psutil.disk_usage().percent`（后者含 ext4 保留块，会虚高 2–3 点）
3. **网络累计量本次开机以来，月流量跨重启**：两个窗口不同，重启后「本月」可能大于「累计」，不是 bug
4. **发送不确定时不重发**：回执丢失无法区分「没发出去」和「发了但没回执」，宁可少发不重发
5. **告警冷却窗口内被抑制的故障不发恢复通知**，否则群里会出现「恢复了」但从未见过「故障」

---

## 十一、实施结果（2026-10-30 完成）

### 交付文件

| 文件 | 行数 | 职责 |
|---|---:|---|
| `nonebot/status_logic.py` | 1119 | 格式化、视图装配、脱敏、资源/掉线状态机、告警聚合、报表聚合、时间与目标解析 |
| `nonebot/status_collect.py` | 508 | psutil 采集：CPU/内存/交换/磁盘/磁盘 IO/网络/进程/系统信息，面板快照 |
| `nonebot/status_store.py` | 537 | JSON 状态（原子写、600 权限）+ SQLite 采样库（损坏自愈、按天裁剪） |
| `nonebot/status_image.py` | 788 | 达妮娅主题 Pillow 渲染：状态卡 + 通用报表版式 |
| `nonebot/status_monitor.py` | 413 | 五类服务探测（HTTP/端口/Docker/systemd/进程）+ 判定状态机 |
| `nonebot/status_llm.py` | 256 | 包 `Muice().model.ask` 记延迟、成功率、首字延迟、连续失败 |
| `nonebot/status_config.py` | 194 | 机器人进程与面板进程共用的配置读写 + 白名单 |
| `nonebot/plugins/statuscard.py` | 1370 | 接线：6 个命令、4 个后台任务、推送、报表、定时 |
| `nonebot/tests/test_status_*.py` | 1540 | 7 个测试文件，100 个用例 |
| `nonebot/dashboard.py` / `dashboard.html` | +约 260 | 「服务器状态」页：实时资源、历史趋势、事件、推送与报表、脱敏开关 |

### 验证情况

| 项目 | 结果 |
|---|---|
| 新增用例 | **100 个全绿**（逻辑 31 / 存储 15 / 渲染 10 / 监测 12 / 接线 18 / 面板 5 / 集成 9） |
| 集成链路 | 告警触发→事件落库→恢复、掉线（探测失败不误报）、LLM 连续失败、报表出图与回退、定时器只发一次、面板登录+CSRF 全流程 |
| 既有面板用例 | 8 个全绿，未被新路由破坏 |
| 真实硬件出图 | 用本机 psutil 实采出图，环形图、磁盘条、双线折线、进程表、系统信息均正确 |
| 面板接口 | 空库、有数据、范围越界、白名单保存四种情形均通过 |
| 前端脚本 | `node --check` 语法通过 |
| 启动/关闭 | 启动钩子建 4 个任务与采样库，关闭钩子干净取消 |

### 与计划的偏差（都是实施后认为更好的选择）

1. **多拆了一个 `status_config.py`**：面板进程不能 import 插件模块（会带起 nonebot 初始化），配置读写必须独立。
2. **进程扫描加缓存**：Windows 上 596 个进程逐句柄查询要 8–13 秒（Linux 0.1 秒级）。采样循环负责刷新，卡片/面板命中 30 秒缓存；否则「查看状态」在开发机上会卡十几秒。
3. **实现了 `suppress_when_down`**：原计划按上游「重复层抑制」保留字段即可，但「配了不生效」的字段比没有更糟，改成两遍扫描（先探测全部、再判定）。
4. **进程 CPU 改整机口径**：上游同时提供单核与整机两个字段，卡片直接显示单核口径会出现 743%，这里统一显示整机口径。
5. **告警首条不再被冷却挡住**：`last_sent=0`（从没发过）曾被当成「刚发过」，新装机器永远发不出第一条。

### 有意没做的（与「与上游的差异」一致）

远程 T2I、多平台/多机器人、通知中心分群路由、Komari Webhook、i18n 双语、看板 CSV 导出、「立即验证」按钮、掉线恢复全群广播。

### 集成测试抓到、单元测试抓不到的两个缺陷

1. **默认值被浅拷贝共享**：`status_config.merge` 与 `status_store._deep_merge` 都用 `dict(base)`，改运行时配置/状态的嵌套 dict 等于改模块级默认值，同进程后续创建的实例全部继承脏数据。已改为 `copy.deepcopy`，并各补一条回归用例。
2. **同一秒内的发送记录排序不稳**：出图失败会立刻写一条「文字」记录，`ORDER BY ts DESC` 在秒级相同的情况下会随机返回旧记录，面板显示「已发送图片」而实际发的是文字。改为 `ORDER BY ts DESC, id DESC`。

### 已知限制

- 面板只能改白名单内的配置项；监测条目等复杂结构仍需手改 `data/statuscard.json`。
- 日报/周报出图失败只回退文字，不做上游那种「隔 10 分钟重试两次」。
- 打包与头像缓存目录不清理，靠 24 小时 TTL 覆盖。
