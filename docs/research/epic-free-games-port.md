# 研判：astrbot_plugin_epic_free_games_notice → NoneBot2 移植（Epic 喜加一）

研判日期：2026-10-31
上游：`CJSen/astrbot_plugin_epic_free_games_notice`，版本 **v0.0.4**（提交 `8dcd0f93a90cf541322b31a355cf3dccd5b1982a`，2025-12-18）
数据源：`https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions`（CN / US 均实测 HTTP 200）
目标：本仓库 `NoneBot2 + MuiceBot`（SnowLuma / OneBot v11）
决定：**移植，但数据层与推送口径重写**——免费判定按「当前活跃的 0% 促销窗口」，推送按「免费集合变化」去重
实施：**V1 已落地**（2026-10-31）；实施结果、与上游差异、已知限制见 §十一–§十三，实跑证据见 [`epic-free-acceptance.md`](epic-free-acceptance.md)

---

## 零、结论速览

| 问题 | 结论 |
|---|---|
| 值得移植吗？ | **值得，但只能借规则、不能借代码**。上游 248 行里真正有价值的是「打哪个接口、读哪些字段」；调度与推送口径与本项目相反 |
| 能直接复制多少？ | **规则约 35% 可继承，代码 0 行可直搬**：上游无 `LICENSE` 文件，且实测判定逻辑有 3 个会漏报/误报的缺陷（见 §三.3） |
| 最大的技术冲突 | **调度语义相反**：上游是「到点就推」（周五/六/日各推一次，内容重复），本项目要「状态变了才推」；上游 89 行定时逻辑（占全文 36%）整体作废 |
| 第二冲突 | **免费判定**：上游只取 `promotionalOffers[0].promotionalOffers[0]`。实测**同一国家（CN）连续 6 次请求，offer 顺序翻转 2 次**——这 2 次上游逻辑判出「本周无免费」，正确规则 6 次都是 1 条 |
| 接口风险 | 该端点会**以 HTTP 200 返回错误体**（非法 `country` → `data.Catalog.searchStore: null` + `errors[]`）；「返回了」不等于「免费」，12 条里只有 1 条当前免费 |
| 许可证风险 | 上游 README 声明 AGPL-3.0，但**仓库没有 LICENSE 文件**；其数据代码来源 `Soulter/astrbot_plugin_essential` **同样没有许可证**。只能按「作者书面声明的意图」处理 |
| 工作量 | V1（数据层 + 判定 + 命令 + 集合变化推送 + Pillow 卡片 + 面板 + 测试）约 **800–1000 行**，1.5 人日量级 |
| V1 状态 | **已落地**（2026-10-31）：5 个模块约 1140 行、6 个测试文件约 700 行、本机 **55 用例全绿**；实跑见 §十一 |

---

## 一、上游能力清单与代码构成

一句话：把 Epic 每周免费游戏拼成一段中文文本，按「每周几 + 几点」推到配置里的若干群，另有 `/喜加一` 命令手动查询。

能力清单（按价值排序）：

1. **`/喜加一` 命令** —— 输出「标题 / 简介 / 原价 / 现价 / 活动时间」的文本列表
2. **定时群推送** —— `push_way` 支持 `每周一…每周日` / `每天` / `每周五六日`，`push_time` 为 `HH:MM`，`groups` 为 SID 列表
3. **「即将免费」段落** —— 当前无促销但 `upcomingPromotionalOffers` 里有 0% 的条目
4. **失败文案** —— 非 200 时返回「请求失败，请稍后重试。」（注意：这段文字会被**当成正文推进群**）

不具备：去重、幂等、图片、领取链接（`direct_link` 算出来了但被注释掉，见 `main.py:174` 与 `:209`）、配置热加载、空数据保护、时区标注、失败重试退避。

### 1.1 代码构成（`main.py`，共 248 行）

| 行区间 | 行数 | 内容 | 对本项目的价值 |
|---|---:|---|---|
| 1–10 | 10 | 导入（`aiohttp` + 4 条 `astrbot.*`） | 全部替换 |
| 13–18 | 6 | `@register(...)` 装饰器元数据 | 换 `__plugin_meta__` |
| 19–33 | 15 | `Star` 子类、`__init__`、取配置、起定时任务 | 重写 |
| 35–42 | 8 | `filter.command("喜加一")` + `plain_result` | 重写为 matcher |
| 44–48 | 5 | `terminate()` 取消任务 | 换 `on_shutdown` |
| 50–64 | 15 | 定时主循环（算 sleep → 睡 → 推 → 睡 60 秒） | **整体作废** |
| 66–154 | 89 | `_calculate_sleep_time`：按星期几算到下次推送的秒数 | **不移植**（36%，被「集合变化」取代） |
| 156–229 | 74 | `_get_epic_free_games`：取数、判定、拼文本 | **只继承字段与坑**，实现重写 |
| 231–248 | 18 | 逐个 SID 发送 + 每条间隔 2 秒 | 重写为 `bot.call_api` |

（另有 8 行空行/分隔，10 + 6 + 15 + 8 + 5 + 15 + 89 + 74 + 18 + 8 = 248 行。）

### 1.2 数据代码的来源（已核对，不是道听途说）

README 写「主要获取 Epic 数据代码**完全 copy** 自 `Soulter/astrbot_plugin_essential`」。我拉了那个仓库的 `main.py`（541 行，HEAD `1d7cd936`，2026-01-19）对照：

- 同一行 `url` 常量（上游只多了 `locale=zh-CN&country=CN&allowCountries=CN`）
- 同一套 `promotions[0]["promotionalOffers"][0]` 取首条 offer 的写法
- 同一套 `strptime("%Y-%m-%dT%H:%M:%S.%fZ") + 8 小时` 的换算
- 同一句 `if discount != 0: continue`

差异仅三处：上游加了 `description`、加了地区参数、把结果推进群组（并去掉 `use_t2i(False)`）。**结论：判定逻辑的缺陷是继承来的，不是上游新写的。**

### 1.3 仓库与元数据事实

| 项 | 实测值 |
|---|---|
| 提交时间 / 最后推送 | 2025-12-18T18:01:20Z / 2025-12-18T18:01:29Z（此后未更新） |
| 文件（`8dcd0f93` 树） | `.gitignore`(5B) / `README.md`(3886B) / `_conf_schema.json`(1006B) / `main.py`(11117B) / `metadata.yaml`(551B) |
| LICENSE | **不存在**；GitHub API `license: null` |
| 版本 / tag | `metadata.yaml: version: v0.0.4`；仓库 **无任何 tag / release** |
| 热度 | 9 star、0 fork、0 open issue |
| `.gitignore` | 内容为 `test*`（上游从来不跑测试） |

`_conf_schema.json` 只有 3 个字段：`groups`（list，默认值是一个 `wechatpadpro:GroupMessage:你的群号@chatroom` 占位符）、`push_time`（默认 `10:00`，「以服务器时区为准」）、`push_way`（默认 `每周五六日`）。

---

## 二、AstrBot 耦合面逐文件量化

只有一个源文件，所以「逐文件」就是逐段落。**直接触碰 AstrBot API 的代码约 25 行（10%）**，其余是标准库。

| 耦合形态 | 位置 | 行数 | 处理方式 |
|---|---|---:|---|
| `from astrbot.api import AstrBotConfig, logger` 等 4 条导入 | 7–10 | 4 | 换 `nonebot.logger`；配置换本项目 JSON |
| `@register(...)` + `Star` 基类 + `super().__init__(context)` | 13–19, 25 | 8 | 换 `__plugin_meta__`（MuiceBot 强制要求）+ 普通模块 |
| `AstrBotConfig` 点号取值（`push_time` / `push_way` / `groups`） | 24, 27–29, 236 | 4 | 换 `data/epicfree.json`，默认值由代码给出 |
| `filter.command("喜加一")` + `event.plain_result` | 35–42 | 8 | 换 `on_message` matcher + `matcher.finish`（`COMMAND_START=["/", ""]`，裸命令也可） |
| `MessageChain` + `context.send_message(target, chain)` | 238–240 | 3 | 换 `bot.call_api('send_group_msg' / 'send_private_msg')`，单条 20 秒超时 |
| `terminate()` 生命周期 | 44–48 | 5 | 换 `driver.on_shutdown` 取消 task |
| **SID / UMO 目标**（`aiocqhttp:GroupMessage:123`） | 236（只透传字符串） | **0 行解析** | 本项目没有 UMO 概念，改存 `kind` + 整数 `id`，直接给 `group_id` / `user_id` |
| **配置页**（`_conf_schema.json`） | 整文件 31 行 | 31 | 换 `data/epicfree.json` + 面板新页 |
| **定时任务**（自建 `asyncio` 循环 + 89 行算时差） | 32, 50–64, 66–154 | 105 | **不移植**：换「每 N 分钟轮询 + 集合变化去重」或 APScheduler |

三点判断：

1. **SID 是最容易被低估的耦合**。上游配置里存的是平台无关的 UMO 字符串（README 用一整张表解释前缀/中缀/后缀）。本项目只有一个 `aiocqhttp` 适配器，目标就是 `kind + id` 两个字段，比上游简单一个数量级，**但迁移时不能直接沿用上游配置格式**。
2. **`push_time` 的「服务器时区」是隐患**。上游用 naive `datetime.now()`；README 明说「以服务器时区为准」。本项目生产跑 Linux，`docs/PITFALLS.md` 已记过同类坑（「日报的『昨天』偏一天」）。Epic 窗口本身是 UTC，展示必须固定 `UTC+8` 并写「北京时间」。
3. **`_calculate_sleep_time` 的 89 行是纯标准库**，技术上能整段搬过来跑——但搬过来只会把「到点重复推」这个错误范式一起搬进来，所以**行数上看是 36% 可复用，决策上是 0**。

---

## 三、Epic 接口事实

### 3.1 请求与响应骨架

| 项 | 实测（2026-10-31） |
|---|---|
| URL | `https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions?locale=zh-CN&country=CN&allowCountries=CN` |
| 状态 / 类型 | 200 / `application/json; charset=utf-8` |
| 体积 / 耗时 | CN 47087 B、US 51023 B；约 0.59 s |
| 缓存头 | **没有** `Cache-Control` / `ETag`（不能靠 HTTP 缓存省请求） |
| UA / Cookie | 不传 UA 也 200；响应无 `Set-Cookie`，无需登录态 |
| 参数含义 | `locale` 决定标题/简介语言；`country` 决定价格与可售；`allowCountries` 额外过滤；**省略 `allowCountries` 时条目集合与价格相同（仍 12 条）** |
| 主机名 | 带 `-ipv4` 的主机 0.59 s；不带的主机也 200（2.0 s）——**保留 `-ipv4`，但这是网络优化不是硬依赖** |
| 稳定性 | 条目数、标题、价格稳定；**但数组内 offer 顺序每次请求都可能不同**（6 次 CN 抓取出现 2 种顺序，见 §3.3） |

结构只有一层：`data.Catalog.searchStore.elements[]`。该端点不是全店目录：本次 CN 12 条 / US 13 条**全部**带 `categories: freegames`，但当下真正免费的只有 1 条 —— **「出现在响应里」与「当前免费」是两件事**。

### 3.2 字段路径与坑（逐条实测）

| 路径 | 类型 / 实测值 | 用途 | 坑 |
|---|---|---|---|
| `elements[].id` | str，`4e469c7d…946` | 去重键的主键 | 稳定；同一游戏跨周重复免费时不变 → 键里必须再加窗口开始时间 |
| `elements[].title` | str，`《Sorry We’re Closed》` | 展示 | 中英混排、全角书名号、弯引号 `’`；部分标题是半角冒号（`失落城堡: 遗迹守护者`） |
| `elements[].description` | str，中文约 120 字 | 卡片摘要 | 可能缺失；要按宽度截断而不是按字数 |
| `elements[].offerMappings[0].pageSlug` | str，`sorry-were-closed-724c86` | **领取链接** | `catalogNs.mappings[0].pageSlug` 同值，可互为兜底 |
| `elements[].productSlug` | **null**（实测） | — | **不能用它拼链接**，会得到 `/p/null`；`url` 字段同样为 null |
| `elements[].price.totalPrice.discountPrice` | int（分），`0` | 免费交叉校验 | 「永久免费」条目也是 `0`，单靠它必误报（见 §3.3） |
| `…totalPrice.originalPrice` | int（分），`7800` | 原价 | 与 `discount` 字段（`7800`）容易混用 |
| `…totalPrice.currencyCode` | `CNY` / `USD` | 币种 | 卡片要显示 `¥` / `$`，别写死 |
| `…fmtPrice.originalPrice` | str，`¥78.00` / `US$24.99` | 直接展示 | — |
| `…fmtPrice.discountPrice` | str，**`"0"`** | — | 免费时是字符串 `"0"` 而不是 `¥0.00`；直接用会显示「现价 0」 |
| `price.lineOffers[0].appliedRules[0].discountSetting.discountType` | `PERCENTAGE` | — | **该处没有 `discountPercentage`**，不能拿 `lineOffers` 算折扣 |
| `promotions.promotionalOffers[]` | 两层数组：组 → `promotionalOffers[]` | 当前促销 | 上游只取了第一组第一条，漏第二组 |
| `…promotionalOffers[].startDate / endDate` | `2026-10-29T15:00:00.000Z` | 窗口 | 固定 6 位小数；换 `fromisoformat` 更稳 |
| `…discountSetting.discountPercentage` | int，`0` / `20` / `35` / `40` / `55` / `60` / `70` | 免费判据 | 0 才是免费 |
| `promotions.upcomingPromotionalOffers` | 同上结构 | 「即将免费」 | **不是当前免费**；也含未来折扣（20%/35%/55%/60%），需再筛 0% |
| `promotions` 整体 | **null**（CN 5/12 条） | — | 先判空再取子字段 |
| `categories[].path` | 含 `freegames` | — | **全部 12/13 条都有 `freegames`**（含全价条目），不能当免费判据 |
| `keyImages[].type` | `OfferImageWide` / `OfferImageTall` / `Thumbnail` | V2 封面 | CDN 外链，首版不拉 |
| `status` / `effectiveDate` / `expiryDate` | `ACTIVE` / 2024-11-14 / null | — | 与促销窗口无关，别当免费窗口用 |

### 3.3 免费判定：四个反例与正确规则

正确规则（地图已定口径，这里给出实测依据）：

> 遍历 `promotions.promotionalOffers` 的**每一组、组内每一条** offer，只要某条的 `discountPercentage == 0` 且 `startDate <= now <= endDate`，即为当前免费；再用 `price.totalPrice.discountPrice == 0` 且 `originalPrice > 0` 做交叉校验。

四个反例，全部来自本次真实响应：

| 只写一半会怎样 | 实测证据 |
|---|---|
| **只判 `discountPrice == 0`** | `《Idle Champions of the Forgotten Realms》` 是 `0/0`（永久免费），当前无任何促销窗口。今天会多报 1 条 |
| **只取首条 offer** | `《Sorry We’re Closed》` 同一组里有两条并发 offer（0% 与 70%）。**这个数组的顺序每次请求都可能翻转**：6 次 CN 抓取里 4 次「0% 在前」、2 次「70% 在前」。按首条判，那 2 次直接得到「本周无免费」 |
| **把 `upcomingPromotionalOffers` 当当前** | `Idle Champions` 与 `浮游世界` 的下一个 0% 窗口从 `2026-11-05T16:00Z` 才开始，现在报就是提前一周 |
| **用 `categories` 含 `freegames` 过滤** | 12/13 条**全部**带这个分类，包括 `《幽灵行者 2》`（¥169 全价）、`TerraScape`（¥53 全价） |

我把上游原逻辑照抄，在**同一国家、同一小时内的 6 份 CN 响应**上各跑一次，结果可直接复核：

| 抓取序 | offer 顺序（0% / 70%） | 上游逻辑判出的免费 | 正确规则判出的免费 |
|---|---|:--:|:--:|
| 第 1 次 | 0% → 70% | 1 条 | 1 条 |
| 第 2 次 | 70% → 0% | **0 条** | 1 条 |
| 第 3 次 | 70% → 0% | **0 条** | 1 条 |
| 第 4–6 次 | 0% → 70% | 1 条 | 1 条 |

也就是说：**上游的判定结果是抽签**——同一地区、同一时刻，约三分之一的请求会回「本周没有免费游戏」；正确规则 6/6 都是 1 条。同样的抖动也出现在 `upcomingPromotionalOffers`（`《LISA: Definitive Edition》` 的两条未来档在两次抓取里顺序互换），所以「即将免费」段同样受影响。

这也意味着：**「CN/US 差异」不是顺序问题的根因**——顺序是每次请求独立的随机量，与地区无关。把 `country` 做成可配置（地图已定）只会让这个 bug 更容易被看见。

### 3.4 时间窗口与时区换算

| 样本窗口（UTC） | 换算到 UTC+8 | 含义 |
|---|---|---|
| `2026-10-19T15:00Z` → `2026-11-02T16:00Z` | `10-19 23:00` → `11-03 00:00` | 20% / 35% / 70% 折扣档 |
| `2026-10-29T15:00Z` → `2026-11-05T16:00Z` | `10-29 23:00` → `11-06 00:00` | **本周免费档（169 小时）** |
| `2026-11-05T16:00Z` → `2026-11-12T16:00Z` | `11-06 00:00` → `11-13 00:00` | 下周免费档（168 小时） |

三个必须记住的换算事实：

1. **换档点是 UTC 15:00 或 16:00** 二选一，对应北京时间 **23:00 或次日 00:00**。15/16 的分界来自北美夏令时（2026-11-01 结束），所以「周四 23:00 换档」和「周五 00:00 换档」会随月份漂移，**不能写死在配置或文案里**。
2. **窗口不一定是 168 小时**。本周这条 169 小时，正是跨了夏令时切换；任何「end - start 一定是 7 天」的假设都会差一小时。
3. **中国无夏令时**，展示端固定 `UTC+8` 即可；但**存储与比较必须用带时区的 UTC**，`docs/PITFALLS.md` 已记过 naive datetime 偏一天的坑。

上游的 `strptime(..., "%fZ") + timedelta(hours=8)` 在数值上对 UTC+8 是对的，但有两个问题：`%f` 只吃恰好 6 位小数（Epic 若改成无小数秒会直接抛异常，被 `except` 吞掉 → **静默少一条游戏**），且文案里从不写「北京时间」。

### 3.5 CN / US 差异

| 项 | CN | US |
|---|---:|---:|
| `elements` 总数 | 12 | 13 |
| `promotions` 为 null | 5 | 5 |
| 当前活跃 0%（免费） | **1** | **1** |
| 当前只有折扣 | 2（20%、35%） | 3（20%、35%、40%） |
| 仅 upcoming、当前无 offer | 4 | 4 |

- **唯一多出的一条是 `Castlevania Anniversary Collection`（US 40% 折扣）**，它在 CN 不存在。也就是说：CN/US 差的那一条**不是免费条目**，两边免费集合今天完全一致。
- 价格按区域换算：同一款游戏 CN `¥78.00` / US `$24.99`；`currencyCode` 与 `fmtPrice` 前缀都不同。
- `locale=zh-CN` 时，US 响应的标题与简介**仍是中文**（商店无中文本地化的条目除外，如 `Them's Fightin' Herds`）。
- **但 offer 数组顺序每次请求都在变**（见 §3.3），与地区无关；这比「少一条」严重得多。

结论：`country` 必须可配置、默认 `CN`；判定逻辑**绝不能依赖数组顺序或条目数量**。

### 3.6 失败与空数据边界

| 情形 | 实测 / 预期 | 必须怎么处理 |
|---|---|---|
| **非法国家码** | **HTTP 200**，598 B：`{"errors":[{"status":400,"serviceResponse":"…invalid_country_code…"}],"data":{"Catalog":{"searchStore":null}}}` | **只看状态码会崩**：先读 `errors`，再判 `searchStore` 是否为 null |
| 网络失败 / 超时 / 5xx | 异常或非 200 | 不推送、不覆盖上次快照，日志留痕 |
| `elements` 为 `[]` 或 null | 结构上可能 | 视为「本次无有效数据」，**不推「本周无免费」**（Epic 每周都有免费，空=可疑） |
| `promotions` 为 null | 实测 5/12 条 | 跳过，不要 `.get` 之后硬取子键 |
| `promotions` 两个数组都空 | 上游 `main.py:190` 会取 `upcoming[0]` → IndexError | 跳过，绝不 `[0]` |
| 同一元素多条并发 offer | **实测存在**（0% 与 70% 重叠） | 遍历全部，不能取首条 |
| 日期格式变化（无小数秒） | 上游 `%f` 会抛 | 用 ISO 解析 + 失败即跳过并计数 |
| 商店页可达性 | `store.epicgames.com` 全站对本机 **403**（含 `/`、`/p/fortnite`） | **不要 HEAD 校验链接**；只把 URL 当文本渲染 |

### 3.7 本次抓取样本摘要

抓取时间 **2026-10-31 11:12–11:20（UTC+8）**，`locale=zh-CN`，共 6 次 `country=CN`、1 次 `country=US`，均 HTTP 200。下表按「任一份 CN 响应」统计，判定结果对 6 份一致。

| 判定 | 条数 | 标题 |
|---|---:|---|
| 当前免费（活跃 0%） | **1** | `《Sorry We’re Closed》` |
| 当前折扣（非免费） | CN 2 / US 3 | `Eternal Threads`（20%）、`纪念碑谷（Monument Valley）`（35%）、US 另有 `Castlevania Anniversary Collection`（40%） |
| 未来将免费（upcoming 0%） | 2 | `《Idle Champions of the Forgotten Realms》`、`浮游世界`（均 `11-06 00:00` 起，北京时间） |
| 未来只有折扣 | 4 | `《幽灵行者 2》`（20%，12-10）、`《LISA: Definitive Edition》`（60%，11-01）、`《Sorry We’re Closed》`（70%，12-10）、`纪念碑谷`（35%，12-10） |
| 完全无促销 | 5 | `Them's Fightin' Herds`、`Model Builder: Complete Edition`、`失落城堡: 遗迹守护者`、`Luftrausers`、`TerraScape` |

当前免费这一条的细节（卡片要用的全部字段）：

| 字段 | 值 |
|---|---|
| `id` | `4e469c7d8bbf49bcabc2c07127d3d946` |
| `pageSlug` | `sorry-were-closed-724c86` → `https://store.epicgames.com/zh-CN/p/sorry-were-closed-724c86` |
| CN 价格 | `¥78.00` → `0`（`fmtPrice.discountPrice` 是字符串 `"0"`） |
| US 价格 | `US$24.99` → `0` |
| 免费窗口 | BJ `2026-10-29 23:00` → `2026-11-06 00:00`（169 小时） |
| 并发折扣 | 同一条目还挂着 70% 档（BJ `10-19 23:00` → `11-03 00:00`） |
| 简介 | 中文约 120 字，可直接做卡片摘要（需按像素宽度截断） |

**与背景快照的三处更正**（以本次实测为准）：

1. 折扣百分比实测是 **当前 20/35/70、未来 20/35/40/55/60/70**，不只有 20/35/60；
2. **当前免费只有 1 条**，不是多条；「即将免费」另有 2 条；
3. US 多出的那条是**折扣条目**，CN/US 的免费集合今天相同——真正的风险不在「少一条」，而在**每次请求都在翻转的 offer 顺序**。

---

## 四、模块映射表

| 上游 | 目标文件 | 处理 |
|---|---|---|
| `_get_epic_free_games` 的 URL 与字段读取 | `nonebot/epic_free_service.py` | 重写：`httpx` + 错误分类（HTTP / 200-错误体 / 空数据）+ 可注入 path 的缓存 |
| 促销解析与免费判定 | `nonebot/epic_free_logic.py` | 纯函数：`select_free(elements, now)`、`select_upcoming(...)`、`dedupe_key(...)`，不 import nonebot |
| 文本拼装（标题/简介/原价/时间） | `nonebot/epic_free_logic.py` | 重写：加「北京时间」「免费」「领取链接」，按宽度而非字数截断 |
| 出图（上游没有） | `nonebot/epic_free_image.py` | 新增 Pillow 卡，复用本仓库字体候选表与配色 |
| `_send_epic_free_games_to_groups` | `nonebot/plugins/epic_free.py` | `bot.call_api('send_group_msg'/'send_private_msg')`，单目标失败换下一个、不重发 |
| 无对应（上游没有去重） | `nonebot/plugins/epic_free.py` + `epic_free_state.json` | 新增：集合键 + `claim` 式先占后发 |
| `filter.command("喜加一")` | `nonebot/plugins/epic_free.py` | `on_message` matcher + `__plugin_meta__` + `gl.register_help_rows` |
| `_calculate_sleep_time`（89 行） | **不移植** | 换 worker 轮询 + 集合变化；APScheduler 已随 `nonebot-plugin-apscheduler` 可用，但非必需 |
| `_auto_task` 主循环 | `nonebot/plugins/epic_free.py` | `on_startup` 起 task、`on_shutdown` cancel（`weather.py` 同款） |
| `AstrBotConfig` + `_conf_schema.json` | `data/epic_free.json` + `dashboard.py` / `dashboard.html` | 精简为 6–8 个字段，面板「Epic 喜加一」页 |
| SID / UMO 目标 | 面板 `targets[]`（`kind` + `id`） | 与 `weather` 的 `pushes` 表同形，不存 UMO 字符串 |
| `terminate()` | `on_shutdown` | 直接对应 |
| `metadata.yaml` | `__plugin_meta__` | 名称/描述/用法 |
| README 的许可段 | `docs/third-party/epicfree-*` + 根 `README.md` | 见 §八 |

分层遵循本仓库惯例：`*_logic.py` 纯逻辑（不 import `nonebot` / `muicebot`，可秒级单测）+ `plugins/*.py` 只做接线 + `tests/` 覆盖。

---

## 五、必须改口径的地方

### 5.1 推送去重：从「到点就推」改成「集合变了才推」

上游范式：`push_way=每周五六日` → 一周推 3 次，**同一批免费游戏推 3 遍**；重启会把计时器清零，在推送分钟重启就会重复推；多群逐个发、没有任何幂等。

本项目范式（地图已定）：

- **集合键** = 当前免费集合里每条 `id + 该条 0% offer 的 startDate`，排序后取摘要。只放 `id` 会漏——同一款游戏隔几周再免费时集合「没变」，就永远不会再推。
- **先占位再发**：沿用 `weather_service.claim_push(id, date)` 的做法（`UPDATE … WHERE last_date != ?`，见 `weather_service.py:57`、`plugins/weather.py:92`）与 `statuscard.push()` 的「只发一次、超时也不重发」。落点建议是 `data/epic_free_state.json` 里按目标保存 `last_key` + `last_sent_at`。
- **多目标各自占位**：A 群发失败不应让 B 群也跳过。
- **失败/空数据不更新 key**：否则一次网络抖动会把本周的推送永久跳过。
- **首装不补发**：初始 `last_key` 为空时只记录、不发送（可用命令查）；避免「装完立刻往所有群刷一条」。
- **「本周无免费」不推**：Epic 每周都有免费，空集合按异常处理（见 §3.6）。

### 5.2 卡片：文本兜底 + 本地 Pillow + 不拉外链

- 上游只有文本。本项目要卡片，但**卡片失败必须回退文本**——`weather.py:46` 的 `as_picture()` / `logger.warning('图片渲染失败，回退到文字')` 是同款先例。
- 版式建议对齐 `weather_image.py`：宽 760，暗底或浅底统一；每款游戏一块，含「标题 / 原价划掉 → 免费 / 截止时间（北京时间）/ 领取链接」。
- **字体**：内置 `media_parser/core/resources/HYSongYunLangHeiW-1.ttf` 实测覆盖本次样本全部字符（`中《》’·，～—¥$：（）…–★`），**但缺 `▸`（U+25B8）与 `✓`（U+2713）**——版式里不要用这两个符号。字体解析逻辑 `status_image.py:97` 已有「内置 → 系统候选 → CJK 探测 → 缓存 → 只告警一次」的成熟实现，但**跨文件 import 会牵进 AGPL 归属**（见 §八），建议在 epic 模块内自带一份等价的轻量解析。
- **链接**：`https://store.epicgames.com/zh-CN/p/{pageSlug}`，`pageSlug` 取 `offerMappings[0].pageSlug`，兜底 `catalogNs.mappings[0].pageSlug`，两者都空就退回商店首页。**不要用 `productSlug`（实测 null）**，也**不要在服务端 HEAD 校验**（商店全站 403）。
- **不下载封面图**：首版零外链依赖；`keyImages` 留到 V2，且必须带缓存与失败退化。

### 5.3 配置：AstrBot 配置页 → 本项目 JSON + 面板

字段收敛到 7 个：

| 字段 | 默认 | 说明 |
|---|---|---|
| `country` | `CN` | 影响价格与条目；非法值会得到 200 错误体 |
| `locale` | `zh-CN` | 与 `country` 解耦，保持中文标题/简介 |
| `targets` | `[]` | `[{"kind": "group" 或 "private", "id": 123}]` |
| `push_enabled` | `true` | 关掉只留命令 |
| `poll_minutes` | `30` | 轮询间隔（去重保证不会因此多推） |
| `include_upcoming` | `true` | 是否附「即将免费」 |
| `card_enabled` | `true` | 关掉只用文本 |

存放建议 `nonebot/data/epic_free.json`（与 `statuscard.json` 同款：原子写、文件名固定、面板与机器人两个进程共享）。**不要**沿用上游的 `groups` 字符串列表格式。

### 5.4 权限：查询开放，配置收敛到 superusers

- `喜加一` / `epic 喜加一`：**只允许超管、群主、群管理员**；普通群成员与非超管私聊静默拒绝。最初草案是「所有人可用」，后续按 #37 的决议收紧（公开信息也会造成刷屏与滥用）。
- `Epic 测试`：仅超管；改目标、开关推送走面板登录 + CSRF，不暴露 QQ 命令。
- 面板：走 `dashboard.py` 现有登录 + `mutate(req)` CSRF 校验，新增 `/api/epic` 读写。
- **不做「谁问就推给谁」**，私聊目标必须显式添加——否则任何用户都能让机器人主动私聊他人。

### 5.5 许可

见 §八，单独成章。

---

## 六、风险清单

| 风险 | 影响 | 对策 |
|---|---|---|
| 接口字段变更（`productSlug` 已是 null、`fmtPrice` 免费时是 `"0"`） | 链接坏、显示「现价 0」 | 全部字段 `.get` + 兜底；把本次两份真实响应存成 fixture，字段契约进单测 |
| offer 数组顺序随机（实测 6 次里 2 次翻转） | **同一游戏漏报**，回「本周无免费」 | 遍历全部组与全部 offer，禁止 `[0]`；fixture 必须覆盖两种顺序 |
| 夏令时导致换档点 15:00Z ↔ 16:00Z 漂移 | 截止时间差 1 小时 | 存 UTC、显示 +8、文案标「北京时间」，不写死小时 |
| 重复推送 | 群里刷屏、信任度下降 | 集合键（含 `startDate`）+ 先占后发 + 失败不重试 + 首装不推 |
| 200 错误体 / `searchStore: null` 被当成「本周无免费」 | 误报 | 先读 `errors`，空集合按失败处理，不推送也不更新 key |
| 同一游戏跨周重复免费 | 只按 `id` 集合会永久漏推 | 键里带该条 0% offer 的 `startDate` |
| 字体缺字形（`▸` `✓` 实测缺失） | 卡片出现方框 | 只用已覆盖字符；缺字回退文本并告警一次 |
| 商店页 403 | 无法在服务端确认链接有效 | 只渲染文本 URL，不校验；文案写「商店搜索同名游戏」兜底 |
| 许可链条断裂（上游无 LICENSE、上游的上游也无） | 再分发合规风险 | 见 §八：按 AGPL 保守处理 + 明确「数据层为独立重写」 |
| 面板/机器人双进程 | 配置不一致、互相覆盖 | 沿用 `status_config` 的「写读分离 + 原子替换」思路 |
| 测试污染真实 `data/` | 本地库被写、用例互相干扰 | 所有路径可注入，测试用临时目录（`statuscard` 已踩过此坑） |
| 多 Bot 实例 | 重复发送 | 取 `get_bots()` 第一个（`_pick_bot` 同款），或按 `self_id` 固定 |

---

## 七、分期计划（V1 范围与验收标准）

| 期 | 交付 | 预估 |
|---|---|---|
| **V1** | 数据层重写、判定、`喜加一`、集合变化推送、Pillow 卡片 + 文本兜底、面板目标配置、帮助行、第三方许可三件套 | 800–1000 行（含约 300 行测试），1.5 人日 |
| **V2** | 封面图（带缓存与失败退化）、每日提醒开关、多地区并推（CN + US 对比）、历史归档 | 按需 |
| **不做** | 自动领券/账号绑定、Epic 登录、短链服务、i18n、通知中心分群 | — |

### 7.1 V1 范围（逐项可验）

1. `epic_free_service.py`：HTTP 取数、错误分类（网络 / 非 200 / 200-错误体 / 空 elements）、天然缓存（同一轮只取一次）
2. `epic_free_logic.py`：活跃 0% 判定、即将免费、去重键、北京时间格式化、文本渲染、链接拼装
3. `epic_free_image.py`：760 宽卡片；渲染异常抛错由接线层回退文本
4. `plugins/epic_free.py`：`喜加一` 命令（群/私聊，权限见 §5.4）、worker（轮询 + 集合变化）、目标推送、`Epic 测试`
5. `data/epic_free.json` + 面板「Epic 喜加一」页（开关、目标增删、立即测试）
6. `gate_logic` 帮助行 + `HELP_GROUPS` 里新增「Epic」组
7. `tests/`：逻辑 / 解析 fixture / 渲染冒烟 / 接线
8. `docs/third-party/epicfree-{ATTRIBUTION,NOTICE,LICENSE}` + 根 `README.md` 许可段补一条

### 7.2 验收标准（全部可测）

| 验收项 | 通过条件 |
|---|---|
| 判定正确性 | 用本次两份真实响应做 fixture：CN 与 US 都判出 **1 条免费**（`《Sorry We’re Closed》`）、2 条即将免费 |
| 顺序无关 | 用本次两份**顺序相反**的 CN 响应各跑一次，都判出 1 条；再把数组反转，结果仍不变 |
| 不误报永久免费 | `Idle Champions`（`0/0`、无当前窗口）不出现在免费列表 |
| 错误体 | 非法 country 的 200 错误体 → 抛可识别异常；不推送、不更新 `last_key` |
| 空数据 | `elements: []` → 不推送，保留上次快照 |
| 幂等 | 同一 `last_key` 连跑两次 worker 只发一次；进程重启后仍不发 |
| 多目标 | A 群发送抛错时 B 群仍收到；失败目标不占位 |
| 卡片 | 中文/《》/’/¥ 不出方框；宽 760；渲染异常时发送文本且日志有一条 warning |
| 链接 | 用 `pageSlug` 拼出 `/p/…`；`productSlug` 为 null 时不产生 `/p/null` |
| 权限 | 非超管不能改目标/开关；`喜加一` 仅超管/群主/群管理员可回；普通成员与非超管私聊静默 |
| 面板 | 未登录 401、缺 CSRF 拒绝、目标增删落盘并立即生效 |
| 测试卫生 | `pytest tests/` 全绿，且跑完 `nonebot/data/` 无新增文件 |

### 7.3 V1 里最容易低估的三件事

1. **去重键的设计**（不是「存个 id 列表」那么简单，跨周重复免费会漏推）；
2. **错误分类**（HTTP 200 也可能是错误，见 §3.6）；
3. **面板与机器人两个进程的配置一致性**（`statuscard` 为此专门拆了 `status_config.py`）。

---

## 八、许可与署名

### 8.1 事实（先摆事实，再谈建议）

| 项 | 实测 |
|---|---|
| 上游 `LICENSE` 文件 | **不存在**（`8dcd0f93` 树只有 5 个文件） |
| 上游 README | 「本项目默认采用 AGPL-3.0 License，详见 LICENSE 文件」——**指向一个不存在的文件**；「项目结构说明」里也列了 LICENSE |
| 上游 `metadata.yaml` | 无 `license` 字段 |
| GitHub API | `license: null`（GitHub 无法识别出许可证） |
| 上游的数据来源 | `Soulter/astrbot_plugin_essential`，README 自述「完全 copy」；该仓库同样是 `license: null`（HEAD `1d7cd936`） |

三条推论：

1. **不能写「上游采用 AGPL-3.0 许可证」**——那是事实错误（没有许可证文本）。准确表述是「上游 README **声明** AGPL-3.0，但仓库未附 LICENSE 文件」。
2. **AGPL 授权链在上游的上游就断了**：CJSen 只能授权他自己写的部分，无法替 `astrbot_plugin_essential`（无许可证）授权从那里复制的取数代码。
3. 反过来说，**只要不复制代码，这些问题就不落在本项目头上**：接口地址、字段路径、JSON 结构是事实与公开数据，不是受版权保护的表达。判定规则本身也是「实测得出的口径」，可以独立重写。

### 8.2 建议（与地图 Notes 一致，保守但不夸张）

**按 AGPL-3.0 处理本插件的衍生部分**，成本近似为零，收益是把「无许可证」的模糊地带挡在门外：

1. **不复制上游代码**：数据层、判定、调度全部重写；文本版式自行组织。V1 计划里本来就没有一行是搬运。
2. 落三个文件，命名对齐 `statuscard-*`：
   - `docs/third-party/epicfree-ATTRIBUTION.md` —— 来源仓库、作者 `CJSen`、版本 v0.0.4、提交 `8dcd0f93…`、查阅日期、**「上游未附 LICENSE，按 README 声明视作 AGPL-3.0」**、上游的上游 `Soulter/astrbot_plugin_essential`（同样无许可证）。
   - `docs/third-party/epicfree-NOTICE.md` —— 覆盖文件清单（`epic_free_*.py`、`plugins/epic_free.py`、`tests/test_epic_free_*.py`）、本移植的改动、义务说明。
   - `docs/third-party/epicfree-LICENSE` —— AGPL-3.0 全文（取自 <https://www.gnu.org/licenses/agpl-3.0.txt>），并在文件首行注明「上游未附许可证文本，此处按上游 README 声明补齐」。
3. **文件头三行**（与 `tests/test_status_logic.py:1` 同格式）：

   ```
   # 移植自 CJSen/astrbot_plugin_epic_free_games_notice v0.0.4（提交 8dcd0f93），原作者：CJSen。
   # 上游 README 声明 AGPL-3.0，但仓库未附 LICENSE 文件；本文件按 AGPL-3.0 分发。
   # 详见 docs/third-party/epicfree-NOTICE.md
   ```

4. 根 `README.md` 的「许可」段（已有一条 statuscard 的说明）补一条 Epic 的，指向上面三个文件。
5. `docs/PITFALLS.md` 的「许可」小节补一句：**改 `epic_free_*.py` 时不要把它们当 MIT 代码，也不要删文件头**。

### 8.3 一个容易被忽略的坑

`status_image._font()` 是现成的字体解析器，直接 import 最省事——但它在 AGPL 衍生的 `status_image.py` 里。**从 MIT 文件 import 它，会让新文件也变成 AGPL 衍生**。V1 建议在 epic 模块内自带一份等价实现（候选表 + CJK 探测 + 缓存），既避开归属纠缠，也让 epic 卡不依赖状态卡模块。

---

## 九、与 `statuscard-port.md` 的风格对齐说明

本文按同目录 `statuscard-port.md`（同类移植研判）的体例写，逐项对应：

| 体例 | statuscard-port.md | 本文 |
|---|---|---|
| 标题 | `# 研判：<上游> → NoneBot2 移植` | 同句式，补「（Epic 喜加一）」副题 |
| 头部四行 | 研判日期 / 上游+版本+提交 / 目标 / 决定 | 同四行，另加「数据源」一行（本次核查的核心对象） |
| 首章 | `## 零、结论速览` 六行表 | 同结构，把「第二冲突」「接口风险」各自成行 |
| 章号 | 零、一、二……中文数字 | 一致 |
| 正文 | 表格优先、结论先行、每节末尾给判断 | 一致 |
| 量化 | 「19 个源文件引用 astrbot」式的逐项计数 | 「248 行 / 36% 是定时逻辑 / 25 行触碰 AstrBot」式的逐项计数 |
| 风险 | 独立「风险清单」表 | 一致，并按本任务要求把四类风险（接口/字体/重复推送/许可）显式列出 |
| 分期 | 期次 + 预估 + 每期范围 | 一致，另加「V1 验收标准（可测）」表 |
| 许可 | 单独一章，事实 + 落点 + 义务 | 一致，并补「上游的上游也无许可证」这一层 |
| 附录 | 「验证方式」「上游承认的坑」 | 用「可复核方法」替代（本任务要求事实可追溯） |

一处**体例差异**：statuscard 文档末尾的「实施结果」章在本文初版时留空；V1 落地后已按同一体例补在 §十一，并新增 §十二（与上游差异）与 §十三（已知限制）。

---

## 十、附：可复核方法与引用

### 10.1 复现本次核查

```bash
# 1. 接口（CN / US 各一次；注意：同一国家重复请求时，offer 数组顺序可能翻转）
curl -sS --http1.1 \
  "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions?locale=zh-CN&country=CN&allowCountries=CN"
curl -sS --http1.1 \
  "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions?locale=zh-CN&country=US&allowCountries=US"

# 2. 空/错边界：非法国家码得到 HTTP 200 + errors[] + searchStore:null
curl -sS --http1.1 \
  "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions?locale=zh-CN&country=ZZ&allowCountries=ZZ"

# 3. 上游源码（注意：raw.githubusercontent 直连在部分网络下会 TLS 断，需 --http1.1 + UA）
curl -sS --http1.1 -A "Mozilla/5.0" \
  "https://raw.githubusercontent.com/CJSen/astrbot_plugin_epic_free_games_notice/8dcd0f93a90cf541322b31a355cf3dccd5b1982a/main.py"
```

判定复算：对 `elements[]` 展开 `promotions.promotionalOffers[*].promotionalOffers[*]`，取 `discountPercentage == 0` 且 `startDate <= now <= endDate` 的条目；`now` 用 2026-10-31T03:14Z 可复现本文数字。

### 10.2 引用清单

| 来源 | 用途 |
|---|---|
| <https://github.com/CJSen/astrbot_plugin_epic_free_games_notice>（提交 `8dcd0f93`） | 上游能力、耦合面、配置、README 许可声明 |
| <https://raw.githubusercontent.com/CJSen/astrbot_plugin_epic_free_games_notice/8dcd0f93/main.py> | 248 行逐段核对 |
| <https://github.com/Soulter/astrbot_plugin_essential>（HEAD `1d7cd936`） | 数据代码来源与许可链条（`main.py:375–440`） |
| Epic 接口（见 10.1） | 字段、窗口、CN/US 差异、错误边界、样本 |
| <https://www.gnu.org/licenses/agpl-3.0.txt> | 上游缺失的许可文本来源 |
| `nonebot/weather_service.py:57`、`nonebot/plugins/weather.py:92` | `claim_push` 先占后发先例 |
| `nonebot/plugins/statuscard.py:535`、`:295` | 单次推送、`_pick_bot` 先例 |
| `nonebot/status_image.py:69`、`:97` | 字体候选表与 CJK 探测先例（注意 AGPL 归属） |
| `nonebot/gate_logic.py:446`、`:476` | 帮助行注册与分组 |
| `nonebot/dashboard.py:318`、`nonebot/dashboard.html:12` | 面板路由与导航结构先例 |
| `docs/PITFALLS.md` §2、§6 | 时区、定时任务、测试污染、许可的既有教训 |

---

## 十一、实施结果（V1）

落地日期：**2026-10-31**。代码按仓库惯例分两层：纯逻辑/网络模块不 import nonebot，插件只做接线。

| 文件 | 行数 | 职责 |
|---|---:|---|
| `nonebot/epic_free_logic.py` | 298 | 免费判定、即将免费、去重键、北京时间、文本与链接汇总 |
| `nonebot/epic_free_service.py` | 199 | `httpx` 抓取、错误分类、重试/备用域名、`StateStore` |
| `nonebot/epic_free_image.py` | 177 | Pillow 760 宽卡片；自带字体候选表，不 import `status_image` |
| `nonebot/epic_free_config.py` | 202 | `data/epic_free.json` 读写、目标校验、跨进程触发文件 |
| `nonebot/plugins/epic_free.py` | 266 | 命令、worker、超管预览、卡片/文本投递、权限 |
| **合计** | **1142** | 5 个模块（含空行与文件头） |

测试：`nonebot/tests/test_epic_free_{logic,service,image,config,plugin,dashboard}.py` 共 6 个文件约 700 行，**本机 55 用例全绿**；全量套件 **564 passed / 1 skipped**（2026-10-31）。

```bash
cd nonebot && python -m pytest tests -q -k epic_free
# 55 passed, 510 deselected
```

### 11.1 实跑验收（2026-10-31 16:41–16:43，Asia/Shanghai）

| 项 | 操作 | 结果 |
|---|---|---|
| 真实接口抓取 | `service.fetch_view(country='CN')` | 当前免费 **1** 条《Sorry We’re Closed》（¥78.00 → 免费，截止北京时间 2026-11-06 00:00，链接 `/p/sorry-were-closed-724c86`）；即将免费 **2** 条（Idle Champions、浮游世界）；集合键 `4453dd31…` |
| 喜加一 出图 | `epic_free_image.render` | PNG 760×686、95 062 B，中文/《》/’/¥ 正常、无方框；样本 [`epic-free-card-sample.png`](epic-free-card-sample.png) |
| 模拟换期推送 | 假 bot + 真实 `_push_round` 三轮 | 第 1 轮 0 条（首装只记录）；第 2 轮 2 条（卡片 + 全部链接）；第 3 轮 0 条（同一期不重推） |
| 卡片失败回退 | `render` 抛异常 | 返回纯文本（首行「【EPIC 喜加一】」、含「北京时间」），日志一条 warning |
| 接口失败 | `fetch_view` 抛 `EpicAPIError` | 推送 0 条且 `last_key` 不变；命令回「Epic 查询失败，请稍后再试。」 |
| 面板增删目标 | `/api/epic` add / delete / test | 均 200；目标落盘；「立即推送」生成触发文件；未登录 401、缺 CSRF 403 |

完整命令、输出与证据见 [`epic-free-acceptance.md`](epic-free-acceptance.md)。

### 11.2 部署状态

- 测试服务器 `root@192.0.2.226:/opt/qqbot` 已同步 Epic 相关文件，原文件备份 `*.bak-epic-20261031`；`qqbot` 已重启并连接（`Bot 3892894069 connected`）。
- 远程历史记录：部署时 47 个 Epic 用例全绿；本轮在**本机**重跑为 55 个用例全绿（新增了配置/面板/权限用例），**远程未在本轮重跑**。
- 面板 `http://192.0.2.226:8092/` 含 Epic 区块，`/api/epic` 未登录 401。

---

## 十二、与上游差异

结论先行：**规则可以继承，代码没有一行直搬**。上游 248 行里真正被继承的是「打哪个接口、读哪些字段」这类事实；调度、判定、投递与配置全部重写。

| 维度 | 上游 v0.0.4 | 本仓库 V1 |
|---|---|---|
| 数据层 | `aiohttp` 单次请求，只看 HTTP 状态码 | `httpx`，区分网络错误 / 非 200 / **HTTP 200 错误体** / 空 `elements`；同域名重试 + 备用域名 |
| 免费判定 | 只取 `promotionalOffers[0].promotionalOffers[0]` | 遍历**每一组、每一条** offer，按活跃 0% 窗口判定；价格交叉校验；fixture 覆盖顺序反转 |
| 调度 | 按「每周几 + 几点」到点就推，一周可推 3 次同一批 | 轮询 + **集合变化**才推；首装只记录、重启不补发、纯下架不推 |
| 去重 | 无 | 键 = 每款游戏 `id` + 该条 0% offer `startDate`；同一游戏隔周再免费算新一期 |
| 投递 | 单条中文文本 | 每次固定两条：卡片（失败回退文本）+ 一条全部领取链接；`include_upcoming` 控制即将免费 |
| 链接 | 算出来但被注释掉 | 用 `pageSlug` 拼 `/p/…`；不做 HEAD 校验（商店页 403） |
| 配置 | AstrBot `_conf_schema.json` + UMO 字符串 | `data/epic_free.json` + 管理面板；`kind` + 整数 `id`；两进程共享 |
| 权限 | 查询对所有会话开放 | `喜加一` 仅超管/群主/群管理员；`Epic 测试` 仅超管 |
| 失败处理 | 失败文案当正文推进群 | 推送失败/空数据不更新状态、不补发；命令回可读错误 |
| 时区 | naive `datetime.now()` + 手动 +8，文案不标时区 | 存储与比较用带时区 UTC；展示固定 +8 并标「北京时间」；不写死换档小时 |
| 许可 | README 声明 AGPL-3.0，无 LICENSE 文件 | 按 AGPL-3.0 保守分发；`docs/third-party/epicfree-{ATTRIBUTION,NOTICE,LICENSE}` + 文件头署名 |
| 测试 | 无（`.gitignore` 直接写 `test*`） | 6 个测试文件 55 用例：判定、顺序、错误体、状态、渲染、面板、接线 |

---

## 十三、已知限制

1. **没有封面图**：V1 不拉 `keyImages`，卡片只用文字与链接；封面/缓存/失败退化留到 V2。
2. **「即将免费」最多 3 条**：由 `upcoming_limit=3` 与卡片版式限制；不做提前预告推送，只在当前免费消息里附带。
3. **全局目标列表**：所有推送目标收到同一份内容，没有按群差异化或按群频率。
4. **真实 QQ 投递未在本轮自动化内复验**：测试服务器已部署并连接，卡片与文本已在本机生成；「群里实际收到」仍需人工在测试群发一次 `喜加一` 目检（不阻塞本次自动化验收）。
5. **推送延迟**：worker 每 30 秒检查一次，轮询默认 30 分钟；换期后最坏约 30.5 分钟才推。
6. **至多一次，不补发**：发送前先占位；若发送抛错，该目标本期不会重试（避免重复刷屏），需要人工用面板「立即推送」补。
7. **单一地区**：`country` 可配置，但一次只推一个地区；不做 CN + US 对比或并推。
8. **接口字段漂移风险**：全部字段用 `.get` + 兜底，但没有版本化契约；Epic 改字段时需更新 fixture 与解析。
9. **卡片字体边界**：内置字体缺 `▸` / `✓` 等字形，版式已避开；长标题/英文混排仍可能出现不理想的换行（见样本）。
10. **AGPL 链条不完整**：上游与其数据来源都未附 LICENSE，本仓库按 README 声明保守处理；若上游后续补许可证，以补充版本为准。
11. **本机与远程测试口径不同**：本轮本机 55 绿；远程仍是部署时的 47 绿，未在本轮重跑。
12. **权限口径以代码为准**：最初草案「所有人可用」已被 #37 覆盖；本文 §5.4 与 §7.2 已同步为「超管/群主/群管理员」。

---

关联：地图「Epic 喜加一提醒：研判与移植」、票「研判文档定稿、AGPL 署名与实跑验收」。

