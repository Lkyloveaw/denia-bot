# 协议终端 API 契约、错误语义与风控边界

- 调研票：wayfinder #6（denia-bot endfield 移植）
- 调研日期：2026-10-31
- 上游快照：`astrbot_plugin_endfield` v3.0.3，commit `41482b7`，`core/client.py`（659 行）
- 对照材料：`docs/research/endfield-upstream.md`（#2，52 端点清单）、`docs/research/endfield-nonebot-upstream.md`（#34，官方 NoneBot 插件）
- 实测凭据：`api_key` 位于 `nonebot/data/endfield_secrets.json`（0600，已 gitignore）；本文**不记录明文、前缀或长度**
- 实测范围：仅 GET 只读；**未执行**绑定、扫码确认、签到、抽卡同步、面板同步、删除/改 primary 等写操作
- 网络环境：`end-api.shallow.ink` 解析到 `198.18.4.25`（代理段）；PowerShell `System.Net.Http.HttpClient`，TLS 1.2

---

## 0. 结论速览

| # | 项目 | 结论 | 证据 |
|---|---|---|---|
| 1 | 认证 | `X-API-Key` 是所有业务端点的前提；缺失或格式错误均为 **HTTP 401 / code 401** | 探测 9、10 |
| 2 | 用户凭证 | `X-Framework-Token` 是绑定后的用户凭证；缺失/无效在不同端点返回 **403/40301、400/400、401/401** 三种形态 | 探测 6、7、13–15 |
| 3 | 限流 | 响应带 `x-ratelimit-limit`（300 或 600）与 `x-ratelimit-remaining`；实测约 14 次请求/3 分钟内触发 **429**，body 是 **HTML**，无 `Retry-After` | 探测 18 |
| 4 | 慢端点 | `gacha/global-stats` 正常约 2.2 s（缓存）；带 `provider` 参数实测 **31 s → 524**（空 body）。25 s 客户端超时会先断开 | 探测 4、12 |
| 5 | 大响应 | 公告列表 344 KB、卡池 254 KB、Wiki 活动 49 KB；必须分页/缓存，不能每消息拉全量 | 探测 2、5、3 |
| 6 | 绑定列表 | `GET /api/v1/bindings?user_identifier=...` **只需 API key**，不需要 framework token；未知用户返回空列表 | 探测 8 |
| 7 | 错误处理 | 上游 client 把错误吞成 `None`，且只对 403 识别 token 失效；**漏掉 401/401「无效的凭证」** | `core/client.py:81-163`；探测 15 |
| 8 | 数据边界 | 网关可见 API key、framework token、bot/用户 QQ（`client_id`/`platform_id`）、role/server id 与查询内容；仓库内**没有**运营方条款/留存说明 | #2 研究；本票未获得条款 |

---

## 1. 方法与证据

### 1.1 静态核对

- 逐行读 `core/client.py`：header 组装、请求封装、错误吞噬、403 失效判断、登录/绑定/查询方法。
- 对照 #2 的 52 端点表与 #34 的官方 NoneBot 实现，确认两端点集高度重叠、同一网关。
- 官方 NoneBot 的 `lib/api.py` 只覆盖 22 个路径；本票以 AstrBot 的 52 个方法为全集，只对本期需要的子集做实测。

### 1.2 实测约束

- 只发 GET；不调用任何 `POST`/`PUT`/`DELETE`。
- 不触发 `/api/endfield/gacha/fetch`、`/api/panel/sync`、签到、绑定、删除、改 primary。
- 测试账号已具备，但**没有**做网页授权/扫码绑定，因此没有真实 framework token；token 相关结论来自「缺 token / 假 token」的只读错误探测 + 静态代码。
- key 仅在进程内读取，未写入文档、日志或命令行输出。

### 1.3 探测轮次

- 第一轮：公开端点 + 缺 token + 无 key/错 key + 404。
- 第二轮：`global-stats` 重试、慢端点、假 token 的 403/401 差异、`search/chars` 公开性、额外 caller header。
- 第三轮：原始响应头分桶；在同一轮第二次请求 `pool-chars` 时触发 429。

完整记录见附录 A。

---

## 2. Header 语义

上游 `core/client.py:36-49` 的 header 组装：

| Header | 是否必需 | 语义 | 实测行为 | 备注 |
|---|---|---|---|---|
| `X-API-Key` | **所有端点必需** | 机器人/网关接入密钥；一个 bot 一个 key | 缺失 → 401/401；格式错 → 401/401「API Key 无效」 | key 是 bot 级共享，不是用户级；绝不能下发给用户或写日志 |
| `X-Framework-Token` | 游戏数据/绑定类必需 | 用户完成授权/扫码后的框架 token | 缺失 → 403/40301 或 400/400；假 token → 403/40301 或 401/401 | **无刷新机制**；失效只能重新授权/扫码 |
| `client_id` | 建议 | 上游填 bot QQ | 公开端点不带也能通；游戏数据用于上下文 | 上游单例 `set_caller` 只有 2 处调用，导致串号；新 client 必须每请求显式传 |
| `platform_id` | 建议 | 上游填用户 QQ | 同上 | 多用户并发时不能共用可变单例 |
| `platform` | 建议 | 上游固定 `bot` | 公开端点不带也能通 | 游戏数据建议带 |
| `Authorization: Bearer` | 可选 | 401 文案提到的替代凭证 | 未探测 | 机器人场景不需要 |
| `X-Anonymous-Token` | 可选 | 401 文案提到的匿名令牌 | 未探测 | 机器人场景不需要 |
| `Content-Type` | 写请求需要 | 上游固定 `application/json` | GET 不带也通 | 写请求必须带 |

**结论**：

- `X-API-Key` 是唯一的全局凭证；`X-Framework-Token` 是每用户/每绑定凭证。
- `client_id`/`platform_id` 不是公开端点的硬要求，但游戏数据端点应有明确 caller 上下文；不要复用「全局单例 + set_caller」模式。
- 401 文案列出三种认证方式，说明网关支持 API key / Bearer / 匿名令牌；本期只用 API key。

---

## 3. 端点覆盖矩阵

完整 52 个方法见 #2 的端点表；这里按「鉴权要求 + 本期是否需要 + 是否实测」分组。

### 3.1 公开（API key only）

| 端点 | 方法 | 实测 | 说明 |
|---|---|---|---|
| `/api/announcements/latest` | GET | ✅ 200/0 | 最新公告；13 KB 级；适合公告订阅轮询 |
| `/api/announcements` | GET | ✅ 200/0 | 公告列表；`page`/`page_size`；实测 344 KB（`limit=2` 仍很大，字段多） |
| `/api/announcements/:id` | GET | 未测 | 公告详情；用 `item_id`/`id` 待确认 |
| `/api/wiki/activities` | GET | ✅ 200/0 | 活动列表；49 KB |
| `/api/wiki/search` | GET | 未测 | `q`/`page`/`page_size` |
| `/api/wiki/items` | GET | 未测 | 参数由调用方拼；#2 有清单 |
| `/api/wiki/items/:id` | GET | 未测 | 物品详情 |
| `/api/endfield/gacha/global-stats` | GET | ✅ 200/0（无参） | 2.2 s；带 `provider` 参数实测 31 s → **524** |
| `/api/endfield/gacha/pool-chars` | GET | ✅ 200/0；随后 429 | 254 KB；限流敏感 |
| `/api/v1/bindings` | GET | ✅ 200/0 | **只需 API key + `user_identifier`**；未知用户返回 `{"bindings":[],"count":0}` |

### 3.2 需要 framework token（只读）

| 端点 | 方法 | 缺 token 实测 | 无效 token 实测 | 说明 |
|---|---|---|---|---|
| `/api/endfield/note` | GET | 403/40301「缺少游戏数据凭证…」 | 403/40301「无效的 Framework Token 或会话已过期…」 | 便签/理智等 |
| `/api/endfield/stamina` | GET | 同 note | 同 note（本次 7.6 s，偏慢） | 理智 |
| `/api/endfield/search/chars` | GET | 403/40301 | 未测 | **不是公开端点**；需 token |
| `/api/endfield/gacha/records` | GET | **400/400「缺少 framework_token」** | 未测 | 错误形态与 note 不同 |
| `/api/endfield/gacha/stats` | GET | 未测 | **401/401「无效的凭证」** | 错误形态与 note 不同；上游 403-only 判断会漏 |
| `/api/endfield/gacha/accounts` | GET | 未测 | 未测 | 抽卡账号 |
| `/api/endfield/card/*`、`/card/crisis-contract`、`/card/char`、`/card/achievement`、`/card/calendar` | GET | 未测 | 未测 | 角色卡/合约/成就/日历 |
| `/api/endfield/area/*` | GET | 未测 | 未测 | 地区/帝江号建设 |
| `/api/panel/chars`、`/panel/char/:id`、`/panel/sync/status` | GET | 未测 | 未测 | 面板同步；`/panel/sync` 是写 |

### 3.3 登录/绑定（未执行写操作）

| 端点 | 方法 | 用途 | 备注 |
|---|---|---|---|
| `/login/endfield/qr` | GET | 取扫码登录二维码 | 返回内容待页面/客户端确认 |
| `/login/endfield/qr/status` | GET | 轮询扫码状态（`framework_token` 参数） | 未测 |
| `/login/endfield/qr/confirm` | POST | 确认扫码并绑定 `user_identifier` | **写**；未执行 |
| `/login/endfield/phone/send` | POST | 手机号发送验证码 | **写**；未执行 |
| `/login/endfield/phone/verify` | POST | 手机号验证码校验 | **写**；未执行 |
| `/login/skport/password` | POST | 国际服邮箱密码登录 | **写**；未执行 |
| `/api/v1/authorization/requests` | POST | 创建网页授权请求（scopes） | **写**；返回 `auth_url`（相对路径需拼 `https://end.shallow.ink`） |
| `/api/v1/authorization/requests/:id/status` | GET | 轮询授权状态 | 未测 |
| `/api/v1/bindings` | POST | 用 `framework_token` 创建绑定 | **写**；未执行 |
| `/api/v1/bindings/:id` | DELETE | 删除绑定 | **写**；未执行 |
| `/api/v1/bindings/:id/primary` | POST | 设主绑定 | **写**；未执行 |

### 3.4 写操作/本期不碰

- `/api/endfield/gacha/fetch`（抽卡同步）、`/api/endfield/gacha/simulate/*`（模拟抽卡，非真实数据）。
- `/api/panel/sync`、`/api/enduid/set-primary`、`/api/maaend/*`。
- 签到/领取类端点（#2 已列；**本票未执行**）。

---

## 4. 错误语义表

| HTTP | code | message（实测原文） | 触发条件 | 分类 | 建议动作 |
|---|---|---|---|---|---|
| 200 | 0 | `成功` | 正常 | 成功 | 取 `data` |
| 200 | ≠0 | 未观测 | 业务失败 | 协议/业务错误 | 记录 code/message；不重试；按端点提示 |
| 401 | 401 | `请提供有效的认证凭证: API Key (X-API-Key) / 访问令牌 (Authorization: Bearer) / 匿名令牌 (X-Anonymous-Token)` | 无 `X-API-Key` | 配置错误 | 不重试；面板提示「配置 API Key」 |
| 401 | 401 | `API Key 无效: 无效的 API Key 格式` | key 格式错 | 配置错误 | 不重试；提示重新填写 |
| 401 | 401 | `无效的凭证` | `gacha/stats` 无效 framework token | 用户授权失效 | 清本地绑定；提示重新授权/扫码 |
| 403 | 40301 | `缺少游戏数据凭证，请在请求头中提供 X-Framework-Token` | note/stamina/search-chars 缺 token | 未绑定/未授权 | 提示绑定；不重试 |
| 403 | 40301 | `无效的 Framework Token 或会话已过期，请重新登录获取` | note/stamina 假/过期 token | 用户授权失效 | 清本地绑定；提示重新授权/扫码；不循环重试 |
| 400 | 400 | `缺少 framework_token` | gacha/records 缺 token | 未绑定/未授权 | 提示绑定；不重试 |
| 404 | — | `404 page not found`（`text/plain`） | 路径错 | 协议错误 | 不重试；修端点 |
| 429 | — | HTML（实测 4259 bytes，非 JSON） | 约 14 请求/3 分钟 | 限流/WAF | 退避 ≥60 s + jitter；最多重试 1 次；不要立即循环 |
| 524 | — | 空 body，约 31 s | `global-stats?provider=skland` | 网关/源站超时 | 视为 transient；缓存/降级；不要长超时死等 |
| — | — | 连接错误/超时 | 网络/代理 | transient | GET 可重试 1 次；写不重试 |

**关键差异**：

- 同一语义「token 缺失/无效」在不同端点返回 **400/400、401/401、403/40301** 三种形态。客户端不能只判断 403。
- 429 的 body 是 HTML，`resp.json()` 会抛异常；必须先看 HTTP status，再决定是否解析 JSON。
- 200 + `code != 0` 本次未观测，但上游 `core/client.py:99-105` 表明存在这种分支；客户端必须统一处理，不能假设 HTTP 200 就是成功。

**上游缺陷（#12 要修的）**：

- `core/client.py:81-145` 把所有异常吞成 `None`，调用方无法区分「未绑定」「限流」「网络错」「端点错」。
- `core/client.py:147-163` 的失效判断只看 **403** 和一组中文/英文 marker；实测 `gacha/stats` 返回 **401/401「无效的凭证」**，不会触发清理回调，会留下坏绑定。
- 无重试、无 429 处理、无 `Retry-After` 读取、无响应 code 类型建模。

---

## 5. 限流、超时与重试

### 5.1 观测

- 公开端点响应头：`x-ratelimit-limit: 300`（announcements/wiki/note）或 `600`（gacha pool-chars/bindings）；`x-ratelimit-remaining` 递减（599、598、597、595…）。
- `limit` 与 `remaining` 数值不自洽（如 300/599），说明可能来自不同分桶/层；**不要用 `limit` 推断剩余**，只把 `remaining` 当提示。
- 实测 429：在约 14 次请求、约 3 分钟内，对 `pool-chars` 的第二次请求返回 429；响应头只有 `Date`/`Server: ESA`，**没有 `Retry-After`/`X-RateLimit-*`**；body 为 `text/html`（4259 bytes）。无法确认是 per-key、per-IP 还是边缘 WAF。
- 慢端点：`global-stats` 无参约 2.2 s（`cached=true`）；带 `provider=skland` 实测 31 s → **524**，空 body。25 s 客户端会先超时，因此该端点需要更长超时或缓存兜底。
- 其他端点响应时间：0.7–3.1 s；`stamina` 假 token 本次 7.6 s。

### 5.2 建议

- **串行/低并发**：bot 侧用 `asyncio.Semaphore(2–4)`；不要按消息并发打满。
- **缓存**：
  - 公告最新 60 s；公告列表 5 min；Wiki 活动 10 min；卡池 10 min；global-stats 5 min。
  - 用户数据（note/stamina/角色卡）不跨用户缓存；同一用户 15–30 s 短缓存可减重复查询。
  - 抽卡记录按 `role_id` 缓存 1–5 min；全量分页 500/页。
- **429**：读不到 `Retry-After` 时，固定退避 **60 s + jitter**，最多重试 1 次；仍 429 则返回「稍后再试」，不要循环。
- **超时**：默认 25 s（#12）；`global-stats` 单独 60 s，或用缓存值直接返回；524/超时按 transient，GET 重试 1 次。
- **重试边界**：GET 在 timeout/502/503/504/524 可重试 1 次；401/403/400/404/429 不重试；所有写操作不重试。
- **熔断**：连续 429/524 时暂停该端点 5–10 min，避免把 key/IP 打进更长的封禁。

---

## 6. Framework Token 生命周期与多账号

### 6.1 获取路径（静态）

1. **网页授权**：`POST /api/v1/authorization/requests`，body：
   ```json
   {"client_id": "<bot identity>", "client_name": "<显示名>", "client_type": "bot",
    "scopes": ["user_info","binding_info","game_data","attendance"]}
   ```
   返回 `auth_url`（相对路径时拼 `https://end.shallow.ink`）与 request id；用户打开授权；轮询 `GET /api/v1/authorization/requests/:id/status`。
2. **扫码**：`GET /login/endfield/qr` → 用户扫码 → `GET /login/endfield/qr/status?framework_token=...` → `POST /login/endfield/qr/confirm`（`framework_token`、`user_identifier`、`platform=bot`）。
3. **手机号**：`POST /login/endfield/phone/send` → `POST /login/endfield/phone/verify`。
4. **国际服**：`POST /login/skport/password`（email/password）。
5. **绑定**：`POST /api/v1/bindings`，body 含 `framework_token`、`user_identifier`、`client_type=bot`、`client_id=bot-<user_id>`、`is_primary`、`provider=skland`；可重复绑定多账号。

### 6.2 失效与刷新

- **没有刷新接口**；token 是长期凭证，失效只能重新授权/扫码。
- 失效信号（实测）：
  - note/stamina：403/40301「无效的 Framework Token 或会话已过期…」
  - gacha/stats：401/401「无效的凭证」
  - 其他端点可能用 400/400 或 403/40301；客户端要按 message 归类，而不是只按 status。
- 处理：清本地该 `framework_token`/绑定；提示用户重新授权；对同一 token 去重，避免每个 matcher 都打一次。
- 多账号：以 `user_identifier`（QQ）为隔离键；一个 QQ 可有多条 binding，`is_primary` 标记主账号；切换走 `POST /api/v1/bindings/:id/primary`（写，未测）。
- **绑定列表**：`GET /api/v1/bindings?user_identifier=<QQ>&client_type=bot&provider=skland` **只需 API key**；返回 `bindings` 数组。未知用户返回空。安全含义：持有 bot API key 的人可以按任意 `user_identifier` 查列表；bot 侧必须只用自己的 QQ 映射，**不能**把用户输入的任意 ID 直接透传。

### 6.3 Caller 上下文

- 上游 `set_caller` 是单例可变状态，`core/client.py:28-31`，只有 2 处调用；并发时会串号。
- 新 client：`client_id`（bot QQ）/`platform_id`（用户 QQ）作为**每次请求参数**显式传入；不要用全局单例保存 caller。
- 公开端点不带 caller 也能通，但游戏数据端点建议带，便于网关审计与风控。

---

## 7. 数据留存与第三方可见范围

网关 `https://end-api.shallow.ink` 会看到：

- `X-API-Key`（bot 级）
- `X-Framework-Token`（用户级）
- `client_id`/`platform_id`（bot/用户 QQ）
- `roleId`/`serverId`、抽卡/角色/便签/理智等查询内容
- 请求时间、IP、UA

仓库内**没有**运营方主体、服务条款、隐私政策、数据保留期限的说明；#5 也未拿到。**需运营方书面确认**：

1. 数据保留多久、是否用于训练/分析、是否与第三方共享；
2. API key 的权限范围/配额/封禁策略；
3. 429/524 的官方语义与推荐退避；
4. 是否允许 bot 长期轮询公告/订阅；
5. 用户删除数据/撤销授权的路径。

在确认前，bot 侧应：

- 只发必要字段；不把 QQ 号/role id 写日志；
- 不把 key/token 写 issue、仓库、错误消息；
- 面板只显示 masked；日志只记 status/code/端点；
- 绑定列表接口只在服务端按会话 QQ 查询，不接受任意 `user_identifier`。

---

## 8. 对 denia 客户端的落地建议（供 #12 细化）

### 8.1 异常层级

```text
EndfieldError
├─ EndfieldConfigError        # 缺 API key / key 格式错（401/401）
├─ EndfieldAuthError          # 未绑定 / token 缺失或失效（400/400、401/401、403/40301）
├─ EndfieldRateLimitError     # 429（含 HTML body）
├─ EndfieldTransientError     # timeout / 5xx / 524 / 连接错误
└─ EndfieldProtocolError      # 404、非 JSON、200+code!=0、字段缺失
```

每个异常带 `status`、`code`、`message`、`path`；日志只输出这些，不输出 key/token。

### 8.2 请求层

- `httpx.AsyncClient(timeout=25.0)`；`global-stats` 用 60 s 或缓存；
- 每请求显式 header：`X-API-Key`、`client_id`、`platform_id`、`platform: bot`；有 token 时加 `X-Framework-Token`；
- 成功判定：HTTP 2xx **且** JSON `code == 0`；取 `data`；
- 429：不解析 JSON；退避 60 s + jitter，最多 1 次；
- GET 重试 1 次（timeout/502/503/504/524）；写不重试；
- 分页：公告 `page/page_size`；抽卡 `page/limit(≤500)`；Wiki `page/page_size`；
- 并发：`Semaphore(2–4)`；同一用户/同一 token 串行，避免 token 失效风暴。

### 8.3 测试矩阵（MockTransport）

| 场景 | 响应 | 期望 |
|---|---|---|
| 正常公告 | 200 `{"code":0,"data":...}` | 返回 data |
| 缺 key | 401 `{"code":401,...}` | `EndfieldConfigError` |
| 错 key | 401 `{"code":401,"message":"API Key 无效..."}` | `EndfieldConfigError` |
| 缺 token | 403 `{"code":40301,...}` | `EndfieldAuthError` |
| 无效 token | 403/401 + message | `EndfieldAuthError` + 触发清理一次 |
| gacha 缺 token | 400 `{"code":400,...}` | `EndfieldAuthError` |
| 429 HTML | 429 `text/html` | `EndfieldRateLimitError`；退避；最多 1 次 |
| 524 空 body | 524 空 | `EndfieldTransientError`；GET 重试 1 次 |
| 404 text | 404 `text/plain` | `EndfieldProtocolError`；不重试 |
| 200 code!=0 | 200 `{"code":500,...}` | `EndfieldProtocolError`；不重试 |

### 8.4 缓存建议（起步值）

| 数据 | TTL | 备注 |
|---|---|---|
| 公告最新 | 60 s | 订阅轮询用 |
| 公告列表 | 5 min | 344 KB，必须分页 |
| Wiki 活动 | 10 min | 49 KB |
| 卡池 | 10 min | 254 KB |
| global-stats | 5 min | 2.2 s；带 provider 可能 524 |
| 抽卡记录 | 1–5 min/role | 500/页 |
| note/stamina | 15–30 s/用户 | 不跨用户 |
| 角色卡/成就/日历 | 5–10 min/role | 变化慢 |

---

## 9. 未解问题

1. **运营方条款/数据保留/SLA**：仓库内无；需书面确认（影响合规与长期可用性）。
2. **429 的准确语义**：per-key / per-IP / WAF？恢复时间？无 `Retry-After`，只能保守退避。
3. **`global-stats?provider=skland` 的 524**：是否参数错误、源站慢、还是该 provider 无数据？需要运营方/重试确认。
4. **绑定列表无 token**：API key 持有者可按 `user_identifier` 查绑定；这是网关设计还是漏洞？bot 侧必须自守。
5. **真实 framework token 生命周期**：本票未做绑定/扫码写入，token 刷新/撤销/多账号切换未实测；需要一次授权的 HITL 测试（不在此票范围）。
6. **`Authorization: Bearer` / `X-Anonymous-Token`**：401 文案提到，未探测用途。
7. **API key 的 scope/配额**：是否所有端点同一 key、是否有端点级限额，未知。
8. **`client_id`/`platform_id` 在游戏数据端点的硬性**：公开端点不带可通，游戏数据未验证。

---

## 附录 A：只读探测记录（2026-10-31）

> 所有请求均带 `X-API-Key`（除注明 no-key/bad-key）；未带真实 framework token；key 已打码，不在本文出现。

| # | 探测 | 方法/路径 | HTTP | code | message/结果 | 大小 | 耗时 | 备注 |
|---|---|---|---|---|---|---|---|---|
| 1 | latest-valid | GET `/api/announcements/latest` | 200 | 0 | 成功 | 13,447 | 2,073 ms | limit=300, remaining=599 |
| 2 | announcements-list | GET `/api/announcements?page=1&limit=2` | 200 | 0 | 成功 | 344,261 | 2,848 ms | limit=300, remaining=598；响应仍很大 |
| 3 | wiki-activities | GET `/api/wiki/activities` | 200 | 0 | 成功 | 49,048 | 2,442 ms | limit=300, remaining=597 |
| 4 | gacha-global-stats | GET `/api/endfield/gacha/global-stats` | 200 | 0 | 成功（cached=true） | 18,312 | 2,217 ms | 首次探测因客户端空值报错，重试成功 |
| 5 | gacha-pool-chars | GET `/api/endfield/gacha/pool-chars` | 200 | 0 | 成功 | 254,572 | 3,126 ms | limit=600, remaining=595 |
| 6 | note-no-token | GET `/api/endfield/note?roleId=...&serverId=1` | 403 | 40301 | 缺少游戏数据凭证… | 62 | 1,750 ms | 未绑定/缺 token |
| 7 | gacha-records-no-token | GET `/api/endfield/gacha/records?page=1&limit=1` | 400 | 400 | 缺少 framework_token | 43 | 1,673 ms | 错误形态与 note 不同 |
| 8 | bindings-dummy-user | GET `/api/v1/bindings?user_identifier=probe_user...` | 200 | 0 | `{"bindings":[],"count":0}` | 58 | 1,419 ms | 无需 token；未知用户空列表 |
| 9 | latest-no-key | GET `/api/announcements/latest`（无 key） | 401 | 401 | 请提供有效的认证凭证… | 114 | 2,660 ms | 三种认证方式文案 |
| 10 | latest-bad-key | GET `/api/announcements/latest`（错 key） | 401 | 401 | API Key 无效: 无效的 API Key 格式 | 51 | 674 ms | 不重试 |
| 11 | 404-path | GET `/api/does-not-exist` | 404 | — | `404 page not found` | 18 | 684 ms | `text/plain`，非 JSON |
| 12 | global-stats-provider | GET `/api/endfield/gacha/global-stats?provider=skland` | 524 | — | 空 body | 0 | 31,069 ms | 网关/源站超时；25 s 客户端会先断 |
| 13 | note-invalid-token | GET `/api/endfield/note?...` + 假 token | 403 | 40301 | 无效的 Framework Token 或会话已过期… | 61 | 2,246 ms | 403 失效 |
| 14 | stamina-invalid-token | GET `/api/endfield/stamina?...` + 假 token | 403 | 40301 | 同上 | 61 | 7,629 ms | 偏慢 |
| 15 | gacha-stats-invalid-token | GET `/api/endfield/gacha/stats` + 假 token | 401 | 401 | 无效的凭证 | 30 | 2,135 ms | **401 失效；上游 403-only 会漏** |
| 16 | search-chars | GET `/api/endfield/search/chars` + 真 key | 403 | 40301 | 缺少游戏数据凭证… | 62 | 1,236 ms | 需要 token，不是公开端点 |
| 17 | latest-extra-headers | GET `/api/announcements/latest` + `client_id`/`platform_id`/`platform` | 200 | 0 | 成功 | 13,447 | 2,329 ms | 额外 header 不改变公开响应 |
| 18 | pool-chars-429 | GET `/api/endfield/gacha/pool-chars`（约 3 分钟内第二次） | 429 | — | HTML | 4,259 | — | 无 `Retry-After`/限流头；`text/html` |

**未执行的写操作**：绑定/扫码确认/手机验证/删除绑定/改 primary/签到/抽卡同步/面板同步/MaaEnd。

---

## 附录 B：代码索引

- `core/client.py:5-7`：`BASE_URL`、`AUTH_FRONTEND_URL`。
- `core/client.py:26`：`httpx.AsyncClient(timeout=25.0)`。
- `core/client.py:36-49`：header 组装（`platform`/`client_id`/`platform_id`/`X-API-Key`/`X-Framework-Token`）。
- `core/client.py:81-145`：请求封装；`raise_for_status`、`code == 0`、异常吞成 `None`。
- `core/client.py:147-163`：403-only 的 token 失效 marker 判断。
- `core/client.py:166-187`：扫码登录。
- `core/client.py:207-280`：授权请求/绑定 CRUD。
- `core/client.py:283-330`：note/stamina/card/crisis-contract。
- `core/client.py:442-559`：抽卡/全服统计/卡池/Wiki。
- `core/client.py:565-580`：公告列表/最新/详情。
- `core/client.py:631-659`：面板同步/角色面板。
- 对照：`docs/research/endfield-upstream.md`（#2）52 端点表；`docs/research/endfield-nonebot-upstream.md`（#34）官方实现与缺陷。
