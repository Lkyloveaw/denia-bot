# 上游 AstrBot 插件 astrbot_plugin_endfield 能力盘点

调研日期：2026-10-31。范围：上游仓库 `Entropy-Increase-Team/astrbot_plugin_endfield`，本地浅克隆于 `C:\Users\16907\AppData\Local\Temp\astrbot_endfield`，commit `41482b7`（`git log -1`）、`metadata.yaml` 声明版本 3.0.3，许可证 AGPL-3.0（根 `LICENSE`）。

本文只做事实盘点和含义说明，不代表已部署或实测；凡标「推断」的都是我根据代码结构给出的判断，不是上游明示。所有结论尽量给到 `文件:行号`。上游仓库未改动。

一处必须先纠正的前提：任务提到的 `data/users.json` **在当前代码中不存在**。持久化实际由 7 个 JSON 文件承担，绑定数据在 `bindings.json`（`core/user.py:44`）；`README.md:382` 仍写「删除插件 `data/users.json` 中对应的绑定记录」，属于文档滞后。同理，「33 个命令」按源码数只有 31 个注册点，差异见第 1 节。

## 1. 结论速览

- **本质是一个第三方 API 网关的客户端**：全部业务数据经 `https://end-api.shallow.ink` 转发（`core/client.py:5`），插件本身不直连森空岛或游戏服务端。API Key 需到 `https://end.shallow.ink` 申请（`_conf_schema.json:5`）。
- **代码体量**：`main.py` 6122 行、`core/client.py` 659 行、`core/render.py` 311 行、`core/user.py` 330 行、`core/utils.py` 43 行；资源 12 MB（其中字体 `resources/font/HarmonyOS_SansSC_Bold.ttf` 约 8 MB）。
- **命令注册点 31 个**：30 个 `@filter.command` + 1 个 `@filter.regex`（`grep -c` 计数）。任务与 README 里的「33」是**按用法/别名展开的可见条目**：README 表格 7（绑定）+ 17（查询）+ 8（公告订阅）= 32 行，再加正文提示的 `zmd`（`README.md:163`）即 33。
- **输出形态**：17 处 `event.image_result`、132 处 `event.plain_result`、1 处 `event.chain_result`；绝大多数图卡命令都带文本回退，但**登录类、订阅类、签到、同步面板、切换/删除绑定**本身只出文本。
- **最重的隐形成本在渲染**：Playwright + Chromium + 16 个在用 HTML 模板（共 18 个，Yunzai art-template 与 Jinja2 混写）+ 12 MB 资源，是移植时无法「只搬 Python」的部分。
- **可复用与不可复用界线清晰**：`core/` 四个模块几乎与 AstrBot 无关（只有 logger 与一处类型注解），而 `main.py` 的 31 个 handler、4 个后台任务、消息收发全部绑死框架。
- **账号凭据是明文落盘的**：`framework_token` 直接写进 `bindings.json`（`main.py:1764-1779`），且该 token 是访问游戏数据的唯一凭据，没有刷新机制，失效即删绑定（`core/client.py:116-132`）。

## 2. 能力与命令表

### 2.1 账号与绑定（7）

| # | 命令 | 别名/参数 | 依赖端点 | 输出 |
|---|------|-----------|----------|------|
| 1 | `授权登陆` | — | `POST /api/v1/authorization/requests`、`GET .../requests/{id}/status`、`POST /api/v1/bindings`（`main.py:1663,1692,1757`） | 文本（授权链接 + 轮询结果）；链接消息 100 s 后撤回（`main.py:1685`） |
| 2 | `扫码绑定` | — | `GET /login/endfield/qr`、`GET /login/endfield/qr/status`、`POST /login/endfield/qr/confirm`、`POST /api/v1/bindings`（`main.py:1801,1852,1857,1909`） | 二维码图片 + 文本（`main.py:1815-1838`） |
| 3 | `手机绑定` | `<手机号>` | `POST /login/endfield/phone/send`、`POST /login/endfield/phone/verify`（`main.py:1956,1974`） | 纯文本；仅私聊（`main.py:1952`）；帮助菜单自标「暂不可用」（`main.py:1393`） |
| 4 | `国际服登录` | — | `POST /login/skport/password`（`main.py:2111`） | 纯文本两段式问答；仅私聊（`main.py:2056`） |
| 5 | `绑定列表` | — | 无网络请求（读本地） | 图片 `enduid/bind-list.html`，失败回退文本（`main.py:1548-1561`） |
| 6 | `切换绑定` | `<序号>` | 无（只改本地 `is_primary`，`main.py:1574-1577`） | 文本 + 追加一次绑定列表 |
| 7 | `删除绑定` | `<序号>` | `DELETE /api/v1/bindings/{id}`（`main.py:1599`） | 文本 + 追加绑定列表 |

### 2.2 数据查询（15 个注册点 / README 17 条用法）

| # | 命令 | 别名/参数 | 依赖端点 | 输出 |
|---|------|-----------|----------|------|
| 8 | `理智` | — | `GET /api/endfield/stamina`、`GET /api/endfield/note`（`main.py:2227,2232`） | 图片 `stamina/stamina.html` + 文本回退（`main.py:2311,2319`） |
| 9 | `便签` | — | `GET /api/endfield/note`、`/stamina`（`main.py:2338,2343`） | 图片 `note/note.html` + 文本回退（`main.py:2571,2578`） |
| 10 | `签到` | — | `POST /api/endfield/attendance`（逐账号，`main.py:2596`） | 纯文本汇总（`main.py:2600-2615`） |
| 11 | `危机合约` | `危机`、`合约`；可带名称/ID，或 `指标`/`词条` | `GET /api/endfield/card/detail`、`GET /api/endfield/card/crisis-contract`（`main.py:2705,2718`） | 图片 `crisis-contract/crisis-contract.html` + `crisis_format_text` 回退（`main.py:2745,2754`） |
| 12 | `成就列表` | `成就查询`、`成就`、`成就一览` | `GET /api/endfield/note`、`GET /api/endfield/achieve`（`main.py:2772,2773`） | 图片 `achievement/achievement.html`，失败列前 30 条文本（`main.py:2867-2877`） |
| 13 | `干员列表` | — | `GET /api/endfield/card/detail`、`GET /api/panel/chars`、`GET /api/friend/detail`（`main.py:2891,2907,2908`） | 图片 `operator/list.html` + 文本回退（`main.py:3075,3081`） |
| 14 | `<干员名>面板` | 正则 `^\*?(?:终末地)?\s*(.+?)\s*(?:终末地)?面板$`（`main.py:3088`） | `search/chars`、`note`、`card/char`、`panel/char`、`friend/detail`、`friend/char`（`main.py:3123,3133,3155,3198,3185,3244`） | 图片 `operator/operator.html`；失败仅提示「图片渲染失败」（`main.py:3268-3275`） |
| 15 | `同步面板` | — | `POST /api/panel/sync`、`GET /api/panel/sync/status`（轮询 90 s，`main.py:3290,3316`） | 纯文本进度/结果 |
| 16 | `抽卡记录` | `<页码>` 默认 1 | `GET /api/endfield/gacha/stats`、`/gacha/records`（`main.py:3379,3391`） | 图片 `gacha/gacha-record.html` + 文本回退（`main.py:3431,3440`） |
| 17 | `抽卡分析同步` | — | `POST /api/endfield/gacha/fetch`、`GET /gacha/sync/status`（轮询 60×2 s，`main.py:3461,3472`） | 纯文本进度，成功后转调 `抽卡分析`（`main.py:3483`） |
| 18 | `抽卡分析` | — | `gacha/stats`、`note`、`gacha/records`、`gacha/pool-chars`、`wiki/activities`、`gacha/global-stats`、`wiki/items`（`main.py:3506,3520,3570,3769,3807,3860,5552`） | 图片 `gacha/gacha-analysis.html` + 文本回退（`main.py:3668-3681`） |
| 19 | `日历` | — | `GET /api/wiki/activities`（`main.py:4283`）、`GET /api/wiki/items/{id}` 取横幅（`main.py:1088`） | 图片 `calendar/calendar.html`；失败仅提示（`main.py:4498-4502`） |
| 20 | `帝江号建设` | `帝江号` | `GET /api/endfield/spaceship`、`/note`（`main.py:4861,4864`） | 图片 `area/spaceship.html`；失败仅提示（`main.py:4952-4956`） |
| 21 | `地区建设` | `建设` | `GET /api/endfield/domain`、`/note`（`main.py:4967,4970`） | 图片 `area/area.html`；失败仅提示（`main.py:5080-5084`） |
| 22 | `全服统计` | 可带干员名、`国际服` | `GET /api/endfield/gacha/global-stats`（`main.py:5610,5634`） | 图片 `gacha/global-stats.html` + 文本回退（`main.py:5981-6006`） |
| — | `zmd` | — | 无 | 图片 `help/help.html` + 文本菜单回退（`main.py:1495,1503`） |

### 2.3 公告与订阅（8）

| # | 命令 | 参数 | 依赖端点 | 输出 |
|---|------|------|----------|------|
| 23 | `公告` | 可 `公告 <序号>` 看详情 | `GET /api/announcements`、`GET /api/announcements/{id}`（`main.py:4186,4151,4167`） | 列表或详情图片（`main.py:4212,4172`）；正文含多余后缀时静默返回（`main.py:4182`） |
| 24 | `公告最新` | — | `GET /api/announcements/latest`（`main.py:4221`） | 详情图片（`main.py:4227`） |
| 25 | `订阅公告` | 仅群聊 | `GET /api/announcements/latest` 取当前时间戳（`main.py:4242`） | 文本 |
| 26 | `取消订阅公告` | 仅群聊 | 无 | 文本 |
| 27 | `订阅理智` | 需已绑定 | 无（写订阅） | 文本 |
| 28 | `取消订阅理智` | — | 无 | 文本 |
| 29 | `订阅调度券` | 需已绑定 | 无 | 文本 |
| 30 | `取消订阅调度券` | — | 无 | 文本 |

另有 `@filter.command("zmd")`（`main.py:1363`）为第 31 个注册点。合计 30 command + 1 regex = 31。

### 2.4 命令层的实现特征（事实）

- **一律 generator 写法和 `yield event.*_result`**，不是 return（全文 132 处 `plain_result`、17 处 `image_result`）。
- **多账号语义**：查询类命令取 `get_primary_binding`（`core/user.py:51-57`），签到与自动签到遍历全部绑定（`main.py:2585,4729`），切换/删除按列表序号（`main.py:1568,1587`）。
- **面板正则有两道守卫**：命中保留字（同步/绑定/理智…）直接 `return`（`main.py:3101-3112`）；名字不在 24 h 干员名缓存里也直接 `return`（`main.py:3123-3130`），因此非干员名 + 面板 不会触发联网。
- **`抽卡记录` 的注释已自承是简化实现**：「Basic text result for now, graphical rendering is complex」（`main.py:3384`），图上只有「最近记录」一段（`main.py:3410-3428`）。

## 3. API 端点清单（`core/client.py`）

公共约定（`core/client.py:36-49`）：所有请求都带 `Content-Type: application/json`、`platform: bot`；配置了 api_key 则带 `X-API-Key`；`bot_qq`/`user_qq` 非空时分别带 `client_id`/`platform_id`；需要用户身份的数据接口额外带 `X-Framework-Token`。`httpx.AsyncClient(timeout=25.0)` 全局复用一个连接池（`core/client.py:26`）。

### 3.1 表：method + path + 用途 + 鉴权

| 分区 | 方法 | 路径 | 用途 | X-Framework-Token | 行号 |
|------|------|------|------|:---:|------|
| 登录 | GET | `/login/endfield/qr` | 取扫码二维码（base64）+ 临时 token | 否 | 166-168 |
| 登录 | GET | `/login/endfield/qr/status` | 查扫码状态（`?framework_token=`） | 否 | 170-174 |
| 登录 | POST | `/login/endfield/qr/confirm` | 扫码确认换长期 token | 否（body 带 token） | 176-187 |
| 登录 | POST | `/login/endfield/phone/send` | 发短信验证码 | 否 | 189-192 |
| 登录 | POST | `/login/endfield/phone/verify` | 验证码换 token | 否 | 194-198 |
| 登录 | POST | `/login/skport/password` | 国际服邮箱密码登录 | 否 | 200-204 |
| 授权 | POST | `/api/v1/authorization/requests` | 创建网页授权请求 | 否 | 207-228 |
| 授权 | GET | `/api/v1/authorization/requests/{id}/status` | 轮询授权状态 | 否 | 230-232 |
| 绑定 | POST | `/api/v1/bindings` | 创建绑定（body 含 token/角色/`client_id=bot-<uid>`） | 否（token 在 body） | 235-253 |
| 绑定 | GET | `/api/v1/bindings` | 列绑定（`user_identifier`/`client_type`/`provider`） | 否 | 255-267 |
| 绑定 | DELETE | `/api/v1/bindings/{id}` | 删绑定 | 否 | 269-275 |
| 绑定 | POST | `/api/v1/bindings/{id}/primary` | 设主绑定 | 否 | 277-280 |
| 游戏 | GET | `/api/endfield/stamina` | 理智/活跃度 | 是 | 283-291 |
| 游戏 | GET | `/api/endfield/note` | 便签（角色、干员、成就概览） | 是 | 293-301 |
| 游戏 | GET | `/api/endfield/card/detail` | 角色卡片总览（含合约列表、干员） | 是 | 303-311 |
| 游戏 | GET | `/api/endfield/card/crisis-contract` | 危机合约详情 | 是 | 313-331 |
| 游戏 | GET | `/api/endfield/card/char` | 单干员详情（`instId` 优先） | 是 | 333-357 |
| 游戏 | POST | `/api/endfield/attendance` | 签到（参数走 query，不是 body） | 是 | 359-376 |
| 游戏 | GET | `/api/endfield/spaceship` | 帝江号建设 | 是 | 378-385 |
| 游戏 | GET | `/api/endfield/domain` | 地区建设/调度券 | 是 | 387-394 |
| 游戏 | GET | `/api/endfield/achieve` | 成就徽章 | 是 | 396-403 |
| 游戏 | GET | `/api/endfield/search/chars` | 全干员名（用于名称守卫） | 是 | 405-411 |
| 好友 | GET | `/api/friend/detail` | 好友展示位（`role_id` 可省，靠 token 反解） | 可选 | 414-423 |
| 好友 | GET | `/api/friend/char` | 好友展示干员面板兜底 | 是 | 425-440 |
| 抽卡 | GET | `/api/endfield/gacha/accounts` | 抽卡账号列表 | 是 | 443-447 |
| 抽卡 | POST | `/api/endfield/gacha/fetch` | 触发同步 | 是 | 449-458 |
| 抽卡 | GET | `/api/endfield/gacha/sync/status` | 同步状态 | 是 | 460-464 |
| 抽卡 | GET | `/api/endfield/gacha/records` | 分页记录（`pools/page/limit`） | 是 | 466-475 |
| 抽卡 | GET | `/api/endfield/gacha/stats` | 个人统计 | 是 | 497-501 |
| 抽卡 | GET | `/api/endfield/gacha/global-stats` | 全服统计（`pool_period/provider`） | 否 | 503-514 |
| 抽卡 | GET | `/api/endfield/gacha/pool-chars` | 卡池角色/UP | 否 | 516-523 |
| 抽卡 | POST | `/api/endfield/gacha/simulate/single` | 模拟单抽 | 否 | 525-532 |
| 抽卡 | POST | `/api/endfield/gacha/simulate/ten` | 模拟十连 | 否 | 534-541 |
| Wiki | GET | `/api/wiki/search` | 维基搜索 | 否 | 544-551 |
| Wiki | GET | `/api/wiki/items` | 维基条目列表 | 否 | 553-555 |
| Wiki | GET | `/api/wiki/items/{id}` | 条目详情 | 否 | 557-559 |
| Wiki | GET | `/api/wiki/activities` | 活动列表（日历数据源） | 否 | 561-563 |
| 公告 | GET | `/api/announcements` | 公告列表（`page/page_size`） | 否 | 566-572 |
| 公告 | GET | `/api/announcements/latest` | 最新一条 | 否 | 574-576 |
| 公告 | GET | `/api/announcements/{id}` | 公告详情 | 否 | 578-580 |
| MaaEnd | POST | `/api/maaend/devices/bind-code` | 设备绑定码 | 否 | 583-585 |
| MaaEnd | GET | `/api/maaend/devices` | 设备列表 | 否 | 587-589 |
| MaaEnd | GET | `/api/maaend/devices/{id}/tasks` | 任务列表 | 否 | 591-593 |
| MaaEnd | POST | `/api/maaend/devices/{id}/tasks` | 下发任务 | 否 | 595-597 |
| MaaEnd | GET | `/api/maaend/jobs/{id}` | 任务状态 | 否 | 599-601 |
| MaaEnd | POST | `/api/maaend/jobs/{id}/stop` | 停止任务 | 否 | 614-616 |
| MaaEnd | GET | `/api/maaend/devices/{id}/screenshot` | 截屏（返回 bytes，不经 `_request`） | 否 | 618-628 |
| 面板 | POST | `/api/enduid/set-primary` | 设主角色（游戏侧） | 是 | 603-612 |
| 面板 | POST | `/api/panel/sync` | 触发面板同步 | 是 | 631-633 |
| 面板 | GET | `/api/panel/sync/status` | 同步状态 | 是 | 635-639 |
| 面板 | GET | `/api/panel/chars` | 已同步干员（`page_size` 上限 50） | 是 | 641-651 |
| 面板 | GET | `/api/panel/char/{template_id}` | 单干员战斗面板 | 是 | 653-659 |

共 52 个端点方法。**其中 14 个在 `main.py` 中从未被调用**：`gacha/accounts`、`gacha/simulate/single`、`gacha/simulate/ten`、`wiki/search`、`wiki/items/{id}`（该路径在 `main.py:1088` 用裸 `_get` 直连，绕过了封装方法）、`bindings/{id}/primary`、`enduid/set-primary`、MaaEnd 全部 7 个。`MaaendManager` 也在 `main.py:954` 实例化后从未使用，`resources/maaend/tasks.html`、`resources/gacha/simulate-result.html` 因此是孤儿模板——与 `README.md:221-223` 的 TODO 状态一致（模拟抽卡、MaaEnd 未完成）。

### 3.2 Header 语义（事实 + 推断）

| 名称 | 赋值位置 | 语义 |
|------|----------|------|
| `X-API-Key` | `core/client.py:45-46`，值来自配置 `api_key` | 网关的接入密钥，区分调用方；缺失时部分接口不可用（`core/utils.py:14`、`_conf_schema.json:2-8`） |
| `X-Framework-Token` | `core/client.py:47-48`，值来自当前绑定 | 用户级凭据，代表「某个森空岛账号的登录会话」；数据接口按它鉴权，绑定接口则把 token 放在 body（`core/client.py:245`） |
| `client_id` | `core/client.py:41-42`，值 `bot_qq` | 「发起请求的机器人 QQ」，由 `set_caller` 注入（`main.py:1653,2062`） |
| `platform_id` | `core/client.py:43-44`，值 `user_qq` | 「当前交互的用户 QQ」 |
| `platform` | `core/client.py:39`，硬编码 `"bot"` | 标明客户端类型为机器人；与 `create_binding` body 的 `client_type: "bot"`、`client_id: f"bot-{user_id}"`（`core/client.py:246-248`）呼应 |

**推断（且有代码证据）**：`EndfieldClient` 是单例共享（`main.py:938-942`），而 `bot_qq`/`user_qq` 是可变字段，只在 `授权登陆` 与 `国际服登录` 两处 `set_caller`（全文仅 2 处）。因此**其它命令发出的请求会携带上一次授权操作者的 `client_id`/`platform_id`**，并非当前提问者。对移植的含义：这套 header 只是松散的上下文提示，不是可靠的身份边界。

### 3.3 错误处理与失效清理

- **业务错误**：HTTP 200 但 `code != 0` → 打 warning 并返回 `None`（`core/client.py:99-105`），调用方一律按「失败/无数据」处理，**不重试**。
- **HTTP 错误**：`raise_for_status()` 后捕获 `HTTPStatusError`，打印状态码与响应中的 `message/msg`（`core/client.py:106-113`）。
- **403 失效识别**：状态码必须为 403，且响应文本命中 6 个标记之一（`无效的 framework token` / `framework token 无效` / `invalid framework token` / `会话已过期` / `会话过期` / `session expired` / `session has expired`）（`core/client.py:147-163`）。
- **一次性清理**：命中后把 token 记入 `_invalid_framework_tokens`（`core/client.py:119-121`），后续同一 token 直接返回 `None`，不再请求；再回调 `on_auth_invalid`（`core/client.py:125-131`）。
- **回调做什么**：遍历本地绑定 → 尽力调 `DELETE /api/v1/bindings/{id}` 删服务端记录 → 再按 token 删本地所有用户下的绑定（`main.py:969-1000`、`core/user.py:101-117`），并在日志里报「请重新授权登录」。
- **网络/解析异常**：`httpx.RequestError`、`ValueError`（JSON 解析）、其它异常全部吞掉返回 `None`（`core/client.py:137-145`）。**唯一的重试**在外链图片下载：3 次、间隔 0.5 s、超时 10 s（`main.py:1166-1179`）。
- 没有 token 刷新、没有过期时间判断、没有 429 限流退避。

## 4. 登录方式与 token 生命周期

四条路径最终都收敛到同一件事：拿到 `framework_token` → `POST /api/v1/bindings` → 写本地 `bindings.json`。

### 4.1 授权登陆（网页授权，`main.py:1650-1795`）

1. 要求配置 `api_key`（`main.py:1656`），`set_caller` 注入 bot/用户 QQ（`main.py:1653`）。
2. `POST /api/v1/authorization/requests`，body 带 `client_id=<用户QQ>`、`client_name`（配置项，默认「终末地机器人」）、`client_type=bot`、`scopes=[user_info, binding_info, game_data, attendance]`（`core/client.py:207-225`）。
3. 返回的 `auth_url` 若是相对路径则拼上 `https://end.shallow.ink`（`core/client.py:226-227`）。
4. 发链接消息并记录 `message_id`，**100 s 后自动撤回**（`main.py:1676-1685`），以规避 QQ 2 分钟撤回窗口（`main.py:1682` 注释）。
5. 每 2 s 轮询一次，最长 95 s（`main.py:1690-1691`）。状态 `used`/`approved` 且带 token → 立即撤回链接并结束；`rejected` → 提示「授权已被拒绝」；`expired` → 提示「授权链接已过期」（`main.py:1696-1731`）。
6. 超时 → 「授权超时，请重新发起授权」（`main.py:1733-1736`）。
7. 用状态里的 `available_roles` 选 `is_default` 或第一个角色，把 `role_id/server_id/nickname/skland_uid/channel_name/server_name/level` 作为 `**kwargs` 传给 `create_binding`（`main.py:1742-1757`）。
8. 成功则写入绑定（`framework_token`、`binding_id`、`role_id`、`nickname`、`server_id`、`channel_name`、`server_name`、`is_active=true`、`is_primary=true`、`login_type="auth"`、`bind_time`、`last_sync`，`main.py:1764-1779`），并以 `existing.append` 方式追加后整体保存（`main.py:1781-1783`）。

### 4.2 扫码绑定（`main.py:1797-1947`）

1. `GET /login/endfield/qr` 拿 `{qrcode: data:image/png;base64,..., framework_token}`（`main.py:1801-1807`）。
2. 把 base64 落临时文件（供回退时 `Image.fromFileSystem`），优先走协议端发 `base64://` 图文（`main.py:1809-1838`）。
3. 同样 100 s 定时撤回（`main.py:1843`），每 2 s 轮询 `qr/status`，最长 95 s（`main.py:1850-1851`）。
4. `status == "done"` → `POST /qr/confirm`（body 含 `user_identifier`、`platform=bot`）换正式 token（`main.py:1856-1857`、`core/client.py:176-187`）；`expired`/`failed` → 提示并结束（`main.py:1871-1880`）。
5. 之后与授权路径相同：选角色 → `create_binding` → 追加写入，`login_type="qr"`（`main.py:1894-1935`）。
6. `finally` 里删除临时二维码文件（`main.py:1886-1888`）。

### 4.3 手机绑定（`main.py:1949-2051`）

- 群聊直接拒绝，要求私聊（`main.py:1952-1954`）。
- `POST /phone/send` 发码，提示里对手机号做掩码 `phone[:3]****phone[7:]`（`main.py:1961-1962`）。
- 用 AstrBot 的 `session_waiter(timeout=300)` 等 6 位数字（`main.py:1966-1972`）；超时 300 s 抛 `TimeoutError` → 「验证超时」（`main.py:2048-2051`）。
- 验证成功 → `create_binding` → 写入，`login_type="phone"`、`last_sync=0`（`main.py:1999-2027`）。
- 注意：代码路径完整，但**帮助菜单把它标成「暂不可用」**（`main.py:1393`），README 同样标注（`README.md:122`）。

### 4.4 国际服登录（`main.py:2053-2210`）

- 仅私聊（`main.py:2056-2060`）。
- 两段 `session_waiter`，**各 120 s**：先邮箱（可输入 `取消` 退出），再密码（`main.py:2073-2106`）。
- 密码明文发给机器人，插件**明确声明无法代为撤回**，反复要求用户自行撤回密码消息（`main.py:2085,2116,2202`）。
- `POST /login/skport/password` → 失败提示「目前插件暂不支持在此阶段处理极验证码」（`main.py:2111-2120`）；成功但无角色则提示无角色（`main.py:2125-2132`）。
- `create_binding(..., provider="skport")`（`main.py:2139-2149`），写入前把既有账号全部置为非主（`main.py:2184-2189`），`login_type="skport"`、`channel_name` 兜底 `"SKPORT"`。

### 4.5 绑定列表 / 切换 / 删除

- 列表读本地，无网络（`main.py:1516`）；`bind_time` 以毫秒时间戳除 1000 转本地时间显示（`main.py:1532-1536`）。
- 切换只改本地 `is_primary`，序号从 1 开始，越界给提示（`main.py:1568-1578`），保存时由 `save_user_bindings` 再规范化一次。
- 删除先调服务端（失败不阻断），再删本地，提示「后端同步可能稍有延迟」（`main.py:1599-1606`）。

### 4.6 token 生命周期小结

| 阶段 | 事实 | 来源 |
|------|------|------|
| 获取 | 四条路径，全部产出 `framework_token` | `main.py:1739,1891,1982,2122` |
| 存储 | 明文写入 `bindings.json`，无加密、无指纹 | `main.py:1765`、`core/user.py:44` |
| 使用 | 每次数据请求放 `X-Framework-Token` | `core/client.py:47-48`、`290` 等 |
| 续期 | **不存在**；没有 refresh 端点，也不存过期时间 | 全文无 refresh 调用 |
| 失效 | 403 + 文本标记 → 一次性标记 + 删服务端/本地绑定 | `core/client.py:116-132,147-163`、`main.py:969-1000` |
| 多账号 | 同一 QQ 下多绑定，按 `role_id` 去重，只有一个 primary | `core/user.py:59-90` |

## 5. 数据持久化

### 5.1 位置与文件

数据目录来自 `StarTools.get_data_dir()`（`main.py:944-947`），即 AstrBot 为插件分配的目录，插件内部再按文件名区分 7 份 JSON：

| 文件 | 结构（顶层） | 管理者 | 用途 | 行号 |
|------|--------------|--------|------|------|
| `bindings.json` | `{ "<QQ>": [binding, ...] }` | `UserManager` | 账号绑定 | `core/user.py:42-127` |
| `simulate_state.json` | `{ "<scope>": { "<pool_type>": {gacha_history, pity} } }` | `SimulateManager` | 模拟抽卡状态（**当前无调用方**） | `core/user.py:130-148` |
| `announcements.json` | `{subscriptions: [{group_id, since_ts, msg_origin}], last_ids: []}` | `AnnouncementManager` | 公告订阅 | `core/user.py:151-189` |
| `sanity_subscriptions.json` | `{subscriptions: [{user_id, msg_origin, last_notified}]}` | `SanityManager` | 理智订阅 | `core/user.py:192-235` |
| `maaend.json` | `{users: {devices, default_device}, groups: {}}` | `MaaendManager` | MaaEnd 设备（**当前无调用方**） | `core/user.py:238-271` |
| `ticket_subscriptions.json` | `{subscriptions: [{user_id, msg_origin, last_notified}]}` | `TicketManager` | 调度券订阅 | `core/user.py:274-316` |
| `sign_state.json` | `{last_sign_date: "YYYY-MM-DD"}` | `SignManager` | 自动签到日期 | `core/user.py:319-330` |

写入策略：`asyncio.Lock` 串行化 + 先写 `*.tmp` 再 `os.replace` 原子替换（`core/user.py:28-35`）；读取失败只记日志并回落到默认结构（`core/user.py:19-26`）；对外一律 `deepcopy`（`core/user.py:37-39,46-49`）。

### 5.2 绑定对象字段（事实，来自写入点）

| 字段 | 含义 | 来源 |
|------|------|------|
| `framework_token` | 用户级凭据（明文） | `main.py:1765` |
| `binding_id` | 服务端绑定 ID，删除时用 | `main.py:1766` |
| `role_id` | 游戏角色 ID，所有数据接口的主键 | `main.py:1767` |
| `nickname` | 角色昵称（展示用） | `main.py:1768` |
| `server_id` | 服务器编号，默认 1 | `main.py:1769` |
| `channel_name` / `server_name` | 渠道名（官服/B服/SKPORT）与地区名 | `main.py:1770-1773` |
| `is_active` | **只写不读**（4 处写入，无读取） | `main.py:1774,1928,2020,2177` |
| `is_primary` | 当前使用账号；保存时强制唯一 | `main.py:1775`、`core/user.py:78-87` |
| `login_type` | `auth` / `qr` / `phone` / `skport` | `main.py:1776,1927,2019,2176` |
| `bind_time` / `last_sync` | 毫秒时间戳；`last_sync` 参与排序 | `main.py:1777-1778`、`core/user.py:66-70` |
| `skland_uid` / `level` | 只在创建绑定时作为参数上送，**未落盘** | `main.py:1751,1754` |
| `avatarUrl` | 读取时可能存在的可选字段 | `main.py:2260` |

### 5.3 多账号 / 多角色模型

- 键是 `str(sender_id)`（QQ 号），值是绑定列表（`core/user.py:46-49`）——**身份粒度只有「平台发送者 ID」**，没有 platform 前缀，也没有群维度；同一 QQ 在任意群/私聊共享同一套绑定。
- 保存时按 `(is_primary, last_sync)` 降序排序，再按 `role_id` 去重，最后保证恰好一个 primary（`core/user.py:66-88`）。因此重复绑定同一角色会被合并，但**不同角色可以并存**。
- 查询接口只认 primary，其余账号仅在 `绑定列表`/`签到` 中出现。
- 全局遍历用 `get_all_bindings()`，会给每个绑定附加 `_user_id` 供自动签到与推送使用（`core/user.py:119-127`）。

### 5.4 缓存目录

| 目录 | 内容 | 清理 |
|------|------|------|
| `resources/cache/` | 外链图片按 URL 的 MD5 + 扩展名缓存（`main.py:1118-1160`） | 不自动清理 |
| `resources/render_cache/` | 渲染产物 `render_<8hex>.png`（`core/render.py:33,236`） | 后台任务每 60 s 删除超过 300 s 的文件（`core/render.py:41-64`） |
| 插件目录下 `tmp_<8hex>.html` | 截图用的临时 HTML，写完即删（`core/render.py:255-260,293-295`） | `finally` 中删除 |

`.gitignore` 同时忽略 `data/`、`render_cache/`、`resources/cache/`（`.gitignore:3-5`）。

## 6. 订阅、自动签到与公告推送

四个后台任务在 `initialize()` 中启动（`main.py:1343-1361`）：公告、理智、调度券三个无条件启动，自动签到受 `auto_sign_in` 开关控制；自动签到还额外做一次「启动补签」——若当天日期与 `sign_state.json` 不符，立即跑一次（`main.py:1354-1361`）。`terminate()` 逐个 cancel 并关闭 httpx/浏览器（`main.py:6008-6023`）。

| 任务 | 触发方式 | 轮询/间隔 | 状态字段 | 推送目标 | 去重/冷却 |
|------|----------|-----------|----------|----------|-----------|
| 公告推送 `announcement_task` | 常驻 while（`main.py:4504`） | 无订阅或失败时睡 600 s（`4512,4517`）；正常 `announcement_poll_interval` 分钟，下限 60 s（`4566-4569`） | 每条订阅的 `since_ts` | `context.send_message(msg_origin, chain)`（`4558`），`msg_origin` 缺失则跳过（`4543-4547`） | `ts > since_ts` 才推（`4525`），推完写回 `since_ts`（`4564`）；**`last_ids` 从未使用** |
| 理智推送 `sanity_task` | 常驻 while（`4576`） | 无订阅睡 600 s（`4583`）；正常 `sanity_poll_interval` 分钟，下限 60 s（`4674-4675`）；订阅间 1.5 s（`4672`） | `last_notified`（0/1 当布尔用） | `msg_origin`（`4653`），内容 `At(qq=user_id)` + 文本（`4649-4651`） | 4 h 冷却（`4601`）；已提醒过且仍满则跳过（`4636-4640`）；未满时把标记重置为 0（`4664-4668`） |
| 调度券 `ticket_task` | 常驻 while（`6025`） | **硬编码 1200 s**（`6097`）；订阅间 2 s（`6095`） | `last_notified` | `msg_origin`（`6088`），`At` + 文本 | 4 h 冷却（`6043`）；任一聚落 `remainMoney >= moneyMax` 即触发（`6063-6071`） |
| 自动签到 `auto_sign_in_task` | 计算下一个 `HH:MM` 并 sleep 到点（`4686-4712`） | 每日一次，时间格式非法回落 00:05（`4691-4694`） | `sign_state.json.last_sign_date` | 可选通知群（`auto_sign_in_notify_group`，`4778-4850`） | 账号间 `auto_sign_in_interval` 秒（默认 3，`4767`）；以日期字符串判断今天是否已跑 |

自动签到的推送目标解析值得单独记一笔（`main.py:4779-4826`）：配置允许只填群号，代码会从已有订阅的 `msg_origin` 里找同群号者，找不到就借第一个 origin 的平台前缀拼一个；若用户填了完整 ID 但平台名对不上，还会把平台名替换成已知平台并打 warning。这是**启发式修补**，不是稳定契约。

推送内容形态：公告优先推详情图，渲染失败才退回「标题 + 摘要」文本（`main.py:4539-4556`）；理智/调度券是 At + 纯文本。注意 URL 到本地路径的转换用 `url.replace("file:///", "")` 再 `os.path.normpath`（`main.py:4552`）。

## 7. 渲染（`core/render.py`）

### 7.1 流程

1. `render_html(template_name, data, options)`（`core/render.py:79-93`）：读模板 → `_adapt_template` → `_inline_assets` → `_render_jinja` → `_screenshot`。
2. **模板语法转换**（`core/render.py:95-158`）：这是从 Yunzai（art-template）移植留下的兼容层，把 `{{if}}`/`{{else}}`/`{{else if}}`/`{{each}}`/`{{/each}}`/`{{@ x}}`/`$index`/`$value` 等改写成 Jinja2，`{{@ x}}` 转为 `|safe`（`144-156`），`||` 取第一段（`135`）。
3. **资源内联**（`core/render.py:160-215`）：把 `<link rel="stylesheet" href="{{pluResPath}}xxx.css">` 换成 `<style>`，把 `src="{{pluResPath}}x.png"`、CSS `url({{pluResPath}}x)`、行内 style 背景都转成 base64 data URI，这样 Playwright 用 `file://` 打开时不必再发本地请求。
4. **Jinja 渲染**（`core/render.py:217-226`）：类级共享 `Environment(autoescape=True, keep_trailing_newline=True)`（`17-24`），并补一个 `_res_path` 别名。
5. **截图**（`core/render.py:228-302`）：
   - 懒启动 Playwright，`chromium.launch()` 只做一次，启动阶段加锁（`239-244`）。
   - 每次渲染新建 context：`device_scale_factor=2.0`，但模板名含 `announcement` 时强制 1.0（超长公告会撞 Chromium 16384 px 上限，`246-251`）；viewport 1300×800。
   - HTML 写到模板同目录的 `tmp_*.html`，`page.goto(file://...)`，`wait_until="networkidle"`，超时用配置 `render_timeout`（默认 30000 ms，`263-267`）。
   - 再 `page.evaluate` 等所有图片 load/error，固定等 200 ms（`269-278`），然后按 `document.body.firstElementChild` 的 bounding box 裁剪截图（`279-288`），无 box 时 `full_page=True`（`290`）。
   - `finally` 删除临时 HTML、关闭 page/context（`293-297`）；任何异常只记日志并返回 `None`（`300-302`）。
6. 关闭：cancel 清理任务 → 关浏览器 → 停 Playwright（`304-311`）。

### 7.2 模板与资源清单

`resources/` 下 18 个 HTML + 19 个 CSS，被 `render_html` 实际引用的有 16 个：

| 模板 | 使用点 | CSS | 语法风格 |
|------|--------|-----|----------|
| `help/help.html` | `main.py:1495` | `help.css` | 混合（art-template 15 处 / Jinja 16 处） |
| `enduid/bind-list.html` | `main.py:1548` | `bind-list.css` | 纯 art-template |
| `stamina/stamina.html` | `main.py:2311` | `stamina.css` | 纯 art-template |
| `note/note.html` | `main.py:2571` | `note.css`（另有孤儿 `note_manual.css`） | 混合 |
| `crisis-contract/crisis-contract.html` | `main.py:2745` | `crisis-contract.css` | 纯 Jinja |
| `achievement/achievement.html` | `main.py:2867` | `achievement.css` | 纯 art-template |
| `operator/list.html` | `main.py:3075` | `list.css` | 混合 |
| `operator/operator.html` | `main.py:3268` | `operator.css` | 纯 art-template |
| `gacha/gacha-record.html` | `main.py:3431` | `gacha-record.css` | 纯 art-template |
| `gacha/gacha-analysis.html` | `main.py:3668` | `gacha-analysis.css` | 混合 |
| `announcement/list.html` | `main.py:4212` | `list.css` | 纯 art-template |
| `announcement/detail.html` | `main.py:4172,4227,4539` | `detail.css` | 纯 art-template |
| `calendar/calendar.html` | `main.py:4498` | `calendar.css` | 纯 Jinja |
| `area/spaceship.html` | `main.py:4952` | `spaceship.css` | 纯 art-template |
| `area/area.html` | `main.py:5080` | `area.css` | 纯 art-template |
| `gacha/global-stats.html` | `main.py:5981` | `global-stats.css` | 混合 |

孤儿资源（无任何引用，对应未完成或已废弃功能）：`gacha/simulate-result.html`、`maaend/tasks.html`、`note/note_manual.css`、`operator/img/opbg.png`（`README.md:213` 仍建议替换它，但代码只认 `bg1.png`/`bg2.png`，见 `main.py:3047-3051,3061`）、`resources/tutorialmaa/1-8.png`。

字体与图片：19 个 CSS 中有 18 个用 `@font-face` 指向 `../font/HarmonyOS_SansSC_Bold.ttf`（约 8 MB，例如 `resources/stamina/stamina.css:2-6`），唯一例外是 `resources/calendar/calendar.css`——它没有内置字体，靠 `calendar.html:8` 外链的 Google Fonts 渲染中文；图片资源含 `img/`（背景、地区图、帝江号、武陵等）、`meta/`（属性/职业/阶段/潜能图标）、`operator/img/bg1|bg2.png`、`gacha/img/red|yellow.png`、`help/img/`。整个 `resources/` 12 MB。**`calendar.html:8` 外链 `fonts.googleapis.com`** 的 Noto Sans SC，渲染时会产生真实外网请求。

### 7.3 超时与失败处理

- 超时来自配置 `render_timeout`（`_conf_schema.json:24-28`，`main.py:957-959`），只作用于 `page.goto`。
- 失败即 `None`，由各命令自己决定回退：文本回退（`理智`/`便签`/`绑定列表`/`干员列表`/`抽卡记录`/`抽卡分析`/`危机合约`/`成就`/`全服统计`/`zmd`）或只报一句错误（`日历`/`帝江号`/`地区建设`/`面板`/`公告`）。
- 一个可注意的细节：输出文件后缀是 `.png`，但 `page.screenshot(..., type="jpeg")`（`core/render.py:236,288`），即**扩展名与实际编码不一致**；下游若按后缀判断格式会踩坑。
- README 的排障条目印证了渲染是主要故障面：Q1 要求先 `playwright install chromium`（`README.md:70-73,366-368`）、Q5 指向 `resources/cache/` 读写权限（`README.md:390-392`）。

## 8. AstrBot 耦合点与模块映射

### 8.1 耦合清单（按出现次数）

| 耦合面 | 具体 API | 次数/位置 | 说明 |
|--------|----------|-----------|------|
| 插件注册 | `@register(...)` + `class EndfieldPlugin(Star)` | `main.py:915-922` | 入口签名 `__init__(self, context: Context, config: AstrBotConfig)` |
| 命令路由 | `@filter.command` / `@filter.regex` | 30 + 1 | 参数类型注解（`index: int`、`page: int = 1`、`phone: str`）由框架解析 |
| 事件对象 | `event.get_sender_id()` 32、`event.get_group_id()` 7、`event.message_str` 5、`event.unified_msg_origin` 3、`event.get_self_id()` 2、`event.get_message_str()` 1、`event.get_platform_name()` 1 | `main.py` 全文 | 身份、会话定位、原始文本 |
| 回复 | `event.plain_result` 132、`event.image_result` 17、`event.chain_result` 1 | 全文 | generator 逐条 yield |
| 主动推送 | `context.send_message(msg_origin, MessageChain)` | `4558,4653,4837,6088` | 依赖 `unified_msg_origin` 字符串 |
| 消息组件 | `MessageChain` 5、`Plain` 5、`At` 2、`Image.fromFileSystem` 2 | `4549-4556,4649-4651,4837,6084-6086` | `Node`/`Nodes` 被 import 但从未使用（`main.py:20`） |
| 会话等待 | `session_waiter` + `SessionController` | `1966-1968,2069-2073,2098` | 手机绑定、国际服登录的两段问答 |
| 数据目录 | `StarTools.get_data_dir()` | `main.py:944-945` | 决定 7 个 JSON 的位置 |
| 配置 | `AstrBotConfig`（dict 语义） | `main.py:923-937,957` | 读 11 个键，其中 `verify_ssl` 不在 `_conf_schema.json` 里 |
| 平台特化 | `AiocqhttpMessageEvent`、`client.send_group_msg/send_private_msg/delete_msg` | `main.py:1613-1635,1640-1648` | 撤回功能**只在 aiocqhttp 下可用**，其它平台静默降级 |
| 日志 | `logger` | 75 处 | 无第三方日志依赖 |

### 8.2 可复用比例（推断，按行数与依赖密度估）

| 模块 | 行数 | 与框架的耦合 | 复用判断 |
|------|------|--------------|----------|
| `core/client.py` | 659 | 仅 `from astrbot.api import logger`（`core/client.py:2`）+ 回调签名 | 约 90% 可直接搬：换掉 logger、把 `on_auth_invalid` 接成自己的清理逻辑即可 |
| `core/user.py` | 330 | 仅 logger（`core/user.py:6`） | 约 95% 可搬；数据目录与文件布局需按目标项目重定 |
| `core/render.py` | 311 | `Star` 仅作类型注解（`core/render.py:7,26`）+ logger | 约 90% 可搬；Playwright/Jinja 逻辑与框架无关 |
| `core/utils.py` | 43 | 无 | 100% 可搬（一张文案表 + 占位符替换） |
| `main.py` | 6122 | 31 个 handler、4 个任务、全部消息收发 | 纯函数部分（`36-912` 的格式化与危机合约解析、`1203-1341` 的名称归一化与好友展示提取、`5087-5172` 目标归一化与面板统计）约 1100 行可迁移，约 18%；其余是框架壳 |

### 8.3 必须重写的部分

- **31 个 handler**：`@filter.command` 装饰器、`yield event.*_result`、`event.*` 取值全部要换。
- **4 个后台任务**：`initialize()` 里 `asyncio.create_task` 的启动方式、`terminate()` 的取消方式、`context.send_message` 的推送方式。
- **配置读取**：`AstrBotConfig.get` → 目标项目的配置模型；`_conf_schema.json` 需重做。
- **会话等待**：`session_waiter` 的两段式问答（手机绑定、国际服登录）在多数框架没有等价物，需要自己实现「下一条消息归属」的状态机。
- **撤回**：依赖 aiocqhttp 的 `delete_msg`，其它平台没有；授权/扫码的 100 s 撤回策略要重新设计。
- **数据目录**：`StarTools.get_data_dir()` 换成目标项目的持久化位置（并保留 `bindings.json` 之外 6 个文件的可选性）。
- **消息组件**：`Image.fromFileSystem` / `At(qq=...)` / `MessageChain` 需要映射到目标协议端。

## 9. 许可证、依赖与风险

### 9.1 许可证

- 根 `LICENSE` 是 **GNU AGPL-3.0 全文**（`LICENSE:1-8`）；`metadata.yaml` 没有 license 字段；README 未声明例外或双授权。
- AGPL-3.0 的强 copyleft 与第 13 条（远程网络交互）意味着：**分发修改版或以网络服务形式提供修改版时，需要按 AGPL 提供对应源码**；把它并入闭源服务通常不可行（推断）。若目标项目许可证不同，直接复制 `core/` 代码会把整个衍生作品拉进 AGPL 约束（推断）。
- 研究其设计与接口、另写实现，与复制代码是两件事——这一区分本身是事实层面的边界，具体义务需按目标项目的分发形态判断。

### 9.2 依赖

`requirements.txt` 只有三行且**全部未锁版本**：`httpx`、`jinja2`、`playwright`。

| 依赖 | 用途 | 安装成本/风险 |
|------|------|----------------|
| `playwright` | 截图 | 除 pip 包外还需 `playwright install chromium`（`README.md:70-74`），浏览器内核体积大、CI 与容器镜像需额外层；服务端长期驻留一个 Chromium 进程（`core/render.py:244`） |
| `httpx` | 全部 API 与图片下载 | 常规，但同一进程里同时存在 `EndfieldClient` 的长连接池（`core/client.py:26`）与 `get_b64` 的另一条连接池（`main.py:1163-1165`） |
| `jinja2` | 模板渲染 | 常规；但模板本身来自 Yunzai，需保留 `_adapt_template` 兼容层（`core/render.py:95-158`） |
| 系统字体 | 中文渲染 | 内置 8 MB 字体，18 个 CSS 用它；`calendar.css` 例外，靠 `calendar.html:8` 外链 Google Fonts，离线环境会等 `networkidle` 超时或降级字体 |

### 9.3 第三方 API 与数据出境面

| 项 | 事实 | 来源 |
|----|------|------|
| 服务地址 | `https://end-api.shallow.ink`（API）、`https://end.shallow.ink`（授权前端与 Key 申请页） | `core/client.py:5-6`、`_conf_schema.json:5` |
| 上游性质 | 由第三方运营的「协议终端」网关，不是森空岛官方接口；README 称「基于森空岛 API」 | `README.md:13,80` |
| 出境数据 | `X-API-Key`、`X-Framework-Token`（可代表账号登录态）、`client_id`/`platform_id`（机器人 QQ 与用户 QQ）、`role_id`/`server_id`、以及全部游戏数据请求 | `core/client.py:36-49,283-411` |
| 图片 | 头像/封面等 URL 由 API 返回，插件再直接下载并缓存到 `resources/cache/` | `main.py:1111-1180` |
| 其他外联 | `calendar.html:8` 的 Google Fonts | `resources/calendar/calendar.html:8` |

含义（事实层）：**账号级 token 与用户 QQ 会离开部署方网络**，进入第三方网关；`framework_token` 明文存于本地 JSON，任何能读插件数据目录的人都能冒用该账号。

### 9.4 硬编码与密钥风险

- 未发现硬编码的 api_key/密码/密钥（对 `main.py`、`core/*.py` 做了 `(api_key|secret|password|token)\s*=\s*"..."` 扫描，命中为空）。
- 硬编码 URL 只有 `core/client.py:5-6` 与 `resources/calendar/calendar.html:8`；`BASE_URL` 可作为构造参数传入（`core/client.py:13`），但 `main.py:938` 未暴露该配置，实际不可切换。
- `verify_ssl` 在代码里读取（`main.py:927`），但**不在 `_conf_schema.json` 中**（schema 只有 10 个键，见 `_conf_schema.json:1-60`），因此用户无法通过 UI 关闭证书校验——属于配置与代码的不一致，而非可用开关。
- `get_b64` 有 SSRF 防护：解析域名并拒绝私有/回环/链路本地/多播地址，解析失败也拒绝（`main.py:1130-1156`）。但防护只覆盖图片下载，API 主机仍由代码固定。
- 明文敏感信息会进日志的风险点：国际服登录反复提醒用户自行撤回密码（`main.py:2085,2116,2202`），密码本身不写日志，但**用户消息留在聊天平台**。

### 9.5 其它实现层面的风险（事实）

- 共享 client 上的 `client_id/platform_id` 会串号（见 3.2），在多用户并发时不可作为身份依据。
- `announcement_task` 里 `since_ts` 只在新公告推送成功后更新（`main.py:4564`），若渲染或发送抛异常，该群仍会重复收到同一公告——去重是「至少一次」语义。
- 自动签到对所有用户的全部账号遍历（`main.py:4729`），与配置的通知群解析耦合，配置填错时只能从 warning 日志发现（`main.py:4843-4846`）。
- `同步面板`、`抽卡分析同步` 用固定次数轮询（90 s / 120 s），超时给的是「稍后再看」，不做后台续跑（`main.py:3310-3367,3470-3494`）。

## 10. 对移植的启示（只列事实与含义）

1. **上游不是「游戏 API 客户端」，而是「网关客户端」**：52 个端点全部指向同一第三方主机，鉴权靠 `X-API-Key` + `X-Framework-Token`（`core/client.py:5,36-49`）。含义：移植后仍需该 Key 与网关可用性；没有网关就没有数据，也无法只搬前端。
2. **凭据模型很薄**：没有 refresh、没有过期时间、失效即删绑定（`core/client.py:147-163`、`main.py:969-1000`）。含义：目标项目若要做更可靠的账号管理（重登提示、过期预警），必须自行加状态，不能照抄。
3. **数据层可整搬的比例最高**：`core/user.py` 的原子写、锁、去重与唯一 primary 逻辑是纯 Python（`core/user.py:28-90`），只需换目录与 logger。
4. **渲染是最大的移植成本**：Playwright + Chromium + 12 MB 资源 + 8 MB 字体 + 16 个模板 + 一套 art-template 兼容层（`core/render.py`、`resources/`）。含义：要么完整保留这条链路，要么所有图卡命令都退化成文本，没有中间态。
5. **模板与数据契约是两套**：`main.py` 里每个命令各自拼 `render_data`（例如 `main.py:2302-2308`、`4486-4496`），没有统一的 view model。含义：复用模板就必须逐一复刻字段名，模板不能脱离 handler 独立使用。
6. **纯函数资产被低估**：通用格式化（`36-144`）、危机合约的解析/格式化（`145-912`，约 770 行）、名称归一化与好友展示提取（`1203-1341`）、推送目标归一化与面板统计（`5087-5172`）都不依赖框架，合计约 1100 行（约占 `main.py` 的 18%）；其中危机合约部分是上游最复杂的领域逻辑，也是最值得对照阅读的部分。
7. **命令语义有隐含前提**：所有查询类命令只作用于 primary 绑定（`core/user.py:51-57`），多账号靠「切换绑定」显式切；签到则遍历全部。含义：目标项目若想要「一个 QQ 多角色自动分流」，上游没有现成模型。
8. **推送模型依赖 `msg_origin` 字符串**：订阅存的是 AstrBot 的 `platform:type:id` 三元组（`core/user.py:158-168`），纯群号配置还要靠启发式补全（`main.py:4791-4824`）。含义：订阅表结构与目标项目的会话标识强绑定，换框架等于重建订阅数据。
9. **代码里存在未完成与死代码**：MaaEnd 7 个端点 + 管理器 + 模板全部未接入（`main.py:954`、`core/user.py:238-271`）、模拟抽卡同类（`core/client.py:525-541`）、`gacha/accounts`、`bindings/{id}/primary`、`enduid/set-primary` 未被调用。含义：评估「上游有 XX 能力」时应以是否被 handler 调用为准，不能只看 client 方法或 README 的 TODO 勾选。
10. **文档与代码有已知偏差**：`README.md:382` 的 `data/users.json` 不存在（实际 `bindings.json`，`core/user.py:44`）；`verify_ssl` 不在 schema（`main.py:927` vs `_conf_schema.json`）；`手机绑定` 代码可用但帮助标「暂不可用」（`main.py:1393`）。含义：移植时以源码为准，文档只当线索。
11. **许可证是硬约束**：整仓 AGPL-3.0（`LICENSE:1-8`），直接拷贝 `core/` 会把衍生作品纳入 AGPL。含义：若目标项目不是 AGPL，代码级复用与设计级借鉴需要分开处理。

## 11. 未解问题与后续核对建议

- **`data/users.json` 与「33 个命令」两个提法均与源码不符**：前者是过时 FAQ（`README.md:382`），后者是 README 用法展开数（32 行表格 + `zmd`）。若后续引用这两个数字，应按本文口径标注。
- **网关的官方性未验证**：`end-api.shallow.ink` / `end.shallow.ink` 的运营主体、服务条款、数据保留策略、是否获得授权，仓库内没有任何说明；README 只给出申请链接（`README.md:80`）。需要向其页面或运营方确认，无法从代码推断。
- **业务错误码表缺失**：代码只区分 `code == 0` 与其它（`core/client.py:99-105`），没有枚举码含义；若要区分「凭证失效」「角色不存在」「限流」，需要实测或索要文档。
- **`_conf_schema.json` 与实际读取键不完全一致**（`verify_ssl` 只读不配）；是否存在更多未文档化配置项需以运行时为准。
- **上游无测试**：仓库内没有 test 目录或测试文件，危机合约解析等复杂逻辑只能靠阅读与实测验证。
