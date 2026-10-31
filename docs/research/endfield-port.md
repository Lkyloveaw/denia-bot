# 《endfield 移植方案》终稿

> 本文为 2026-10-31 制定的目标方案，架构树和里程碑不代表全部已交付。
> 当前能力、外部渲染服务前置条件及未完事项见 `../endfield.md`；分支整合审查见
> `branch-merge-review-2026-10-31.md`。第 18 节保留合并前的历史快照。

- 版本：1.0（2026-10-31）
- 目标仓库：`Lkyloveaw/denia-bot`（NoneBot2 + MuiceBot + OneBot v11）
- 上游：`Entropy-Increase-Team/astrbot_plugin_endfield` v3.0.3，commit `41482b7`（2026-08-24），AGPL-3.0
- 基线：**AstrBot 基线 + denia 远程 HTML→PNG**（#35）；官方 NoneBot 插件只作参考（#34）
- 决策来源：#2、#3、#5、#6、#8、#10、#12、#14、#16、#17、#18、#19、#20
- 研究底稿：`docs/research/endfield-upstream.md`、`endfield-integration.md`、`endfield-nonebot-upstream.md`、`endfield-api.md`
- 状态：方案定稿；实现另开（本 map 不直接改业务代码）

---

## 0. 结论摘要与可行性

**可行。** 功能面可以覆盖上游三期（MVP → 查询全量 → 订阅/自动任务）；真正的成本不在代码量，而在：

1. AstrBot 专属胶水（`Star`、`filter.command`、`event.image_result`、`MessageChain`、后台任务）全部重写为 NoneBot matcher + asyncio；
2. 16 套模板的字段映射与自包含化（base64 内联、字体、长卡分页），以适配 denia 现有远程渲染器；
3. 多用户绑定/凭据、订阅调度、面板与测试的工程化。

**关键边界**：

- 客户端/存储/模板/渲染从 AstrBot 同源移植的部分是 **AGPL-3.0-only**；纯逻辑/配置/接线为 MIT；分发组合按 AGPL 处理。
- 官方 NoneBot 插件 `nonebot-plugin-endfield` 许可证冲突（AGPL vs pyproject MIT）、PyPI 缺模板/图片、功能仅一半、近 6 个月无提交 → **只作协议/设计参考，不复制代码**。
- `api_key` 与测试账号已就绪；只读探测已完成（18 项），429/524/错误语义已记录。
- **不装本地 Chromium**：图卡作为自包含 HTML 发到 denia 现有 `POST /render`，失败回退文本。

**主要风险**：网关字段漂移、账号风控/ToS、AGPL 边界、渲染器 2MB/2400px/2 并发限制、OneBot 私聊可达性、真实 token 生命周期尚未做 HITL 实测。

---

## 1. 目标与非目标

### 目标

- 命令面、绑定、查询、图卡、订阅、自动签到、面板、测试与运维文档可落地。
- 保持仓库约定：`xxx_logic.py` 纯逻辑（不 import nonebot/muicebot）、薄接线、`nonebot/tests/_bootstrap.py` 测试、`data/` 0600/原子写。
- 数据不出本机：不接远程 T2I，不把 token/key 写入日志/issue/仓库。
- 保留上游 AGPL 署名与许可。

### 非目标

- 本方案不直接实现/合入代码（实现另开）。
- 不修改上游仓库、不向上游提 PR。
- 不做其他游戏（明日方舟本体、鸣潮）的查询。
- 不做远程 T2I/第三方渲染服务。
- 不做像素级视觉 golden；模板视觉人工验收一次。

---

## 2. 上游事实与基线

| 项 | AstrBot 版 | 官方 NoneBot 版 |
|---|---|---|
| 版本 | v3.0.3 / `41482b7` | v0.2.5 / `526dc466` |
| Python | 6,676 行（`main.py` 6,122） | 约 3.4–3.9k 行 |
| 命令 | 31 个注册点 | 13 个 matcher |
| 图卡 | 16 套在用模板 | 6 全 + 2 半 / 16 |
| 许可 | AGPL-3.0 | 仓库 AGPL，pyproject MIT（冲突） |
| 状态 | 团队主力，2026-08 仍更新 | 2026-05 后停滞，PyPI 缺资源 |
| 结论 | **基线** | **只参考** |

- 上游数据链路：所有登录/查询经第三方「协议终端」`https://end-api.shallow.ink`（`X-API-Key` + `X-Framework-Token`），授权前端 `https://end.shallow.ink`；不直连森空岛。
- 上游渲染：Playwright + Chromium + 16 模板 + 12 MB 资源；`calendar.html` 外链 Google Fonts；`.png` 后缀但 JPEG 编码。
- 上游 client：单例 + `set_caller` 仅 2 处调用 → header 串号；错误吞成 `None`；只对 403 识别 token 失效（漏 401）。
- 上游 `framework_token` 明文落盘、无刷新；403 命中标记即删绑定。

---

## 3. 目标架构

### 3.1 目录树

```text
nonebot/
├─ endfield/
│  ├─ __init__.py          # MIT；导出公开 API/版本
│  ├─ config.py            # MIT；EndfieldConfig + 校验 + mtime 重载
│  ├─ logic.py             # MIT；纯函数：格式化/分页/权限/consent/cache key
│  ├─ client.py            # AGPL；上游 core/client.py 同源移植（端点子集）
│  ├─ store.py             # AGPL；上游 core/user.py 同源移植 + 订阅存储
│  ├─ render.py            # AGPL；模板/内联/自包含 HTML + POST /render 客户端
│  └─ scheduler.py         # MIT；订阅/签到/心跳/审计（新写）
├─ plugins/
│  └─ endfield.py          # MIT；matcher/rule/权限/发送/help 接线
├─ tests/
│  ├─ _bootstrap.py        # 幂等 nonebot.init()
│  ├─ fixtures/endfield/   # 脱敏样本 + 合成 token 样本 + 小 PNG
│  └─ endfield/
│     ├─ test_logic.py
│     ├─ test_config.py
│     ├─ test_store.py
│     ├─ test_client.py
│     ├─ test_wiring.py
│     ├─ test_render.py
│     ├─ test_scheduler.py
│     ├─ test_dashboard.py
│     └─ test_e2e.py
├─ dashboard.html          # 终末地页
├─ dashboard.py            # /api/endfield/*（MIT additions）
└─ data/
   └─ endfield/
      ├─ bindings.json     # 0600；bot 独占
      ├─ subscriptions.json# 0600；bot 独占
      ├─ heartbeat.json    # 0600；bot 写，面板读
      └─ audit.jsonl       # 0600；bot 写，面板读尾部

仓库根：
├─ LICENSE-AGPL-3.0
├─ docs/third-party/endfield-NOTICE.md
├─ docs/research/endfield-port.md     # 本文件
├─ docs/research/endfield-*.md        # 研究底稿
├─ docs/OPERATIONS.md
└─ docs/PITFALLS.md
```

### 3.2 数据流

```text
QQ 事件 → plugins/endfield.py matcher/rule
        → 权限/配额/防抖（logic）
        → store（绑定/订阅）
        → client（httpx，per-request headers）
        → 视图（logic）
        → render（自包含 HTML → POST /render）
        → 发送（文本/图片）；失败回退文本

scheduler（60s tick）→ client → 公告/理智/券/签到
                    → PushSink（OneBot private/group）
                    → heartbeat/audit
```

### 3.3 模块职责与许可

| 文件 | 许可 | 职责 | 禁止 |
|---|---|---|---|
| `endfield/client.py` | AGPL-3.0-only | httpx AsyncClient；端点方法；错误映射；GET 重试 | 不 import nonebot/muicebot；不读写存储 |
| `endfield/store.py` | AGPL-3.0-only | 绑定/订阅 JSON；原子写；0600；去重/primary | 不发 HTTP；不 import nonebot |
| `endfield/render.py` | AGPL-3.0-only | 模板/内联/自包含 HTML；POST `/render` | 不 import status_image；不装本地浏览器 |
| `endfield/logic.py` | MIT | 文本/分页/权限/consent/缓存键/状态机 | 不 import nonebot/muicebot/httpx |
| `endfield/config.py` | MIT | 配置 dataclass/校验/mtime | 不做网络/存储 I/O |
| `endfield/scheduler.py` | MIT | tick/任务/心跳/审计/PushSink | 不复制上游后台任务代码 |
| `plugins/endfield.py` | MIT | matcher/rule/发送/help | 不写业务逻辑；不直接读 token |
| `dashboard.py` additions | MIT | 只读数据 + 配置写 + status | 不 import AGPL 模块；不直接写绑定/订阅 |

> 分发边界：MIT 接线在运行时会 import AGPL 的 client/store/render，整个插件分发按 **AGPL-3.0** 处理；仓库自身 MIT 代码保持 MIT。根 `LICENSE-AGPL-3.0` + NOTICE + README 声明。

---

## 4. 上游 → 目标模块映射

| 上游 | 目标 | 处理 | 许可 |
|---|---|---|---|
| `core/client.py`（659 行） | `endfield/client.py` | 保留端点方法/错误语义；去 AstrBot logger；**每请求显式传 header**；只搬所需端点；映射 `EndfieldError` | AGPL |
| `core/user.py`（330 行） | `endfield/store.py` | 保留原子写/去重/primary；路径改 `data/endfield/`；绑定 + 订阅 | AGPL |
| `core/render.py` | `endfield/render.py` | 模板加载/内联；HTTP 改为 `POST /render`；不启动本地 Chromium | AGPL |
| `main.py` handler（31 个） | `plugins/endfield.py` + `logic.py` | 重写为 NoneBot matcher；命令/权限/分页 | MIT |
| `main.py` 后台任务 | `endfield/scheduler.py` | 重写为 asyncio 单 tick；公告/理智/券/签到/心跳 | MIT |
| `main.py` 纯函数 | `endfield/logic.py` | 格式化/状态机/文案；去 AstrBot 依赖 | MIT |
| `resources/` 16 模板 | `endfield/templates/` | 原样保留 + 自包含化；字体/外链替换 | AGPL |
| `core/utils.py` | `endfield/logic.py` | 文案/格式化重写 | MIT |
| `_conf_schema.json` | `endfield/config.py` + `data/endfield.json` | 配置键/默认值/校验 | MIT |

**不搬**：14 个未调用端点、MaaEnd/模拟抽卡死代码、AstrBot `file://` + 本地 assets 渲染方式、官方 NoneBot 的 3,427 行代码（只参考）。

---

## 5. 数据模型

### 5.1 `bindings.json`（0600）

```json
{
  "version": 1,
  "users": {
    "<qq>": {
      "consent_version": 1,
      "consent_at": "2026-10-31T14:40:00+08:00",
      "bindings": [
        {
          "binding_id": "gateway id（可空）",
          "role_id": "游戏角色 id",
          "server_id": 1,
          "role_name": "角色名",
          "alias": "用户别名（可空）",
          "provider": "skland",
          "framework_token": "明文 token",
          "is_primary": true,
          "status": "valid",
          "invalid_reason": null,
          "invalid_at": null,
          "created_at": "ISO8601",
          "updated_at": "ISO8601",
          "last_used_at": "ISO8601|null"
        }
      ]
    }
  }
}
```

- 每 QQ 最多 3 个；按 `role_id + server_id` 去重；一个 `is_primary`。
- 失效标 `invalid`，保留元数据；不自动删。
- `api_key` 不在本文件；见 `endfield_secrets.json`。

### 5.2 `subscriptions.json`（0600）

```json
{
  "version": 1,
  "announcements": {
    "<group_id>": {
      "target": "group:<id>",
      "since_ts": 0,
      "last_item_id": "",
      "enabled": true,
      "fail_count": 0,
      "created_by": "<qq>",
      "created_at": "ISO8601",
      "updated_at": "ISO8601"
    }
  },
  "sanity": {"<qq>": {"target": "private:<qq>", "enabled": true, "last_notified": 0, "last_checked": 0, "fail_count": 0}},
  "tickets": {"<qq>": {"target": "private:<qq>", "enabled": true, "last_notified": 0, "last_checked": 0, "fail_count": 0}},
  "signin": {"<qq>": {"enabled": true, "last_run_date": "2026-10-31", "last_result": "ok|partial|failed", "last_error": ""}},
  "meta": {"last_announcement_poll": 0, "last_sanity_poll": 0, "last_ticket_poll": 0}
}
```

### 5.3 `endfield.json`（非敏感配置，0600）

| 键 | 默认 | 范围 |
|---|---|---|
| `enabled` | true | 总开关 |
| `enable_queries` | true | 查询命令 |
| `enable_subscriptions` | true | 订阅任务 |
| `enable_signin` | true | 自动签到 |
| `announcement_poll_minutes` | 10 | 5–60 |
| `sanity_poll_minutes` | 20 | 10–120 |
| `ticket_poll_minutes` | 20 | 10–120 |
| `signin_time` | `00:05` | HH:MM |
| `signin_jitter_minutes` | 60 | 0–120 |
| `quiet_start` / `quiet_end` | `23:00` / `08:00` | 个人提醒 |
| `daily_push_limit` | 10 | 0–100 |
| `push_retry` | 1 | 0–3 |
| `push_disable_after_failures` | 3 | 1–10 |
| `render_url` | `http://127.0.0.1:3000` | 仅 loopback/私有 |
| `render_timeout_seconds` | 20 | 1–60 |
| `log_level` | `INFO` | DEBUG/INFO/WARNING/ERROR |
| `audit_max_entries` | 200 | 50–1000 |

### 5.4 其他文件

- `endfield_secrets.json`（0600）：`{"api_key": "..."}`；面板写、env fallback。
- `heartbeat.json`（0600）：`{ts, version, scheduler_started_at, last_announcement_poll, last_sanity_poll, last_ticket_poll, last_signin_date}`；每 tick 写。
- `audit.jsonl`（0600）：JSONL，1 MB 轮转 ×2；脱敏；面板读尾部 50。
- 所有写：`.tmp` + `os.replace`；`asyncio.Lock` 串行；`data/` 已 gitignore；POSIX 0600（Windows best-effort + 文档）。

---

## 6. API 契约

### 6.1 Header

| Header | 必需 | 语义 |
|---|---|---|
| `X-API-Key` | 所有端点 | bot 级接入密钥；401/401 缺/错 |
| `X-Framework-Token` | 游戏数据/绑定 | 用户级；无刷新；403/40301 或 400/400 或 401/401 失效 |
| `client_id` | 建议 | bot QQ；每请求显式传 |
| `platform_id` | 建议 | 用户 QQ；每请求显式传 |
| `platform` | 建议 | `bot` |
| `Content-Type` | 写请求 | `application/json` |

### 6.2 端点组

- 公开（API key only）：`/api/announcements*`、`/api/wiki/*`、`/api/endfield/gacha/global-stats`、`/api/endfield/gacha/pool-chars`、`/api/v1/bindings`（GET，按 user_identifier）。
- token：`note`、`stamina`、`card/*`、`search/chars`、`gacha/records`、`gacha/stats`、`gacha/accounts`、`panel/*`、`area`/`spaceship`/`domain`/`achieve`。
- 登录/绑定：QR、phone、skport password、authorization requests、bindings CRUD、primary。
- 写：`gacha/fetch`、`panel/sync`、`attendance`、`enduid/set-primary`、`maaend/*`。
- 完整 52 端点见 `docs/research/endfield-api.md`。

### 6.3 错误表

| HTTP | code | 含义 | 动作 |
|---|---|---|---|
| 200 | 0 | 成功 | 取 data |
| 200 | ≠0 | 业务失败 | 不重试；按端点提示 |
| 401 | 401 | 缺/错 API key | 配置错误；面板提示 |
| 401 | 401 | 无效凭证（gacha/stats） | 清绑定；重授权 |
| 403 | 40301 | 缺/无效 framework token | 未绑定/失效；提示 |
| 400 | 400 | 缺 framework_token（gacha/records） | 未绑定；提示 |
| 404 | — | `text/plain` | 不重试；修端点 |
| 429 | — | HTML；约 14 请求/3 分钟实测 | 60 s + jitter；最多 1 次 |
| 524 | — | 空 body；global-stats 带 provider 实测 31 s | transient；缓存/降级 |
| — | — | 超时/连接 | GET 重试 1；写不重试 |

### 6.4 限流/缓存

- `x-ratelimit-limit` 300/600；`remaining` 递减；数值不完全自洽，只作提示。
- 429 无 `Retry-After`；body HTML；不解析 JSON。
- 缓存：公告最新 60 s；公告列表 5 min；Wiki 10 min；卡池 10 min；global-stats 5 min；抽卡记录 1–5 min/role；note/stamina 15–30 s/用户；角色卡 5–10 min/role。
- 并发 `Semaphore(2–4)`；同用户串行；GET 重试 1；写不重试。

### 6.5 Token 生命周期

- 网页授权：`POST /api/v1/authorization/requests`（scopes `user_info`/`binding_info`/`game_data`/`attendance`）→ `auth_url` → 轮询 status → `POST /api/v1/bindings`。
- 扫码：`GET /login/endfield/qr` → poll `/qr/status` → `POST /qr/confirm` → bind。
- 手机/国际服：phone send/verify；skport password。
- **无 refresh**；失效只能重授权/扫码。
- 多账号：`user_identifier`=QQ；最多 3；primary。
- 绑定列表 `GET /api/v1/bindings?user_identifier=...` 只需 API key；bot 侧只按自己的 QQ 映射查询，不透传用户输入。

### 6.6 数据边界

- 网关可见 API key、framework token、bot/用户 QQ、role/server id、查询内容。
- 仓库内无运营方条款/留存说明；**需书面确认**：留存期限、是否用于分析/共享、key 权限/配额/封禁、429/524 语义、长期轮询许可、撤销授权/删除路径。
- 日志/错误/面板不回显 key/token；绑定列表不暴露给普通用户。

---

## 7. 命令面、触发与权限

### 7.1 一期（MVP，纯文本）

| 命令 | 触发 | 权限/说明 |
|---|---|---|
| `授权登陆` | 私聊 | 本人；首次发同意告知；网页授权优先 |
| `扫码绑定` | 私聊 | 本人；一期占位，二期实现 |
| `便签` | 群聊 @ / 私聊 | 查询主体=发送者；primary |
| `理智` | 群聊 @ / 私聊 | 同上 |
| `签到` | 群聊 @ / 私聊 | 手动签到；与自动签到互不重复 |
| `绑定列表` | 私聊 | 本人；角色名/区服/状态；不显示 token |
| `切换绑定 N` | 私聊 | 本人；改 primary |
| `删除绑定 N` | 私聊 | 本人；本地优先，网关失败也本地删 |

### 7.2 二期（2a 核心 6 + 2b 扩展 4，图卡优先/文本回退）

| 命令 | 端点 | 期 | 输出 | 备注 |
|---|---|---|---|---|
| `抽卡记录` | `/gacha/records?page&limit=20` | 2a | `gacha/gacha-record.html` | 最近 6 个月；20/页 |
| `抽卡分析` | `/gacha/stats` | 2a | `gacha/gacha-analysis.html` | 网关聚合 |
| `干员列表` | `/panel/chars?page&page_size=20` | 2a | `operator/list.html` | 可先 `同步面板` |
| `干员面板` | `/card/char` + 可选 `/panel/char/:id` | 2a | `operator/operator.html` | 指定干员 |
| `危机合约` | `/card/detail` → `/card/crisis-contract` | 2a | `crisis-contract/crisis-contract.html` | 当期；可名称/ID |
| `日历` | `/wiki/activities` | 2a | `calendar/calendar.html` | 公开，无 token |
| `成就列表` | `/achieve` | 2b | `achievement/achievement.html` | 当前快照 |
| `帝江号建设` | `/spaceship` | 2b | `area/spaceship.html` | 当前快照 |
| `地区建设` | `/domain` | 2b | `area/area.html` | 调度券来源 |
| `全服统计` | `/gacha/global-stats` | 2b | `gacha/global-stats.html` | 公开；可选国际服 |
| `同步抽卡` | `POST /gacha/fetch` + poll | 2a | 文本 | 私聊；1 次/天/QQ |
| `同步面板` | `POST /panel/sync` + poll | 2a | 文本 | 私聊；1 次/天/QQ |

- 列表统一 `[页码]`，默认 1，20 条/页；图卡一页一张。
- 查询默认 primary；不提供内联账号参数；切账号用 `切换绑定 N`。
- 图卡成功→图片；失败/超 2MB/超 2400px→文本；不把原始响应/token 发给用户。

### 7.3 三期（订阅/公告）

| 命令 | 场景 | 权限 |
|---|---|---|
| `公告` / `公告最新` | 群聊 @ / 私聊 | 查询；公开端点 |
| `订阅公告` / `取消订阅公告` | 目标群 @ | **仅 superusers**；群主/管理员不行 |
| `订阅理智` / `取消订阅理智` | 私聊 | 本人；需绑定 |
| `订阅调度券` / `取消订阅调度券` | 私聊 | 本人；需绑定 |
| `订阅签到` / `取消订阅签到` | 私聊 | 本人 opt-in；需绑定 |
| `订阅列表` | 私聊 | 本人；显示状态/最近结果 |
| `终末地诊断` | 私聊 | superusers；摘要，不含秘密 |

### 7.4 触发/限额

- 群聊查询必须 @ 机器人；**@ 只作触发，查询主体永远是发送者**；不 @ 不触发。
- 绑定/订阅（个人）仅私聊；群聊发绑定命令忽略/提示私聊。
- `on_message(priority=3, block=True)`；精确文本匹配；无命令前缀；`COMMAND_START` 含空串的风险由精确匹配 + @ 控制。
- 5 s/QQ 防抖；查询 100 次/天/QQ；绑定尝试 3 次/天；切换 10 次/天；删除 20 次/天；主人豁免。
- 同步命令私聊、1 次/天/QQ；写操作不重试。
- `gate` 不覆盖自定义 matcher：@ 校验、防抖、配额在 `plugins/endfield.py` 内实现。

---

## 8. 渲染与展示

### 8.1 契约

- `POST {render_url}/render`：`{"html": "...", "width": 900, "height": 1200, "timeout_ms": 20000}` → `image/png`。
- 限制：HTML ≤2 MB；宽 320–1600；高 200–2400；超时 1–60 s；并发默认 2；满返回 429。
- 渲染端禁 JS、拦外网，只放行 `about:blank`/`data:`；HTML 必须自包含。
- 字体：优先渲染端镜像 Noto CJK；**不内联 8 MB/3.7 MB 字体**；视觉确需仓库字体时把它装进渲染器镜像。
- 图片：小图 base64；大背景压缩/换 CSS/SVG；单卡目标 <2 MB；超长公告分页/缩放；必要时调 `MAX_BODY_BYTES` 并记录。
- 失败：`RenderError` → 文本回退；429/503 一次退避重试或无重试；bot 侧 `Semaphore(2)`。
- 不引入 Python Playwright/local Chromium；渲染器容器 `docker/playwright-renderer/` 已存在。

### 8.2 16 模板映射

| 模板 | 命令 | 期 |
|---|---|---|
| `help/help.html` | 帮助/`zmd` | 二期图像升级 |
| `enduid/bind-list.html` | 绑定列表 | 二期图像升级 |
| `stamina/stamina.html` | 理智 | 二期图像升级 |
| `note/note.html` | 便签 | 二期图像升级 |
| `crisis-contract/crisis-contract.html` | 危机合约 | 2a |
| `achievement/achievement.html` | 成就列表 | 2b |
| `operator/list.html` | 干员列表 | 2a |
| `operator/operator.html` | 干员面板 | 2a |
| `gacha/gacha-record.html` | 抽卡记录 | 2a |
| `gacha/gacha-analysis.html` | 抽卡分析 | 2a |
| `calendar/calendar.html` | 日历 | 2a |
| `area/spaceship.html` | 帝江号建设 | 2b |
| `area/area.html` | 地区建设 | 2b |
| `gacha/global-stats.html` | 全服统计 | 2b |
| `announcement/list.html` | 公告 | 三期 |
| `announcement/detail.html` | 公告最新/公告详情 | 三期 |

- 模板/图片 AGPL；根 `LICENSE-AGPL-3.0` + NOTICE + README。
- `calendar.html` 删除 Google Fonts 外链；所有 `@font-face` 改渲染端 Noto CJK 或镜像内字体。

---

## 9. 订阅、自动签到与调度

### 9.1 类型/目标

| 类型 | 粒度 | 目标 | 权限 |
|---|---|---|---|
| 公告 | 群 | 群 | 仅 superusers 在目标群 @ 订阅 |
| 理智 | QQ | 私聊 | 本人 |
| 调度券 | QQ | 私聊 | 本人 |
| 自动签到 | QQ | 私聊（仅失败告警） | 本人 opt-in；签全部有效绑定 |

- 个人数据绝不发群；私聊失败不群内回退。

### 9.2 默认/配置

- 公告 10 min；理智/券 20 min；签到 00:05（00:05–01:05 jitter）。
- 个人提醒 23:00–08:00 静默；08:00 后重新检查条件再补发；公告不静默。
- 每 QQ 每天最多 10 条个人推送；私聊失败重试 1 次，连续 3 次禁用。
- 配置写入 `data/endfield.json`；面板 #18 编辑；mtime 热加载。

### 9.3 状态机

**公告**：每 N min 拉 `/announcements/latest`；`published_at_ts + item_id` 去重；新公告取详情 → 图卡/文本 → 推群；成功更新 `since_ts/last_item_id`；失败计数/禁用；订阅时以当前最新为基线，不回填。

**理智**：每 N min 遍历；primary；`/stamina`；`current >= max` 且 `last_notified==0` → 私聊；置 1；4 h 冷却；未满重置 0；token 失效标 invalid + 提示一次。

**调度券**：每 N min 遍历；primary；`/domain`；`remainMoney >= moneyMax` → 私聊列聚落；置 1；4 h 冷却；无满重置 0。

**自动签到**：仅 opt-in；`signin_time + jitter` 窗口；所有有效绑定逐个 `POST /attendance`；间隔 1.5 s；成功静默；失败私聊（静默顺延）；token 失效标记；429 暂停 60 s 重试 1 次；`last_run_date` 防重。

### 9.4 调度/心跳

- asyncio 单协程 tick 60 s；`meta.last_*` 持久化；重启补跑一次；`Semaphore(2–4)`；429 暂停 10 min；不 sleep 到目标时刻。
- 每 tick 写 `heartbeat.json`；面板 >180 s 标离线；离线/连续 3 次失败私聊主人 1 次/小时。
- `PushSink` 抽象：生产 OneBot `send_private_msg`/`send_group_msg`；测试 `RecordingSink`；`ENDFIELD_PUSH_DRY_RUN=1`。

---

## 10. 管理面板与运维

### 10.1 页面（`dashboard.html` 新增「终末地」）

- API Key 卡片（#38）。
- 运行状态：Key 来源、心跳、渲染器健康、绑定/订阅计数、last poll、429、最近错误。
- 绑定（只读）：QQ、角色名、区服、primary、状态；role_id 脱敏；无 token。
- 订阅（只读）：公告群、理智/券/签到计数、disabled、last error。
- 配置：#16 参数 + 渲染器 URL/超时 + 功能开关。
- 运维：测试 Key、渲染器健康、重载配置、文档链接；写操作提示走聊天命令。

### 10.2 写入边界

| 文件 | 写者 | 面板 |
|---|---|---|
| `endfield.json` | dashboard | 读写 |
| `endfield_secrets.json` | dashboard | 读写，masked |
| `bindings.json` | bot only | 只读 |
| `subscriptions.json` | bot only | 只读 |
| `heartbeat.json` | bot | 只读 |
| `audit.jsonl` | bot | 只读尾部 |

### 10.3 API

- `GET /api/endfield/status`（authorized）：key 来源、心跳、渲染器、计数、last poll、429、最近错误；不含 token/QQ/role_id。
- `GET/POST /api/endfield/config`（authorized/mutate）：校验 + 原子写 0600 + mtime。
- `GET /api/endfield/bindings`、`GET /api/endfield/subscriptions`：只读、脱敏。
- `GET /api/endfield/audit?limit=50`：脱敏尾部。
- `GET /api/endfield/render/health`：服务端调 `/health`；仅 loopback/私有、2 s。
- `POST /api/endfield/reload`（mutate）。
- `/api/endfield/key*`：#38 五接口。
- 不提供绑定/订阅写接口、不提供手动签到/同步接口。

### 10.4 日志/告警

- `logging.getLogger("endfield")`；字段：event/endpoint/status/code/latency/attempt/user_hash/role_hash；不记 token/key/完整 QQ/role_id。
- `audit.jsonl` 1 MB 轮转 ×2；面板尾部 50。
- 429/524 计数；不写原始 HTML。
- 主人告警：心跳离线 >3 min 或连续 3 次失败 → 私聊 1 次/小时；不发群。

---

## 11. 安全、许可与合规

### 11.1 许可

- 同源移植：`client.py`、`store.py`、`render.py`、16 模板/资源 = **AGPL-3.0-only**。
- 新写：`logic.py`、`config.py`、`scheduler.py`、`plugins/endfield.py`、dashboard 增量 = MIT。
- 根 `LICENSE-AGPL-3.0`、`docs/third-party/endfield-NOTICE.md`、README 声明；分发组合按 AGPL。
- 官方 NoneBot 插件：许可待运营方澄清，**不复制代码**；只参考协议/设计。
- 上游 AstrBot：钉 v3.0.3 `41482b7`；README 记录来源/许可；不向上游提 PR。

### 11.2 凭据/数据

- `api_key`：`data/endfield_secrets.json` 0600；面板 masked；env fallback；不进日志/git/issue。
- `framework_token`：`bindings.json` 0600 明文；不加密（决策）；不进日志/群/面板 HTML；备份警告。
- 绑定列表按会话 QQ 查询；不透传任意 `user_identifier`。
- `render_url` 仅 loopback/私有；防 SSRF；非 http(s) 拒绝。
- 第三方告知：首次绑定私聊一次性告知，回复「同意」才继续；存 `consent_version/at`。
- 运营方条款/留存未知：需书面确认；文案不承诺超出已知范围。

### 11.3 依赖

| 依赖 | 用途 | 状态/许可 |
|---|---|---|
| `httpx` | 客户端 | 已在锁文件；BSD |
| `jinja2` | 模板渲染 | 需确认/显式加入；BSD |
| `qrcode` | 扫码二维码（二期） | 可选新增；BSD |
| `playwright`（Node） | 远程渲染器容器 | 已有 `docker/playwright-renderer/`；MIT 容器，模板 AGPL |
| `Pillow` | 不需要（不本地出图） | 不新增 |

- 不引入 Python Playwright、不装 Chromium、不接远程 T2I。

---

## 12. 测试与验收

### 12.1 分层

| 层 | 内容 | 默认 |
|---|---|---|
| L1 纯逻辑 | 格式化/分页/mask/consent/错误映射/订阅判定/缓存键 | 离线 |
| L2 配置/存储 | 默认值/校验/mtime/CRUD/3 上限/primary/去重/invalid/原子写 | 离线 |
| L3 客户端 | MockTransport：200/401/40301/400/401/429 HTML/524/404/超时 | 离线 |
| L4 接线 | `_bootstrap`；priority/block；群 @/私聊；绑定仅私聊；配额/防抖 | 离线 |
| L5 渲染 | 视图/自包含 HTML/≤2MB/2400/`POST /render` mock/回退 | 离线 |
| L6 面板 | TestClient；auth/CSRF/配置/脱敏/audit/心跳 | 离线 |
| L7 调度 | 假时钟/RecordingSink；去重/flag/静默/限额/签到/429 | 离线 |
| L8 E2E | 假 OneBot → matcher → MockTransport → store → fake 渲染 → 回复 | **默认** |
| L9 live | 公开只读 + dev server 健康 | `ENDFIELD_LIVE_TESTS=1` |
| L10 HITL | 测试账号真实绑定/查询/签到/解绑 | 人工 |

### 12.2 运行

```bash
cd nonebot && pytest -q
cd nonebot && pytest -q -m "not live"

cd nonebot && ENDFIELD_LIVE_TESTS=1 \
  ENDFIELD_LIVE_RENDER_URL=http://127.0.0.1:3100 \
  ENDFIELD_LIVE_DASHBOARD_URL=http://127.0.0.1:8080 \
  pytest -q -m live     # 在 10.0.0.226 本机跑；renderer 仅回环
```

- 请求 5–10 s 超时；CI job 10 min；`--maxfail=1`；不新增测试依赖。
- 测试不读 `secrets`；live 用 env 临时注入；不打印。
- `3100` 只绑 `127.0.0.1`，外网/CI 不能直连；远程调试用 `ssh -L 3100:127.0.0.1:3100`，或直接在服务器上跑。

### 12.3 分阶段 must-pass

- 一期：绑定/同意/取消；3 上限；primary；8 命令；群 @ 自查；绑定私聊；无 token/role_id 泄露；store 0600/原子。
- 二期：2a/2b 全部 fixture/空态/错误；图卡 + 回退；分页；缓存；同步私聊 1 次/天。
- 三期：公告去重；理智/券满/回落；静默；日限额；3 次禁用；opt-in 签到全部绑定；心跳/status/audit。
- E2E：离线全绿；live 仅 gate；HITL 清单一次。

---

## 13. 部署与运行

### 13.1 开发服务器

- `10.0.0.226`：**22 SSH**；**3000 personal-wiki（登录页，非渲染器）**；**3100 renderer（宿主机模式，仅 127.0.0.1）**；**8080 uvicorn（dashboard/服务入口，路径待确认）**；80 关闭；bot/dashboard/renderer 同机。
- bot 在 dev server：`render_url=http://127.0.0.1:3100`。
- 外网/CI：**3100 不可直连**（只绑回环）；live render 在服务器上跑，或 `ssh -L 3100:127.0.0.1:3100` 建隧道。
- `3000` 是 personal-wiki，不是渲染器；不要把 endfield 的 render_url 指到 `10.0.0.226:3000`。
- 只绑回环/私网；不暴露公网；不加 render token（当前客户端不发送；跨机时再加）。

### 13.2 启动顺序

1. 宿主机模式（dev server）：`HOST=127.0.0.1 PORT=3100 ... node server.js`；`curl http://127.0.0.1:3100/health` → ready。Docker compose 默认绑 `127.0.0.1:3000`，但 dev server 的 3000 是 personal-wiki，不要占用；独立机器才用 3000。
2. bot（现有服务/进程）加载 `nonebot/plugins/endfield.py`；`data/endfield.json` 生效，`render_url` 指向 `http://127.0.0.1:3100`。
3. dashboard（现有 uvicorn）打开 `http://10.0.0.226:8080`；终末地页（路径以现有面板为准）。
4. 面板填 API Key / 测试连接；首次绑定私聊；`终末地诊断`。

### 13.3 配置/文件

- `.env`：不新增 key（key 由面板/secret 管理）；如需 fallback 才加 `ENDFIELD_API_KEY`。
- `data/endfield.json`、`data/endfield_secrets.json`、`data/endfield/bindings.json`、`subscriptions.json`、`heartbeat.json`、`audit.jsonl`。
- 备份警告：bindings 含明文 token；不提交 git；迁移时注意权限。
- 文档：README、`docs/OPERATIONS.md`、`docs/PITFALLS.md`、NOTICE。

---

## 14. 实现顺序（里程碑）

### M0 脚手架/许可/配置/存储

- 分支 `feat/endfield`；`nonebot/endfield/` 骨架；`LICENSE-AGPL-3.0`、NOTICE、README 声明。
- `config.py` + `endfield.json` 默认/校验/mtime；`store.py` 绑定/订阅 schema + 原子写；`tests/_bootstrap.py` + fixtures 目录。
- 验收：配置/存储单测绿；AGPL 文件头齐全。

### M1 MVP（一期 8 命令，纯文本）

- `client.py` 登录/绑定/note/stamina/attendance 子集；`plugins/endfield.py` matcher/权限/防抖/配额；`logic.py` 文本/consent/mask。
- 面板 Key 可暂用 env fallback；`终末地诊断` 可选。
- 验收：绑定/同意、8 命令、群 @ 自查、绑定私聊、无泄露；离线 E2E。

### M2 渲染 + 2a 核心 6

- `render.py` 模板/内联/自包含 HTML + `POST /render`；16 模板自包含化（先 6 张 + 帮助/绑定/理智/便签）。
- 抽卡记录/分析、干员列表/面板、危机合约、日历；`同步抽卡`/`同步面板`。
- 验收：图卡/文本回退；2MB/2400；429/超时；分页；缓存。

### M3 2b 扩展 4

- 成就、帝江号、地区、全服统计；模板自包含化。
- 验收：查询矩阵完整；全服统计公开缓存；国际服 provider。

### M4 订阅/自动签到/调度

- `scheduler.py` tick/公告/理智/券/签到/心跳/审计；`subscriptions.json`；`PushSink`。
- 公告群（superusers）、个人私聊、静默/限额/失败禁用；opt-in 签到全部绑定。
- 验收：假时钟/RecordingSink 全绿；心跳/告警。

### M5 面板/运维/文档

- 终末地页（Key/状态/绑定/订阅/配置/运维）；`/api/endfield/*`；日志脱敏/audit；README/OPERATIONS/PITFALLS。
- 验收：TestClient；status 脱敏；配置热加载；面板只读数据。

### M6 测试/实测/收尾

- 离线全量 E2E；live smoke 在 `10.0.0.226` 本机跑（renderer 仅回环，端口 3100）；手工 HITL 绑定/查询/签到/解绑；视觉逐模板验收。
- 验收：`pytest -q` 全绿；live gate 通过；无秘密泄露；文档完整。

---

## 15. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 网关字段漂移/无契约 | 查询/图卡失败 | 双份盘点 + 只读探测；fixture 回归；错误回退文本；持续观察 |
| 账号风控/ToS/自动签到 | 封号/合规 | 保守频率；opt-in；不代签未知账号；运营方确认；失败告警 |
| AGPL 边界 | 分发合规 | 文件头/LICENSE/NOTICE；上游钉版本；官方插件不复制 |
| 渲染器 2MB/2400/2 并发 | 图卡失败/排队 | 自包含压缩/分页/缩放；文本回退；必要时调 MAX_BODY/并发；缓存 |
| 429/524 | 查询失败/封禁 | 串行/缓存/60s 退避；不重试写；监控计数 |
| OneBot 私聊不可达 | 个人提醒失败 | 私聊优先；3 次禁用；不群回退；用户手动查询 |
| 真实 token 生命周期未实测 | 绑定/失效处理偏差 | M6 HITL 一次；失败标 invalid；不自动删 |
| 官方 NoneBot 许可冲突 | 不能复用其代码 | 只参考；不复制；需运营方书面澄清 |
| 上游持续更新 | 功能差距 | 钉 v3.0.3；diff 策略；不追 HEAD |
| 视觉验收未做 | 图卡错位 | 逐模板人工看一次；HTML 快照；文本兜底 |

---

## 16. 未决问题与后续 ticket

1. **运营方条款/数据保留/SLA**：需书面确认（合规）。
2. **真实 framework token 生命周期**：M6 用测试账号做一次 HITL 绑定/查询/签到/解绑；不写 issue。
3. **渲染器限制适配**：逐模板验证 2MB/2400/2 并发；长公告分页方案。
4. **视觉验收**：16 模板逐张人工确认；字体/布局偏移修正。
5. **官方 NoneBot 插件许可**：向运营方要书面澄清/双授权；在此之前不复制。
6. **dev server 8080 面板登录方式**：实现时对齐现有 dashboard 会话；live 只 GET `/`。
7. **测试账号区服/角色**：绑定实测时记录（不进 issue 的敏感值）。
8. **API 字段漂移**：版本更新后回归 fixture；必要时补端点。
9. **扫码二维码生成**：二期用 `qrcode` 库，不走 Playwright；确认 QR 端点字段。

后续 ticket 建议（实现阶段另开）：
- `impl-1` M0/M1 脚手架+MVP；
- `impl-2` 渲染+2a；
- `impl-3` 2b；
- `impl-4` 订阅/签到；
- `impl-5` 面板/运维；
- `hitl-1` 真实绑定/签到；
- `license-1` 官方插件许可澄清；
- `visual-1` 16 模板视觉验收。

---

## 17. 附录

### 17.1 决策票索引

| 票 | 结论 | 底稿 |
|---|---|---|
| #2 | 上游 52 端点/31 注册点；AGPL；`core/` 可搬、`main.py` 重写 | `endfield-upstream.md` |
| #3 | 接入面：gate 不覆盖自研 matcher；纯逻辑+接线；0600；asyncio | `endfield-integration.md` |
| #5 | key 就绪；公开端点 200/code 0 | — |
| #6 | 18 项只读探测；401/40301/400；429 HTML；524；token 生命周期 | `endfield-api.md` |
| #8 | 一期 8 命令；群 @/私聊；绑定私聊；5s 防抖 | — |
| #10 | bindings 0600；3 个+primary；invalid 保留；consent；面板脱敏 | — |
| #12 | `nonebot/endfield/` + `plugins/endfield.py`；AGPL 隔离；25s/GET 重试 1 | — |
| #14 | 2a 6 + 2b 4；图卡优先；6 个月；手动同步；20/页 | — |
| #16 | 四类订阅；10/20/20/00:05；静默；asyncio tick；opt-in 签到 | — |
| #17 | 16 模板；自包含 HTML；Noto CJK；PNG；回退 | — |
| #18 | 终末地页；配置/Key 写、数据只读；status/心跳/audit | — |
| #19 | 全量 E2E 离线；live gate；10.0.0.226；must-pass | — |
| #34 | 官方 NoneBot 许可冲突/缺资源/功能一半 → 只参考 | `endfield-nonebot-upstream.md` |
| #35 | AstrBot 基线 + 远程渲染 | — |
| #38 | Key 面板化；secrets 0600；masked；mtime | — |

### 17.2 交付物清单

- 本方案：`docs/research/endfield-port.md`
- 研究底稿：`docs/research/endfield-{upstream,integration,nonebot-upstream,api}.md`
- 实现文件（另开）：`nonebot/endfield/*`、`nonebot/plugins/endfield.py`、`nonebot/dashboard.*`、`nonebot/tests/endfield/*`
- 许可：`LICENSE-AGPL-3.0`、`docs/third-party/endfield-NOTICE.md`
- 运维：`README.md`、`docs/OPERATIONS.md`、`docs/PITFALLS.md`

### 17.3 一句话

**按 AstrBot 的功能面移植、按 denia 的工程约定重写胶水、用现有远程渲染出图；先 MVP 文本，再 2a/2b 查询，最后订阅/签到/面板；AGPL 边界、429/524、私聊可达性、真实 token 实测是四个必须盯住的风险。**

---

## 18. 原实现分支快照（2026-10-31，接手修复与合并前）

- 分支：`feat/endfield-impl`（隔离 worktree，基于 `0a33293`）；**未合入主工作区**（主工作区有未提交改动）。
- 提交：`ef0f974` M0 → `32f2d6a` M1 → `9f93f92` M2a → `b31aa50` M2b → `2a2f858` M3 → `79ff343` M4 → `216f085` M5 → `25c2ab0` M6。
- 测试：`110 passed, 1 skipped`（`tests/endfield` + `tests/test_dashboard.py`）。
- live 只读：真实公告 `item_id=6431975`、日历 93 条活动；未打印 Key。
- 覆盖：配置/存储/客户端/一期 8 命令/2a 6 条/2b 4 条/公告/订阅/60s 调度/心跳/审计/自包含图卡/面板 API+`/endfield` 页/文档。
- 未覆盖/限制：
  - 图卡用自包含通用卡片（MIT），**未搬上游 16 套 AGPL 视觉模板**；
  - 真实 renderer 在 `127.0.0.1:3100`，外网不可达，live 图卡未在本机验证；
  - 真实绑定/扫码/自动签到未做 HITL；
  - 面板 `/endfield` 已可用，但未并入 dashboard 主导航；主分支未合并。
- 当时的合并建议已由本次隔离工作树审查整合替代；不要据此切换或覆盖含未提交改动的工作区。
