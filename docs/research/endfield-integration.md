# 终末地插件接入面盘点（wayfinder #3）

- 调研日期：2026-10-31
- 代码快照：`D:\Software\qqbot` 的**工作区**（分支 `main`，HEAD `f22a100`），包含未提交改动与未跟踪文件；本轮**只读**，未写入主工作区
- 交付位置：`denia-endfield-integration` 工作区（分支 `research/endfield-integration`，HEAD 同为 `f22a100`）
- 结论口径：正文中未标「建议」的陈述，都是读代码/配置确认的现状；标「**建议**」的是判断与取舍，不是现状

**快照注意**：主工作区有一批未提交的新文件（`nonebot/plugins/statuscard.py`、`status_*.py`、`nonebot/plugins/superusers.py`、`superuser_settings.py` 等）。目标工作区分支停在 `f22a100`，**这些文件在该分支上还不存在**。因此本文引用它们时，以「主工作区的工作区状态」为准；合并 research 分支前不要拿分支快照去逐行核对 `status_*.py`。

---

## 一、结论速览

| 问题 | 结论 |
|---|---|
| 终末地插件要占哪些接缝？ | 1 个事件 matcher（命令/查询）、1 次 `.help` 注册、一套 `xxx_logic.py` 纯逻辑、一个自有 sqlite/json 存储；出图和面板页都是可选接缝 |
| 入口怎么接？ | `nonebot/bot.py` 只做 `nonebot.init()` + 注册 OneBot v11 + `load_plugin("muicebot")`（`nonebot/bot.py:12-30`），业务插件由 MuiceBot 在 import 时扫描 `./plugins` 加载 |
| 路由怎么写？ | `on_message(rule=..., priority=1~4, block=True)`；自研插件优先级全部早于 MuiceBot 的 `nickname_event=99` / `at_event=100` |
| 命令与人格聊天谁先？ | 插件先。`block=True` 的插件 matcher 命中后事件不再传给 MuiceBot；`block=False`（解析器）会继续下传 |
| `gate` 怎么叠加？ | `gate` 只在启动时把规则挂到 MuiceBot 的两个 matcher 上（`nonebot/plugins/gate.py:185-199`），**不覆盖自研插件的 matcher**；因此群聊 @only 与每人限额对终末地插件都不生效——这是最大的行为冲突 |
| 纯逻辑怎么分？ | 仓库硬约定：`xxx_logic.py` 不 import nonebot/muicebot，接线只放在 `nonebot/plugins/xxx.py`（`README.md:38-41`） |
| 数据放哪？ | `nonebot/data/` 整目录被忽略（`.gitignore:22`）；现有先例是每个功能自己的 sqlite/json，写入用「临时文件 + `os.replace`」，文件权限 0600 |
| 密钥怎么处理？ | **没有字段级加密先例**；面板密码是 scrypt 哈希，天气 API Key 与解析器 Cookie 都是明文落盘（靠 0600 与目录忽略） |
| 面板怎么加页？ | 面板是独立 uvicorn 进程（`nonebot/qqbot-dashboard.service:8`）；读接口用 `authorized`，写接口用 `mutate`（会话 + CSRF + Origin 校验） |
| 定时怎么做？ | 仓库业务代码**没有用 apscheduler**；天气与状态卡都是 `asyncio.create_task` + 轮询 + 落盘标记去重（`nonebot/plugins/weather.py:78-102`、`nonebot/plugins/statuscard.py:1078-1088`） |
| 出图怎么做？ | 纯 Pillow 直绘，`RenderError` 抛给接线层决定降级；状态卡 1170 宽，天气 760 宽；**没有 HTML/Playwright 依赖** |
| 新增依赖贵不贵？ | `httpx` 与 `Pillow` 已在 `requirements.lock`，零成本；Playwright 完全没装，不建议一期引入 |
| 上游 `core/client.py` / `core/user.py` / `core/render.py`？ | 本 checkout 里**不存在**这三份文件（全仓搜索无命中），§十只能按职责给映射建议，不能声称已核对上游实现 |

---

## 二、快照、范围与读法

| 项 | 值 |
|---|---|
| 主仓库 | `D:\Software\qqbot`，`main`，HEAD `f22a100` |
| 目标工作区 | `C:\Users\16907\AppData\Local\Temp\denia-endfield-integration`，分支 `research/endfield-integration` |
| 运行时 | Python + `nonebot2==2.5.0` + `MuiceBot==1.1.5` + OneBot v11（SnowLuma） |
| 进程模型 | 机器人（`bot.py`）与管理面板（`uvicorn dashboard:app`）是**两个进程**，只共享文件（`nonebot/dashboard.py:328-330`） |
| 本轮动作 | 只读代码与配置；唯一写入是目标工作区的本文档 |

读代码时的三条隐含前提，后面反复用到：

1. **插件在 import 时完成注册**：`nonebot/plugins/statuscard.py:1197` 在模块级创建 `runtime`，`:1213` 在模块级注册 matcher；`nonebot/plugins/gate.py:259` 同理。因此「测试 import 插件」就可能产生副作用，现有代码用 `lazy=True` 规避（`nonebot/plugins/statuscard.py:105-119`）。
2. **工作目录必须是 `nonebot/`**：MuiceBot 用相对路径 `./plugins` 加载插件（`muicebot/onebot.py:72`），测试里也显式 `os.chdir`（`nonebot/tests/test_kb_wiring.py:23`）。
3. **数据目录整体不进仓库**：`.gitignore:22` 忽略 `nonebot/data/`，所以新增数据文件不需要改 `.gitignore`，但也拿不到版本历史。

---

## 三、接缝 1：启动与路由

### 3.1 现状

**启动链**（`nonebot/bot.py`）：

| 行 | 动作 |
|---|---|
| 8-9 | 在 `nonebot.init()` 之前写 `OPENAI_CUSTOM_HEADERS`（每次重启换 ID） |
| 12 | `nonebot.init()`，从 `.env` 读配置 |
| 14-17 | 取 driver，注册 OneBot v11 适配器 |
| 19-27 | 在 NoneBot 自己的 FastAPI app 上挂 `/dashboard-health`（供面板探测；面板本体不在这里） |
| 30 | `nonebot.load_plugin("muicebot")` |
| 32-33 | 注释说明：本地插件由 MuiceBot 统一加载，这里不能再提前 `load_plugins`，否则插件 import `muicebot` 时它还没注册 |

MuiceBot 侧的加载时机：`muicebot/onebot.py:66-80` 的 `startup_plugins()` 在**模块导入时**执行，其中 `:72` 调用 `load_plugins("./plugins")`；`plugin_dirs` 也在 `nonebot/pyproject.toml:8-9` 里声明为 `plugins`。

**匹配器与优先级**（NoneBot 按 priority 升序尝试，`block=True` 命中后终止传播）：

| 匹配器 | 位置 | priority | block | 备注 |
|---|---|---|---|---|
| 状态卡收消息计数 | `nonebot/plugins/statuscard.py:1363` | 0 | False | 只计数，不拦截 |
| 状态卡命令 | `nonebot/plugins/statuscard.py:1213` | 1 | True | 仅主人可用（`:1200-1206`） |
| gate 私聊指令 | `nonebot/plugins/gate.py:259` | 1 | True | `permission=SUPERUSER` |
| kb 命令 | `nonebot/plugins/kb.py:212` | 1 | True | `on_command("kb", aliases={"知识库"})` |
| 解析器控制指令 | `nonebot/plugins/media_parser.py:60` | 1 | True | 内部再判超管（`:63-71`） |
| 天气 | `nonebot/plugins/weather.py:43` | 3 | True | 自定义 rule（`:37-41`） |
| 解析器候选 | `nonebot/plugins/media_parser.py:104` | 4 | False | 解析完继续下传 |
| gate 帮助 | `nonebot/plugins/gate.py:322` | 80 | True | 必须早于 MuiceBot 自带的 90，注释在 `:321` |
| MuiceBot 内置命令 | `muicebot/onebot.py:128` 等 | 90（`reset`/`refresh` 等为 10，见 `:146-150`） | — | `command_help`/`command_about`/`command_status` 等 |
| MuiceBot 昵称触发 | `muicebot/onebot.py:240-245` | 99 | True | `nickname_event` |
| MuiceBot @ 触发 | `muicebot/onebot.py:247-253` | 100 | True | `at_event`，`rule=to_me()` |

**gate 如何叠加**（`nonebot/plugins/gate.py`）：

- 启动钩子把 `gate_rule` 与 MuiceBot 两个 matcher 的 `rule` 相与：`:194-199`；拿不到触发器会显式报错，不静默失效（`:188-192`）。
- 判定顺序在 `gate_logic.decide`（`nonebot/gate_logic.py:159-183`）：
  1. 私聊 → 一律放行（`:173-174`）；
  2. 主人在 `superusers` 里 → 放行（`:175-176`，名单取自 `driver.config.superusers`，`nonebot/plugins/gate.py:68-69`、`:172`）；
  3. 群聊且 `group_at_only` 为真 → 必须真的 @ 到机器人（`:177-178`）；
  4. 再查当天限额（`:179-182`）。
- 计数只在 MuiceBot 真的回完消息后发生：`on_finish_chat` 钩子（`nonebot/plugins/gate.py:217-234`），私聊不计数（`:221`），按 `群号|QQ号` 记（`nonebot/gate_logic.py:127-128`）。
- 「引用时 QQ 自动补的 @ 算不算」由 `.env` 的 `QUOTE_AT_COUNTS` 控制，默认算（`nonebot/plugins/gate.py:90-93`；`.env.example:48`）。

**结论：gate 只管 MuiceBot，不管其它插件。** 一个自研插件在 priority 1–4 命中的群消息，既不需要 @ 机器人，也不消耗当天限额，`gate` 完全看不到它。

**命令与人格聊天的先后**：插件 matcher 命中即 `block=True` 消费事件，MuiceBot 的两个触发器（99/100）收不到；`block=False` 的解析器则两边都会跑。反过来，MuiceBot 的 `help`/`about`/`status`（priority 90）晚于 gate 的帮助（80），所以 `.help` 不会被 MuiceBot 抢走（`reset`/`refresh` 等在 10，命令词不同，不构成竞争）。

### 3.2 可复用

| 复用点 | 位置 |
|---|---|
| 插件元数据必填 `__plugin_meta__`（MuiceBot 加载器要读） | `nonebot/plugins/gate.py:48-52`、`nonebot/plugins/kb.py:39-41` |
| 命令/查询 matcher 的写法（priority 1–4、block 语义） | `nonebot/plugins/statuscard.py:1213`、`nonebot/plugins/weather.py:43` |
| 私聊/群聊统一取文本、去前缀 | `nonebot/plugins/statuscard.py:1205-1207`、`nonebot/plugins/media_parser.py:42-44` |
| 超管判定口径（只认 `driver.config.superusers`） | `nonebot/plugins/statuscard.py:1200-1202`、`nonebot/plugins/media_parser.py:63-71` |
| 权限判定读同一份热更新名单 | `nonebot/plugins/superusers.py:44-48` 把 `data/superusers.json` 写回 `driver.config.superusers` |

### 3.3 需改动（建议）

1. **新增 matcher 放 priority 2**（状态卡 1 之后、天气 3 之前），`block=True`。避免与 `查看状态`、`天气` 等已有命令词冲突；若命令词可能撞车，先查 `nonebot/gate_logic.py:422-438` 的 `HELP_ROWS` 与各插件 `register_help_rows`。
2. **rule 必须窄**：只匹配自己的命令词/参数形态，别用 `^.*$`。参考天气的正则（`nonebot/plugins/weather.py:19-35`）——它连「北京天气怎么样」都认，但显式限定在「天气」语义上。
3. **群聊策略要显式决定**，三选一（建议先做 A）：
   - A. 群聊里只允许主人触发（与状态卡一致），普通群员只能用私聊；
   - B. 群聊要求 `event.is_tome()`；
   - C. 群聊允许前缀（`.终末地`），接受它绕过 @only。
4. **若要与 gate 的 @only 口径完全一致**，不要自己写一套 @ 判断，复用 `gate_logic.mentions_self`（`nonebot/gate_logic.py:209-231`）并传入 `original_message` 的段序列（取法见 `nonebot/plugins/gate.py:106-114`），否则「引用自动补 @」的边界会与 gate 不一致。
5. **不要修改 gate 去挂新 matcher**：那会把「只回 @」从 MuiceBot 扩大到插件，属于全局行为变更，超出本插件范围。

---

## 四、接缝 2：插件范式（纯逻辑 + 接线）

### 4.1 现状：四个插件的分层

| 插件 | 接线层 | 纯逻辑/服务层 | 接线层 import 了什么 |
|---|---|---|---|
| weather | `nonebot/plugins/weather.py`（113 行） | `weather_service.py`（98 行，httpx + sqlite）、`weather_image.py`（79 行，Pillow） | `nonebot`（`get_driver/get_bots/logger/on_message`）、`nonebot.adapters.onebot.v11`、`muicebot.plugin`、`gate_logic`、`weather_service`（`nonebot/plugins/weather.py:6-11`） |
| statuscard | `nonebot/plugins/statuscard.py`（1455 行） | `status_logic.py` / `status_collect.py` / `status_store.py` / `status_image.py` / `status_monitor.py` / `status_llm.py` / `status_config.py` | `nonebot`、OneBot 适配器、`muicebot.plugin`、`gate_logic`、上述七个模块（`nonebot/plugins/statuscard.py:33-46`） |
| media_parser | `nonebot/plugins/media_parser.py`（112 行） | `nonebot/media_parser/`（`runtime.py`、`core/*`，独立包） | `nonebot`、OneBot 适配器、`muicebot.plugin`、`gate_logic`、`parser_logic.session_key`（`nonebot/plugins/media_parser.py:6-11`），重活全部延迟到启动钩子（`:22-33`） |
| kb | `nonebot/plugins/kb.py`（242 行） | `kb_logic.py` | `nonebot`、`httpx`、`muicebot.llm.ModelRequest`、`muicebot.plugin.hook.on_before_completion`、`kb_logic`（`nonebot/plugins/kb.py:23-37`） |

状态卡自己的分层说明写得最清楚：`nonebot/plugins/statuscard.py:1-26` 明确列出「纯逻辑 / 采集 / 存储 / 渲染 / 监测 / LLM」各自模块，并声明「这个文件只做把东西接起来」。

**接线层的判定标准**（从上面四个例子归纳）：

- 允许 import：`nonebot.*`、`nonebot.adapters.*`、`muicebot.*`、`gate_logic`、`help_image`、以及本功能的纯逻辑模块。
- 不允许（纯逻辑层）：`nonebot`、`muicebot`；甚至连 logger 都不应直接拿，状态卡的做法是构造时注入（`nonebot/status_store.py:12-13`、`:79-83`）。
- 纯逻辑层可以 import：标准库、`httpx`、`sqlite3`、`PIL`（`nonebot/weather_service.py:1-8`、`nonebot/status_image.py:18-25`）。
- 路径兜底：两个插件都有 `except ImportError` 往 `sys.path` 补工程根的写法（`nonebot/plugins/gate.py:42-46`、`nonebot/plugins/kb.py:33-37`），保证既能 `python bot.py` 跑，也能被测试单独 import。
- 模块级副作用要延后：状态卡用 `Runtime(lazy=True)`（`nonebot/plugins/statuscard.py:1197`）+ `prepare()`（`:1411-1414`）避免 import 时建库。

### 4.2 测试如何打桩

| 手法 | 例子 |
|---|---|
| 幂等引导（只 init 一次、只 load 一次、超管合并） | `nonebot/tests/_bootstrap.py:33-53`；背景在 `:1-16` |
| 接线用例先 `ensure_nonebot()` 再 import 插件 | `nonebot/tests/test_statuscard_wiring.py:26-32`、`nonebot/tests/test_kb_wiring.py:27-31` |
| `os.chdir(nonebot)`（MuiceBot 按 `./plugins` 找插件） | `nonebot/tests/test_kb_wiring.py:23` |
| 假函数替换模块内函数（不打网络） | `nonebot/tests/test_kb_wiring.py:89-92`（替换 `_search_once`） |
| `httpx.MockTransport` 假 HTTP | `nonebot/tests/test_weather_service.py:12-24` |
| 临时目录当数据目录 | `nonebot/tests/test_weather_service.py:30-35`、`nonebot/tests/test_statuscard_wiring.py:60-66` |
| FastAPI `TestClient` + `patch.object` | `nonebot/tests/test_dashboard.py:22-40` |
| 纯逻辑直接断言，不初始化框架 | `nonebot/tests/test_gate_logic.py`（文件头即说明）、`nonebot/tests/test_status_logic.py` |

`_bootstrap.py` 解决的具体问题：NoneBot 插件管理器是全局的，同进程重复 `init + load_plugin` 会抛 `Plugin already exists`（`nonebot/tests/_bootstrap.py:3-9`）；`ensure_nonebot` 只在未初始化时 init、只在未加载时 load（`:37-44`），超管号**合并**而非覆盖（`:46-51`）。

### 4.3 可复用

- 直接抄 `_bootstrap.py` 的用法，不要另写初始化。
- 抄 `nonebot/plugins/statuscard.py:105-119` 的 lazy 思路：插件模块级只构造对象，不落盘。
- 抄 `gate_logic.register_help_rows`（`nonebot/gate_logic.py:446-466`）注册帮助，注意它去重、且不覆盖自带命令。

### 4.4 需改动（建议）

- 新增 `endfield_logic.py` 时**不要** import `nonebot`/`muicebot`，日志用注入或标准库；否则 `nonebot/tests/test_endfield_logic.py` 会退化成需要 bootstrap 的接线测试。
- 新增插件不要 `from nonebot import require` 之类的自动安装依赖；仓库没有这个先例。
- 插件 import 时只注册 matcher 与 help，所有文件读写放到 `on_startup`。

---

## 五、接缝 3：数据与密钥

### 5.1 现状：数据文件一览

| 数据 | 路径 | 形式 | 写入方式 | 读写方 |
|---|---|---|---|---|
| 闸门状态 | `nonebot/data/gate.json` | JSON（day/counts/caps/group_at_only） | 临时文件 + `os.replace`（`nonebot/gate_logic.py:114-121`）；路径常量 `:32` | 仅机器人 |
| 状态卡配置 | `nonebot/data/statuscard.json` | JSON | 临时文件 + `replace`，面板白名单合并（`nonebot/status_config.py:122-127`、`:182-199`） | 机器人与面板 |
| 状态卡状态 | `nonebot/data/statuscard-state.json` | JSON（告警进度、定时标记、计数） | `StateStore` 原子写 + 0600（`nonebot/status_store.py:109-124`、`:151-156`） | 机器人与面板（面板只读，`nonebot/dashboard.py:340-345`） |
| 状态卡采样 | `nonebot/data/statuscard.sqlite3` | SQLite（采样/事件/快照/报表记录） | 每操作短连接，建表 `nonebot/status_store.py:236-240`，schema `:163-218` | 机器人写、面板读 |
| 天气配置与推送 | `nonebot/data/weather.sqlite3` | SQLite（config/pushes） | 建表 + 0600（`nonebot/weather_service.py:12-24`）；路径 `:10` | 机器人与面板 |
| 超级管理员 | `nonebot/data/superusers.json` | JSON（`{"users": [...]}`） | 原子写 + 0600（`nonebot/superuser_settings.py:86-97`）；路径 `:25` | 机器人热加载 + 面板 |
| 面板密码 | `nonebot/data/dashboard-auth.json` | JSON（salt + scrypt hash） | 0600 原子写（`nonebot/dashboard.py:32-40`）；路径 `:25` | 仅面板 |
| 面板会话 | `nonebot/data/dashboard-sessions.json` | JSON（token → 过期时间） | 0600 原子写（`nonebot/dashboard.py:70-81`）；路径 `:50-51` | 仅面板 |
| 解析器配置 | `nonebot/data/media_parser/config.json` | JSON（含 Cookie/代理等敏感字段） | 临时文件 + `os.replace`（`nonebot/parser_settings.py:211-219`）；路径 `:16` | 机器人与面板 |

补充事实：

- `nonebot/data/` 整个目录被忽略（`.gitignore:22`），`nonebot/data/media_parser/cookies/` 另有单独条目（`.gitignore:15`）。
- 状态卡把「小、要能手工看」的放 JSON，「量大、要按时间聚合」的放 SQLite，理由写在 `nonebot/status_store.py:5-13`；这是本仓库最接近「数据层设计说明」的文档。
- 状态卡 `SampleStore` 每次操作开短连接，避免多线程共用一个连接导致 `database is locked`（`nonebot/status_store.py:221-226`）。

### 5.2 现状：密钥与敏感信息

| 项 | 处理方式 | 位置 |
|---|---|---|
| OneBot access token / 超管初始名单 | `.env`（整个文件被忽略），模板只放占位符 | `.gitignore:3`、`.env.example:11`、`:17` |
| 面板登录密码 | **只存 scrypt 哈希 + 随机 salt**，不存明文；会话 token 落盘 0600 | `nonebot/dashboard.py:32-44`、`:70-81`、`:98-104` |
| 面板会话 Cookie | `httponly` + `samesite=lax` + `path=/` | `nonebot/dashboard.py:153` |
| 天气 API Key | **明文**存 SQLite 的 `config` 表，仅靠文件 0600 保护；面板只回显「是否已配置」 | `nonebot/weather_service.py:36-41`、`:17`；`nonebot/dashboard.py:198-199` |
| 解析器 Cookie / 代理 | **明文**存 JSON；面板回显 `secrets` 布尔值，支持 `clear_secrets` 清空 | `nonebot/parser_settings.py:18`、`:46-51`、`:184-189` |
| 加密先例 | **没有**。业务代码里搜不到 `Fernet`/`encrypt`；锁文件里虽有 `cryptography`，但业务代码未使用它 | 全仓搜索无命中；`requirements.lock` 依赖表 |

### 5.3 可复用

- `superuser_settings.py` 的「文件为准、`.env` 只作初始值、损坏回退」三态模型（`nonebot/superuser_settings.py:1-14`、`:113-122`）。
- 面板与机器人共享配置的写法：配置模块**不 import nonebot**，两边都能 import（`nonebot/status_config.py:5-11`）。
- 原子写 + 0600 的模板（`nonebot/status_store.py:115-124`、`nonebot/superuser_settings.py:86-97`）。

### 5.4 需改动（建议）

1. 终末地自己的数据放 `nonebot/data/endfield.sqlite3`（绑定、订阅、缓存、发送标记）与可选的 `nonebot/data/endfield.json`（少量配置）；**不要**往 `gate.json`/`statuscard-state.json` 里塞。
2. 如果上游需要账号级凭据（token/cookie），**建议**沿用「`.env` 或 0600 文件 + 面板只显示是否已配置」的现有口径，并在文档里写明是明文；不要发明一套半吊子加密（密钥仍要落盘，收益低）。
3. 用户绑定（QQ → 终末地 UID）属于个人数据，建议单独表 + 提供解绑；导出/日志里不要带 token。
4. 若需要保留历史，显式做裁剪（参考 `status_store.prune`，`nonebot/plugins/statuscard.py:1135` 的调用方式）。

---

## 六、接缝 4：管理面板

### 6.1 现状：进程与鉴权

- 面板是**独立进程**：`nonebot/qqbot-dashboard.service:8` 用 `uvicorn dashboard:app` 启动，端口 8092（`docs/OPERATIONS.md:12`、`:24`）。机器人重启不影响面板，反之亦然。
- `nonebot/dashboard.py:27` 创建裸 `FastAPI`，显式关掉 `/docs`、`/redoc`、`/openapi.json`。
- 页面就是读一个静态 HTML：`GET /` 返回 `nonebot/dashboard.html`（`nonebot/dashboard.py:131-133`）。

鉴权分两级：

| 级别 | 函数 | 校验 |
|---|---|---|
| 读 | `authorized` | `admin_session` Cookie 必须存在且未过期，否则 401；`must_change` 未改密时 403（`nonebot/dashboard.py:112-123`） |
| 写 | `mutate` | 先过 `authorized`，再要求 `X-CSRF-Token` 等于会话 token，且 `Origin` 的 netloc 等于 `Host`，否则 403（`nonebot/dashboard.py:125-129`） |

### 6.2 现状：路由清单与风格

| 方法 | 路径 | 鉴权 | 作用 |
|---|---|---|---|
| GET | `/` | 无 | 返回单页 HTML |
| POST | `/api/login` | 无（限 8 次/5 分钟/IP） | 校验密码、发会话（`:135-154`） |
| GET | `/api/session` | authorized | 恢复登录态（`:156-164`） |
| POST | `/api/password` | mutate(allow_change) | 改密并清空所有会话（`:166-181`） |
| POST | `/api/logout` | 无 | 删会话（`:183-189`） |
| GET | `/api/state` | authorized | 用户/群/天气聚合（`:191-199`） |
| GET | `/api/bot-status` | authorized | 机器人/协议端/RAG/缓存状态（`:201-219`） |
| POST | `/api/bot-restart` | mutate | 触发重启（`:221-226`） |
| GET | `/api/user/{uid}` | authorized | 用户记忆（`:228-232`） |
| GET/POST | `/api/master-aware` | authorized / mutate | 主人识别（`:234-245`） |
| GET/POST | `/api/parser` | authorized / mutate | 解析器配置（`:247-259`） |
| GET/POST | `/api/parser-events`、`/clear` | authorized / mutate | 解析日志（`:261-271`） |
| GET/POST | `/api/token`、`/api/token/clear` | authorized / mutate | Token 统计（`:273-283`） |
| GET | `/api/group/{gid}` | authorized | 群话题（`:285-289`） |
| POST | `/api/topic` | mutate | 开关/清除话题（`:291-297`） |
| POST | `/api/memory` | mutate | 记忆增删改（`:299-309`） |
| POST | `/api/weather`、`/api/push` | mutate | 天气配置与推送（`:311-325`） |
| GET | `/api/status/overview` | authorized | 状态卡概览/历史（`:359-400`） |
| POST | `/api/status/config` | mutate | 状态卡配置（`:403-411`） |
| GET/POST | `/api/superusers` | authorized / mutate | 超管名单（`:418-448`） |

API 风格：读接口直接 `return {...}`；写接口 `await req.json()`、用 `HTTPException(状态码, 中文说明)` 报错、成功返回 `{'ok': True}`；数据读写尽量走「不 import nonebot 的模块」（注释在 `nonebot/dashboard.py:328-330`、`nonebot/status_config.py:5-11`）。

### 6.3 现状：页面结构

- 单文件 `nonebot/dashboard.html`，内联 `<style>`（`:2-8`）+ 内联 `<script>`（`:21-354`）。
- 侧栏导航硬编码在 `:12`，每个 `<a href="#xxx">` 对应一个 `<section id="xxx" class="card">`（`:14-20`）。
- 所有请求走统一的 `api()`（`:22`），它自动带 `X-CSRF-Token`；错误统一冒泡到 `notice()`。
- 导航点击时才懒加载数据：`:353` 根据 hash 调 `loadStatus/loadTokens/...`。
- 视觉令牌：CSS 变量 `--accent:#c66e4e` 等（`:3`）；卡片头是 `card-icon + h2 + section-description`（如 `:14`）。

### 6.4 加一个「终末地」页要动的地方（建议）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `nonebot/dashboard.html:12` | 侧栏加 `<a class="nav-link" href="#endfield">…终末地</a>` |
| 2 | `nonebot/dashboard.html:14-20` | 加 `<section id="endfield" class="card">`，沿用 card-head + controls + list 结构 |
| 3 | `nonebot/dashboard.html:353` | 加 `if(href==='#endfield')run(loadEndfield)` |
| 4 | `nonebot/dashboard.html` 脚本区 | 加 `loadEndfield()` / `saveEndfield()`，一律走 `api()` |
| 5 | `nonebot/dashboard.py` | 加 `GET /api/endfield`（`authorized`）与 `POST /api/endfield`（`mutate`） |
| 6 | 新模块（建议 `endfield_config.py`） | 默认值 + 白名单 + `public_view`/`apply_patch`，**不 import nonebot**，两边进程共用 |
| 7 | 部署 | `systemctl restart qqbot-dashboard`（`docs/OPERATIONS.md:24`、`:98`）；机器人侧靠 mtime 热加载或重启 |

**不建议**把 FastAPI 路由挂到 `nonebot.get_app()` 上（`nonebot/bot.py:19` 只挂了健康检查）：面板是独立进程、独立 systemd 单元，混进去会让「面板崩了不影响机器人」这条边界失效。

---

## 七、接缝 5：调度（定时推送）

### 7.1 现状：没有 apscheduler 先例

- `requirements.lock:9` 有 `APScheduler==3.11.3`、`:52` 有 `nonebot-plugin-apscheduler==0.5.0`，但业务代码没有使用（来源是框架/插件依赖链，未逐项追溯）。
- 业务代码里唯一用到 apscheduler 的地方是**校验 cron 表达式**：`nonebot/parser_settings.py:12` import `CronTrigger`，`:209` 调 `CronTrigger.from_crontab(...)` 验证格式。没有 `add_job`、没有 scheduler 单例。
- 天气与状态卡都用 `asyncio.create_task` 起自己的循环，在 `on_startup` 创建、`on_shutdown` 取消。

### 7.2 现状：天气的调度

| 项 | 实现 |
|---|---|
| 循环 | `while True` + `asyncio.sleep(30)`（`nonebot/plugins/weather.py:79-102`） |
| 时区 | 硬编码 UTC+8（`nonebot/plugins/weather.py:82`） |
| 到点判断 | `clock == HH:MM` 且 `last_date != 今天`（`:84`） |
| 去重 | `claim_push` 用条件 UPDATE：`last_date=? WHERE id=? AND last_date!=?`（`nonebot/weather_service.py:57-59`；调用 `nonebot/plugins/weather.py:92`） |
| 失败 | 单项异常只记日志（`nonebot/plugins/weather.py:98-101`）；查询/渲染失败不会 claim，同一分钟内每 30 秒会重试，过了这一分钟就等明天 |
| 生命周期 | `on_startup` 创建、`on_shutdown` cancel + gather（`nonebot/plugins/weather.py:104-113`） |
| 发送 | 自己取 `get_bots()` 的第一个 bot，调 `send_group_msg`/`send_private_msg`（`:87-97`） |

### 7.3 现状：状态卡的调度

| 项 | 实现 |
|---|---|
| 循环 | `schedule_loop` 每 30 秒跑一次 `_run_schedule`（`nonebot/plugins/statuscard.py:1078-1088`） |
| 时区 | `datetime.now()` 本地时间（`:1091-1093`），依赖服务器 TZ |
| 到点语义 | `_due`（`:1146-1167`）：今天已发 → 不发；有历史标记但今天没发 → 补发；从未发过 → 只在正好到点那一分钟触发 |
| 去重/防重试 | 成败都写标记，失败当天不再重试，避免每 30 秒刷屏（`:1106-1112`、`:1116-1121`、`:1127-1132`） |
| 持久化 | 标记存 `statuscard-state.json` 的 `schedule` 段（`:1094-1096`；`nonebot/status_store.py:53`） |
| 任务集合 | 采样/告警/监测/定时四个 task 一起在启动时创建（`nonebot/plugins/statuscard.py:1441-1444`），关闭时统一 cancel（`:1448-1455`） |

### 7.4 可复用

- 「落盘 last_date + 条件更新」的 at-most-once 语义（`nonebot/weather_service.py:57-59`）。
- 「成败都记标记」的防重试风暴（`nonebot/plugins/statuscard.py:1106-1112`）。
- `push` 的发送约定：任一目标成功即算成功、超时**不重发**（`nonebot/plugins/statuscard.py:535-545`），避免群里出现两条。
- 配置热加载用 mtime：`maybe_reload`（`nonebot/plugins/statuscard.py:176-189`）在采样循环里调用（`:913`）。

### 7.5 需改动（建议）

1. 新增订阅表 `endfield_pushes(id, kind, target, payload, clock, last_date)`，沿用 `claim_push` 的条件 UPDATE；发送失败要么「当天不再重试」要么「标记前发」，二选一并写清楚。
2. 不要在 `weather.worker` 里加分支，也不要去抢 `statuscard` 的 `schedule_loop`；各插件自带循环是现状，新增一个 `endfield_loop` 最省事（每 30 秒一次、只读自己的表）。
3. 时区**显式**处理：天气已经暴露「硬编码 +8」与「服务器本地时间」两种口径并存的问题（`nonebot/plugins/weather.py:82` vs `nonebot/plugins/statuscard.py:1091`），新插件建议统一用 `ZoneInfo('Asia/Shanghai')` 并在文档/面板标注。
4. 不建议为一期引入 apscheduler：多一个调度器就多一份「重启后 job 丢失/重复注册」的处理，现有两种循环已经够用。

---

## 八、接缝 6：出图（Pillow）

### 8.1 现状：status_image.py 的约定

| 约定 | 证据 |
|---|---|
| 纯 Pillow 直绘，拒绝远程 T2I / HTML，理由是隐私与离线可用 | `nonebot/status_image.py:3-8` |
| 输入是 `status_logic.build_view()` / `build_report()` 产出的 dict，输出 PNG bytes | `nonebot/status_image.py:13-15`；`nonebot/status_logic.py:172-176` |
| 任何异常统一包成 `RenderError`，**渲染层不做降级决定** | `nonebot/status_image.py:30-31`、`:396-404` |
| 宽度默认 1170 | `nonebot/status_image.py:396`、`:664`；配置默认值 `nonebot/status_config.py:22` |
| 主题色：深紫夜空底 + 樱花粉 + 暖金 + 半透明泡泡；装饰画在 RGBA 图层再合成 | `nonebot/status_image.py:10-11`、`:39-61` |
| 字体：优先复用解析器自带字体，再按系统路径找含 CJK 字形的字体，缓存按字号 | `nonebot/status_image.py:68-120` |
| 找不到中文字体时只告警一次并抛 `RenderError` | `nonebot/status_image.py:112-120` |
| 渐进式绘制：渐变底 → 面板/描边 → 文本/图表，PNG 编码在最后 | `nonebot/status_image.py:145-160`、`:406+`、`:653` |
| 失败降级在接线层：`RenderError` → 文字摘要 | `nonebot/plugins/statuscard.py:1174-1192` |

### 8.2 现状：weather_image.py 的简化版

- 同样的 Pillow 路线，但只有 79 行：`render(text)` → PNG bytes（`nonebot/weather_image.py:36-79`），宽度 760（`:40`），字体路径列表含 Debian 文泉驿与 Windows 雅黑（`:13-17`），没有字体缓存、没有 `RenderError`，内容为空抛 `ValueError`（`:38-39`）。
- 调用方用 `asyncio.to_thread` 避免阻塞事件循环（`nonebot/plugins/weather.py:46-50`），渲染失败回退纯文本（`:51-53`）。
- 还有个兼容 hack：系统 Pillow 缺失时把 `/usr/lib/python3/dist-packages` 加进 `sys.path`（`nonebot/weather_image.py:6-11`）。

### 8.3 现状：有没有 HTML/Playwright

- **没有**。全仓 `playwright` 零命中；`requirements.lock` 无此依赖；`media_parser` 也是 Pillow + `apilmoji` 画表情（`nonebot/media_parser/core/render.py:15-19`）。
- 唯一「HTML」是面板页面本身（`nonebot/dashboard.html`），由浏览器渲染，不参与 QQ 出图。

### 8.4 可复用

- `RenderError` + bytes 的契约；接线层 try/except 降级。
- `asyncio.to_thread` 包渲染。
- 字体探测与缓存逻辑（`nonebot/status_image.py:86-120`）。

### 8.5 需改动（建议）

1. 新建 `endfield_render.py`：输入 view dict、输出 PNG bytes、只抛 `RenderError`；不要在渲染层里发消息或读配置。
2. **不要直接 import `status_image`**：它是 AGPL-3.0 衍生文件（`nonebot/status_image.py:1-2`、`README.md:138-141`），而新插件大概率按 MIT 发布。需要主题/字体工具就复制少量工具函数并保留来源说明，或抽一个独立小模块。
3. 宽度建议与状态卡一致用 1170（群里观感统一），字体优先用仓库自带 `media_parser/core/resources/HYSongYunLangHeiW-1.ttf`（`nonebot/status_image.py:69`），但要在文件不存在时有系统字体兜底。
4. 出图失败必须有文字兜底，且**不能**把 `matcher.finish()` 放进 try（`nonebot/plugins/gate.py:325-333` 的注释记录了这个坑：`FinishedException` 会被当成失败吃掉）。

---

## 九、接缝 7：部署与依赖

### 9.1 现状：依赖

| 依赖 | 版本/位置 | 与终末地的关系 |
|---|---|---|
| `httpx` | `requirements.lock:30`（0.28.1） | **已在**，调 API 零新增 |
| `Pillow` | `requirements.lock:64`（13.0.0） | **已在**，出图零新增 |
| `numpy` | `requirements.lock:60`（2.5.4） | 锁文件里已有，未确认本项目业务代码使用；不是出图必需 |
| `APScheduler` | `requirements.lock:9`（3.11.3） | 已在但业务未用；新增调度器不算新增依赖 |
| `fastapi` / `uvicorn` | `requirements.lock:20` / `:92` | 面板已有 |
| `nonebot2` / `MuiceBot` | `requirements.lock:56` / `:47` | 框架 |
| 开发依赖 | `requirements-dev.txt:1-2`：只有 `pytest==9.2.0` | 新测试不需要额外包 |
| Playwright | **无** | 见 9.3 |

### 9.2 现状：服务与脚本

- Linux 服务清单与启动顺序：`docs/OPERATIONS.md:8-13`、`:18-24`；机器人 `cd nonebot && ../.venv/bin/python bot.py`，面板 `systemctl start qqbot-dashboard`。
- 面板 systemd 单元：`nonebot/qqbot-dashboard.service:1-13`（`WorkingDirectory=/opt/qqbot/nonebot`，`ExecStart=... uvicorn dashboard:app --host 192.0.2.226 --port 8092`，`UMask=0077`）。
- Windows 本机：`启动机器人.bat` / `停止机器人.bat`；测试命令 `cd nonebot && python -m pytest tests -q`（`README.md:115`）。
- `deploy-linux/docker-compose.yml` 是**历史遗留的 AstrBot 部署**（`:1-19` 自述、`:21-45` 服务是 napcat + astrbot），与当前 NoneBot 架构不符，不要照它加服务。

### 9.3 新增依赖的成本（建议）

| 方案 | 成本 | 结论 |
|---|---|---|
| httpx 直连 API | 零新依赖；注意超时、重试、限流 | **一期就用它** |
| Pillow 出图 | 零新依赖；CPU 毫秒级，放 `to_thread` | 需要图就用它 |
| Playwright + Chromium | 浏览器包数百 MB、Linux 需系统库、常驻内存、Windows/Linux 安装方式不同；与「语料不出本机」的隐私口径也要重新评估 | **不建议**；除非确有 HTML 模板必须复用 |
| apscheduler | 零新依赖，但引入第二套调度语义 | 不建议，轮询够用 |

新文件不需要改打包配置：`nonebot/plugins/*.py` 由 MuiceBot 扫描（`muicebot/onebot.py:72`），根目录模块由 `sys.path`（cwd = `nonebot/`）导入。

---

## 十、接缝 8：模块映射建议

> **前置事实**：任务里提到的上游 `core/client.py` / `core/user.py` / `core/render.py` 在本 checkout 中**不存在**（全仓搜索无命中，`docs/` 里也没有终末地相关记录）。因此下面的「上游」一列只表示**职责对应**，不是已核对的源码；真正 vendoring 前必须先确认上游仓库与许可证。

### 10.1 建议文件与职责

| 建议文件 | 职责 | 允许 import | 禁止 import | 测试入口 | 职责对应的上游 |
|---|---|---|---|---|---|
| `nonebot/endfield_client.py` | 终末地 API 客户端：鉴权、超时、重试、响应解析成 dataclass/dict；不碰 QQ、不碰存储 | `httpx`、标准库 | `nonebot`、`muicebot`、`PIL` | `nonebot/tests/test_endfield_client.py`（`httpx.MockTransport`） | `core/client.py` |
| `nonebot/endfield_logic.py` | 纯逻辑：命令文本解析、结果格式化、订阅时间判断、绑定校验；无 I/O | 标准库 | `nonebot`、`muicebot`、`httpx`、`sqlite3` | `nonebot/tests/test_endfield_logic.py` | `core/user.py` 的纯计算部分 |
| `nonebot/endfield_store.py` | 持久化：绑定表、订阅表、缓存、`last_date` 标记；原子写/短连接；日志注入 | `sqlite3`、`json`、标准库 | `nonebot`、`muicebot` | `nonebot/tests/test_endfield_store.py`（临时目录） | `core/user.py` 的存储部分 |
| `nonebot/endfield_render.py`（可选） | Pillow 出图，输入 view dict、输出 PNG bytes，只抛 `RenderError` | `PIL`、标准库 | `nonebot`、`muicebot` | `nonebot/tests/test_endfield_render.py` | `core/render.py` |
| `nonebot/endfield_config.py`（可选，若上面板） | 默认配置、白名单、`public_view`/`apply_patch`；面板进程也能 import | 标准库 | `nonebot`、`muicebot` | `nonebot/tests/test_endfield_config.py` | 无 |
| `nonebot/plugins/endfield.py` | 接线：matcher/命令、帮助注册、启动/关闭、定时循环、发送 | `nonebot.*`、`muicebot.*`、`gate_logic`、上面四个模块 | 业务逻辑 | `nonebot/tests/test_endfield_wiring.py` | `main.py` 类胶水 |

### 10.2 依赖方向

```
plugins/endfield.py
   ├─→ endfield_logic.py      (纯计算)
   ├─→ endfield_client.py     (httpx)
   ├─→ endfield_store.py      (sqlite3)
   ├─→ endfield_render.py     (PIL, 可选)
   └─→ endfield_config.py     (标准库, 可选)
```

规则：箭头单向，纯逻辑/客户端/存储/渲染**都不能反向 import 插件**；面板只 import `endfield_config`（或 store 的只读函数），绝不 import 插件（否则会把 nonebot matcher 一起拖进无 driver 的进程，先例警告在 `nonebot/status_config.py:5-11`）。

### 10.3 命名与位置

- 仓库惯例是**根目录平铺** `xxx_logic.py`，不是包（`README.md:70-76`；`status_*` 七个模块都在 `nonebot/` 根下）。建议沿用，不要新建 `nonebot/endfield/` 包。
- 插件名 `endfield`，`__plugin_meta__` 必填（`nonebot/plugins/gate.py:48-52`）。
- 帮助注册用 `gate_logic.register_help_rows`（`:446-466`），并在 `gate_logic.HELP_GROUPS`（`:476-485`）里加一个分组，否则会落到「其它」（`:496-501`）。

### 10.4 与上游三个文件的对应关系（建议）

| 上游 | 本仓库落点 | 说明 |
|---|---|---|
| `core/client.py` | `endfield_client.py` | HTTP/会话/重试；不要带入上游的框架对象（AstrBot/NoneBot 事件） |
| `core/user.py` | `endfield_store.py` + `endfield_logic.py` | 绑定与用户数据落库；纯转换放 logic。若上游有「按用户缓存/配额」，也在这里 |
| `core/render.py` | `endfield_render.py` | 若上游是 HTML 模板，必须重写为 Pillow（§八） |

---

## 十一、接缝 9：风险与冲突

### 11.1 与 gate 的冲突（最高优先级）

| 风险 | 机制 | 证据 |
|---|---|---|
| 群聊 @only 对插件不生效 | gate 只把规则挂在 MuiceBot 的两个 matcher 上；插件 matcher priority 1–4 先跑，`block=True` 直接消费事件 | `nonebot/plugins/gate.py:185-199`、`:138-182`；`nonebot/plugins/statuscard.py:1213` |
| 限额对插件不生效 | 计数只发生在 MuiceBot 的 `on_finish_chat`；插件回复不触发该钩子 | `nonebot/plugins/gate.py:217-234` |
| 私聊不受限 | `decide` 对私聊一律放行 | `nonebot/gate_logic.py:173-174` |
| 超管豁免只对 MuiceBot 有效 | 豁免逻辑在 `gate_rule` 内 | `nonebot/plugins/gate.py:172`、`nonebot/gate_logic.py:175-176` |
| 引用自动 @ 的口径可能不一致 | 插件若自己判 `to_me()`，不认「引用补的 @」 | `nonebot/plugins/gate.py:146-165`、`nonebot/gate_logic.py:185-231` |

**影响**：如果终末地插件在群里响应普通群员，就绕过了主人设的「群里只回 @」和「每人每群每天 N 次」两条规则。**建议**一期把群聊命令限制为主人，或强制 `event.is_tome()`；若产品上确实要放开，必须先在 gate 的文档/`.help` 里说清楚，别让主人以为限额管得住。

### 11.2 与 MuiceBot 人格聊天的冲突

| 风险 | 机制 | 证据 |
|---|---|---|
| 规则过宽会抢走聊天 | priority 1–4 + `block=True`，命中即不再进人格 | `nonebot/plugins/weather.py:43`、`muicebot/onebot.py:240-253` |
| 规则过松会双重回复 | `block=False` 会让插件和 MuiceBot 都回 | `nonebot/plugins/media_parser.py:104` |
| 命令词与 MuiceBot 内置冲突 | MuiceBot 多数命令在 90（`reset`/`refresh` 等在 10）；插件若也用这些优先级会打架 | `muicebot/onebot.py:128` 等、`nonebot/plugins/gate.py:321-322` |
| 帮助菜单被 MuiceBot 抢 | `.help` 必须在 90 之前并 `block=True` | `nonebot/plugins/gate.py:321-322` |

### 11.3 与面板鉴权的冲突

| 风险 | 缓解 |
|---|---|
| 新接口忘记 `authorized` 导致裸奔 | 所有 GET 走 `authorized`，所有 POST 走 `mutate`（`nonebot/dashboard.py:112-129`） |
| 只做会话不做 CSRF | `mutate` 已强制 `X-CSRF-Token` + Origin 校验；前端必须走 `api()`（`nonebot/dashboard.html:22`） |
| 面板 import 插件 | 面板进程没有 nonebot driver；配置/存储模块必须独立（`nonebot/status_config.py:5-11`） |
| 改密码/会话失效 | 现有逻辑会清空所有会话（`nonebot/dashboard.py:175-178`），新页无需特殊处理 |

### 11.4 与 data 目录的冲突

| 风险 | 缓解 |
|---|---|
| 文件名撞车 | 用 `endfield_*` 前缀或独立子目录；现有 `data/` 已有 gate/statuscard/weather/superusers/dashboard/parser |
| 敏感信息明文 | 与现状一致（明文 + 0600），但在 NOTICE/README 写清楚 |
| 多进程同时写 | 机器人写、面板只读；若面板要写，走「面板写 JSON → 机器人 mtime 热加载」（`nonebot/plugins/statuscard.py:176-189`） |
| 目录被 gitignore 后无法回滚 | 关键配置提供默认值 + 面板可见（现有三个插件都这么做） |

### 11.5 与定时任务的冲突

| 风险 | 缓解 |
|---|---|
| 重启后重复推送 | 落盘 `last_date` + 条件更新（`nonebot/weather_service.py:57-59`）或首日只在整点触发（`nonebot/plugins/statuscard.py:1146-1167`） |
| 发送失败后每 30 秒重试 | 成败都写标记（`nonebot/plugins/statuscard.py:1106-1112`） |
| 时区漂移 | 显式 `Asia/Shanghai` 或统一服务器 TZ；现状两种口径并存（`nonebot/plugins/weather.py:82` vs `nonebot/plugins/statuscard.py:1091`） |
| 多实例 | 单机单 bot；若将来多进程，条件 UPDATE 是唯一可靠去重 |

### 11.6 与出图资源的冲突

| 风险 | 缓解 |
|---|---|
| 字体只存在于 `media_parser` 资源里 | 自带/系统兜底；`nonebot/status_image.py:112-120` 会抛 `RenderError` |
| 渲染阻塞事件循环 | `asyncio.to_thread`（`nonebot/plugins/weather.py:49`） |
| AGPL 传染 | 不 import `status_image.py`；复制工具函数要保留署名 |
| Windows/Linux 字体差异 | 字体候选列表同时覆盖（`nonebot/status_image.py:73-80`） |

### 11.7 其他

- **许可证**：仓库 MIT，但 `status_*` 是 AGPL 衍生（`README.md:138-141`）。终末地上游若也是 AGPL，需单独 NOTICE；若是 GPL/闭源，先确认能否改造。
- **API 契约未知**：终末地数据来源、鉴权方式、频率限制、ToS 都没确认（§十五）。
- **凭据泄露**：如果用户要扫码/绑号，日志里不要打印 token；现有 `parser_settings` 的 `clear_secrets` 是可借鉴的逃生口（`nonebot/parser_settings.py:184-189`）。

---

## 十二、建议目录树与文件职责

```
nonebot/
├─ endfield_client.py        # HTTP 客户端（httpx，纯）
├─ endfield_logic.py         # 命令/格式/时间判断（纯，不 import 框架）
├─ endfield_store.py         # sqlite：绑定、订阅、缓存、last_date（纯）
├─ endfield_render.py        # 可选：Pillow 出图（只抛 RenderError）
├─ endfield_config.py        # 可选：面板共享配置（不 import nonebot）
├─ plugins/
│  └─ endfield.py            # 接线：matcher、help、startup/shutdown、发送
├─ tests/
│  ├─ test_endfield_logic.py
│  ├─ test_endfield_client.py
│  ├─ test_endfield_store.py
│  ├─ test_endfield_render.py   # 若做图
│  └─ test_endfield_wiring.py
└─ data/                     # 运行时生成，gitignore
   ├─ endfield.sqlite3
   └─ endfield.json           # 若需要少量配置
```

| 文件 | 必须做到 | 明确不做 |
|---|---|---|
| `nonebot/plugins/endfield.py` | 注册 matcher/help；启动时建库；关闭时 cancel 任务；异常降级为文字 | 不拼 SQL、不画图、不解析业务字段 |
| `endfield_logic.py` | 纯函数、可秒级单测；输入输出都是简单类型 | 不读写文件、不发网络、不打日志到 nonebot |
| `endfield_client.py` | 超时、有限重试、错误分类；可注入 `httpx.AsyncClient` 以便测试 | 不读 `driver.config`、不碰 QQ |
| `endfield_store.py` | 原子写、0600、短连接、可注入路径与 logger | 不 import 框架；不在 import 时建库 |
| `endfield_render.py` | 只做绘制；`RenderError`；`to_thread` 由调用方负责 | 不做降级决策、不发消息 |
| `endfield_config.py` | 默认值 + 白名单 + 面板视图 | 不 import 插件 |

---

## 十三、测试与验收入口

### 13.1 跑法

```bash
cd nonebot
python -m pytest tests -q          # README.md:115
# Windows 本机：
.venv\Scripts\python.exe -m pytest tests -q
```

单个文件也可以直接跑（仓库测试同时兼容 unittest 风格，如 `nonebot/tests/test_weather_service.py:37-38`）。

### 13.2 测试矩阵（建议）

| 文件 | 覆盖 | 打桩方式 | 参考先例 |
|---|---|---|---|
| `nonebot/tests/test_endfield_logic.py` | 命令解析、参数校验、订阅到点判断、格式化 | 无（纯函数） | `nonebot/tests/test_gate_logic.py` |
| `nonebot/tests/test_endfield_client.py` | 成功/超时/4xx/5xx/畸形 JSON | `httpx.MockTransport` | `nonebot/tests/test_weather_service.py:12-24` |
| `nonebot/tests/test_endfield_store.py` | 建表、绑定、去重、`last_date`、并发条件更新 | 临时目录 + 真 sqlite | `nonebot/tests/test_weather_service.py:30-35` |
| `nonebot/tests/test_endfield_wiring.py` | matcher priority/block、rule 命中、超管限制、help 行、启动不写真实 data | `ensure_nonebot` + 事件工厂 + `patch` | `nonebot/tests/test_statuscard_wiring.py:26-32`、`nonebot/tests/test_kb_wiring.py` |
| `nonebot/tests/test_endfield_render.py` | 空输入、正常出图、字体缺失降级 | 直接断言 PNG magic/尺寸 | `nonebot/tests/test_status_image.py` |

接线测试要点：

- 必须先 `os.chdir(nonebot)` 再 import 插件（`nonebot/tests/test_kb_wiring.py:23`）。
- 用 `ensure_nonebot(superusers={...})`（`nonebot/tests/_bootstrap.py:33-53`），不要自己 `nonebot.init()`。
- 断言 `plugin.matcher.priority` 与 `.block`（先例 `nonebot/tests/test_statuscard_wiring.py:62-64`）。
- 数据路径用临时目录，避免污染 `data/`（`nonebot/plugins/statuscard.py:105-119` 的 lazy 设计就是为这个）。

### 13.3 手工验收清单

| # | 场景 | 预期 |
|---|---|---|
| 1 | 私聊发命令 | 有回复；非主人按策略拒绝 |
| 2 | 群里不 @ 直接发命令 | 按 §3.3 选定的策略：拒绝 / 静默 / 允许（必须与文档一致） |
| 3 | 群里 @ 后发命令 | 按策略响应；不触发 MuiceBot 人格回复 |
| 4 | 主人 `.help` | 菜单里出现终末地分组与命令（`gate_logic.HELP_GROUPS`） |
| 5 | 连发两次同样的定时推送 | 同一天只发一次 |
| 6 | 重启机器人后到点 | 不重复发；错过的按 `_due` 语义补发 |
| 7 | 面板未登录访问新接口 | 401 |
| 8 | 面板已登录但无 CSRF 头 POST | 403 |
| 9 | 面板改配置 → 机器人不重启 | 30–60 秒内生效（若实现 mtime 热加载） |
| 10 | 出图失败（临时改坏字体路径） | 回退文字，不静默 |
| 11 | 检查 `git status` | `data/` 下无新增被跟踪文件 |

---

## 十四、风险清单（按优先级）

| 级别 | 风险 | 触发条件 | 验证方式 |
|---|---|---|---|
| P0 | 群聊绕过 @only 与限额 | 插件在群里响应普通群员 | 手工：非主人不 @ 发命令 |
| P0 | 面板新接口漏鉴权/漏 CSRF | 只写 GET 不写 authorized，或 POST 不走 mutate | 未登录 401、无 CSRF 403 |
| P1 | 规则过宽抢人格聊天 | `rule` 用了宽泛匹配 + `block=True` | 用普通聊天句子跑 `_rule` 断言 False |
| P1 | 重复推送/漏推送 | 无落盘标记或标记时序错误 | 重启 + 同分钟多次触发 |
| P1 | 敏感信息进日志/仓库 | 打印 token、把 data 文件提交 | `git status` + 日志抽查 |
| P1 | 面板 import 插件导致启动失败 | 新页 import `plugins.endfield` | 单独 `uvicorn dashboard:app` 启动 |
| P2 | 字体缺失导致图全方框 | 只依赖 `media_parser` 字体 | 改坏字体路径后出图 |
| P2 | AGPL 污染 | 复制/import `status_image.py` | 许可证审查 |
| P2 | 时区偏差（最多 8 小时） | 混用 UTC+8 与本地时间 | 改系统 TZ 后跑 `_due` 测试 |
| P2 | API 限流/封禁 | 无节流、无缓存 | 压测 + 阅读上游 ToS |
| P3 | 依赖膨胀 | 引入 Playwright | 部署包体积/启动时间对比 |

---

## 十五、未解问题（需要产品或上游信息）

1. **数据来源**：终末地是官方开放 API、网页接口还是玩家数据抓取？鉴权方式（token/cookie/扫码）决定 `endfield_client.py` 的形态与部署成本。
2. **核心用例**：查角色/查抽卡/基建排班/公告订阅/签到，哪一个是 v1 必须？不同用例对存储与调度的要求差别很大（订阅才需要 §七，纯查询不需要）。
3. **作用域**：只私聊、只群聊、还是两者？是否需要 @ 才能用？这直接决定 §3.3 的群聊策略。
4. **是否需要面板页**：如果只有几个命令，`.help` + 命令足够；只有「多用户绑定/订阅管理」才值得加 §六 的页面。
5. **是否需要出图**：Pillow 路线成本低，但终末地的信息形态（表格/长文本/图片）是否适合出图未知。
6. **上游代码**：任务提到的 `core/client.py`、`core/user.py`、`core/render.py` 不在本仓库；请提供上游仓库地址与许可证，否则 §十 只能停留在职责映射。
7. **配额与频率**：上游是否有 QPS/日限额？是否需要全局限流（现有插件只有天气做了 10 秒用户级防抖，`nonebot/plugins/weather.py:60-64`）。
8. **隐私**：绑定 UID 与游戏数据是否允许落盘/展示在群里？是否需要「仅主人可见」的默认值。

---

## 附：本轮核对过的关键文件

| 文件 | 用途 |
|---|---|
| `nonebot/bot.py` | 启动链 |
| `nonebot/plugins/gate.py`、`nonebot/gate_logic.py` | 闸门与限额 |
| `nonebot/plugins/weather.py`、`weather_service.py`、`weather_image.py` | 查询/存储/出图/轮询 |
| `nonebot/plugins/statuscard.py`、`status_*.py` | 大型插件的分层、调度、渲染 |
| `nonebot/plugins/media_parser.py`、`nonebot/plugins/kb.py` | 另外两种接线范式 |
| `nonebot/dashboard.py`、`nonebot/dashboard.html`、`qqbot-dashboard.service` | 面板进程、鉴权、页面 |
| `nonebot/superuser_settings.py`、`nonebot/plugins/superusers.py` | 超管存储与热加载 |
| `nonebot/parser_settings.py` | 唯一的 cron 校验、敏感字段处理 |
| `nonebot/tests/_bootstrap.py`、若干测试 | 打桩范式 |
| `requirements.lock`、`requirements-dev.txt`、`docs/OPERATIONS.md` | 依赖与部署 |
| `muicebot/onebot.py`（venv） | 插件加载时机与触发器优先级 |
