# 官方 NoneBot2 插件盘点：`Entropy-Increase-Team/nonebot-plugin-endfield`

- 调研票：wayfinder #34（供「移植基线」决策票 #35 使用）
- 调研日期：2026-10-31
- 代码快照：`C:\Users\16907\AppData\Local\Temp\nonebot_endfield`，浅克隆 `master`，HEAD `526dc466`（2026-03-31），`pyproject.toml:7` 声明 `0.2.5`
- 对照快照：AstrBot 版 `41482b7`（v3.0.3）；denia-bot `D:\Software\qqbot` HEAD `f22a100`
- 交付位置：目标工作区 `denia-endfield-nonebot`（分支 `research/endfield-nonebot`）
- 口径：未标注「推断」的陈述都是读代码、读发布产物或查官方 API 得到的事实；标注「**需运营方确认**」的是仅凭仓库无法闭合的问题
- 本轮只读，未改动主工作区；唯一写入是本文件

---

## 0. 结论速览

| # | 问题 | 结论 | 关键证据 |
|---|------|------|----------|
| 1 | 它是什么 | **第三方 API 网关的客户端**，不是森空岛直连实现；业务数据全部经 `https://end-api.shallow.ink/` 转发，需申请 API Key | `nonebot_plugin_endfield/config.py:6-7`、`README.md:45-53` |
| 2 | 体量 | Python 3,899 行、13 个 matcher、资源 36 MB（其中字体 32 MB） | 见 §3.1、§6.2 |
| 3 | 许可证 | 仓库内**三处人工声明为 AGPL-3.0**（LICENSE / README / GitHub 识别），构建元数据与 PyPI **两处写 MIT**；建议按 AGPL-3.0 处理，**冲突需运营方确认** | `LICENSE:1-3`、`README.md:105-107`、`pyproject.toml:14` |
| 4 | pip 能否直接用 | **不能完整用**：PyPI 0.2.5 的 wheel 只打包了 `assets/fonts/*`，`assets/templates/` 与 `assets/img/` 全部缺失，`终末地信息卡` 安装后必抛 `FileNotFoundError` | 已下载并解包 wheel 核对，见 §6.2 |
| 5 | 功能覆盖 | 只有 AstrBot 的子集：缺便签、理智、危机合约、独立成就、日历、建设、公告列表/订阅、自动签到、面板同步 | §2.1 |
| 6 | 架构 | `endfield/` 是 2 行 shim；两个 entry point **不会**重复注册 matcher，但会产生两个插件记录，且「禁用其中一个」不生效 | `endfield/__init__.py:1-2`、`pyproject.toml:38-40`、§3.2 |
| 7 | 异步安全 | `sync_playwright` 全部经 `asyncio.to_thread` 调用，**没有**在事件循环线程直接跑浏览器；唯一在事件循环里的同步网络调用是二维码图片下载（最长阻塞 10 s） | §5.2、`user_bind.py:50` |
| 8 | 依赖 | 9 个声明依赖里 **5 个零引用**（apscheduler、aiofiles、pyecharts、httpx-ws、numpy）；真正只用 nonebot2 / onebot 适配器 / localstore / httpx / playwright | §6.1 |
| 9 | 与 denia-bot 冲突 | 命令名零冲突；真正冲突在**渲染栈**（本地 Chromium vs 远程 HTML→PNG）、**存储目录**（localstore 平台目录 vs `nonebot/data/`）、**权限模型**（gate 不覆盖外部 matcher） | §7 |
| 10 | 基线含义 | 「接入成本最低」选官方插件骨架；「功能对标 AstrBot」则官方插件缺一半功能，且两头都带 AGPL 约束 | §8、§9 |

---

## 1. 来历与许可证

### 1.1 仓库身份与活跃度（事实）

| 项 | 值 | 来源 |
|---|---|---|
| Git 远端 | `https://github.com/Entropy-Increase-Team/nonebot-plugin-endfield.git` | `git remote -v` |
| 声明的首页 | `https://github.com/TakesBot/nonebot-plugin-endfield` | `pyproject.toml:35-36` |
| 作者字段 | `NekoTC <1719252259@qq.com>`；唯一提交作者为 `Tome Chen <xtwg17192522@outlook.com>` | `pyproject.toml:8-10`、`git log` |
| 提交历史 | 共 68 个提交；本地浅克隆只有 1 个（`526dc466`，2026-03-31） | GitHub Commits API 分页 `last=68` |
| 创建 / 最后推送 | 2026-03-03 / 2026-05-07 | GitHub Repo API |
| 热度 | 5 star / 1 fork / 0 open issue | GitHub Repo API |
| 标签与发布 | tag：`v0.0.1`、`v0.0.2`、`v0.2.0`、`v0.2.1`、`v0.2.2`，另有一个误建的 `push`；**无 GitHub Release** | GitHub Tags / Releases API |
| PyPI | `nonebot-plugin-endfield`，最新 `0.2.5`（2026-05-07 上传），共 13 个版本 | PyPI JSON API |
| 工程配套 | 无 CHANGELOG、无测试目录、无 `MANIFEST.in`；CI 只有 `.github/workflows/pypi-publish.yml`（打 tag 即发布） | 仓库文件树 |

`pyproject.toml:35-36` 仍指向 `TakesBot/…`，与远端组织名不一致——【推断】仓库从个人账号迁到 `Entropy-Increase-Team` 组织后，元数据未同步。

**同团队仓库活跃度对照**（GitHub Org API，2026-10-31 取）：

| 仓库 | 定位 | star | 最后推送 | 许可证 |
|---|---|---|---|---|
| `endfield-plugin` | 云崽，原始实现 | 61 | 2026-07-15 | AGPL-3.0 |
| `astrbot_plugin_endfield` | AstrBot 版 | 66 | 2026-08-24 | AGPL-3.0 |
| **`nonebot-plugin-endfield`** | **NoneBot 版（本文对象）** | **5** | **2026-05-07** | **AGPL-3.0（识别）** |

含义（事实层）：NoneBot 版是该团队的**第三顺位移植**，功能与维护强度都明显弱于 AstrBot 版；近 6 个月无提交。

### 1.2 许可证冲突矩阵（事实）

| 来源 | 声明 | 位置 |
|---|---|---|
| `LICENSE` 文件 | **AGPL-3.0 全文**（35,184 字节，v3, 19 Nov 2007） | `LICENSE:1-3` |
| README | 「## 许可证 / [AGPL v3](./LICENSE)」 | `README.md:105-107` |
| GitHub API | `license.key = agpl-3.0`（由 LICENSE 文件识别） | `api.github.com/repos/…` |
| `pyproject.toml` | `license = "MIT"` | `pyproject.toml:14` |
| PyPI 元数据 | `license_expression = MIT`；**0.0.2 起每个版本都是 MIT** | PyPI JSON API（逐版本核对） |
| 发布产物 | wheel 的 `METADATA` 写 `License-Expression: MIT`，**同时**又打进 `dist-info/licenses/LICENSE`（AGPL 全文 34,523 字节） | 解包 `nonebot_plugin_endfield-0.2.5-py3-none-any.whl` |

补充事实：同组织其余旗舰插件清一色 AGPL-3.0（`delta-force-plugin`、`endfield-plugin`、`astrbot_plugin_endfield`、`endfield-wallpaper`、`MaaEnd-Client`、`koishi-plugin-delta-force`、`astrbot_plugin_rocom`；另有 GPL-3.0 的 `nonebot-plugin-delta-force`、`karin-plugin-delta-force` 与 LGPL-3.0 的 `endfield-sim`）。

### 1.3 判定与复用边界

**事实**：三处人工声明（LICENSE 正文、README 章节、GitHub 识别）一致指向 AGPL-3.0；MIT 只出现在机器生成的构建配置与其派生的包索引里，且自 0.0.2 起从未变过，连发布产物都把 AGPL 全文一起塞进了 wheel。

**推断**：`license = "MIT"` 是模板遗留，团队真实意图是 AGPL-3.0（同组织全部旗舰仓库一致）。→ **应按 AGPL-3.0 处理**。

**但**：许可证冲突无法靠读仓库闭合（无法排除作者本意是 MIT、后加 LICENSE 时贴错），因此——**需运营方确认**：向团队要一句书面澄清，或直接申请 MIT 双授权。这是本票唯一无法用代码解决的问题。

| 复用方式 | 许可含义（推断） | 对 denia-bot（MIT，`LICENSE:1`）的风险 |
|---|---|---|
| `pip install` 后作为依赖 import，不分发修改版 | 通常视为「使用/聚合」而非衍生；AGPL 第 13 条对未修改版本的解释仍有争议 | 低—中；保持独立、可随时替换 |
| fork 后修改、或把 `.py` 拷进仓库 | 衍生作品须整体以 AGPL-3.0 分发；与 MIT 不兼容 | **高**：会把 denia-bot 整体拉进 copyleft |
| 只读接口契约与设计，自写实现 | 不受版权约束（但不得复制代码表达式与结构化原创实现） | 低 |

> 决策建议（仅陈述含义，不替决策）：在拿到书面澄清前，把「复制代码」这条路排除，只把本仓库当成**协议契约与设计参考**。

---

## 2. 功能矩阵（vs AstrBot 版）

### 2.1 主表

符号：✅ 有｜⚠️ 部分/有缺陷｜❌ 无。AstrBot 列依据 `docs/research/endfield-upstream.md:21-75`。

| 组 | 功能 | 官方 NoneBot | 证据 | AstrBot | 差距 |
|---|---|---|---|---|---|
| 账号 | 扫码绑定 | ✅ 轮询 150×2 s，状态机 `done/expired` | `user_bind.py:358-510` | ✅ | 相当 |
| 账号 | 直接填 token 绑定 | ✅ `终末地fmt绑定`（AstrBot 无） | `user_bind.py:25,550-619` | ❌ | **官方独有** |
| 账号 | 授权链接 / 手机 / 国际服登录 | ❌ | 全仓无对应端点 | ✅ 4 种 | 缺 3 种登录 |
| 账号 | 多账号列表与切换 | ✅ 序号或 roleId，本地 sqlite | `user_bind.py:513-548` | ✅ | 相当 |
| 账号 | 删除绑定 | ❌ 无删除命令（仅 upsert） | `user_bind.py:249-304` | ✅ `DELETE /api/v1/bindings/{id}` | 缺删除 |
| 卡片 | 信息卡（note） | ✅ 1920×1080 模板卡 | `user_card.py:14,27-89` | ✅ | 相当 |
| 卡片 | 干员列表 | ✅ 4 列网格图 | `user_char_list.py:14,25-66` | ✅ | 相当 |
| 卡片 | 单干员面板 | ❌ | — | ✅ `operator/operator.html` | 缺 |
| 卡片 | 便签 / 理智 | ❌ | — | ✅ | 缺 2 张卡 |
| 卡片 | 危机合约 | ❌ | — | ✅ | 缺 |
| 卡片 | 成就列表 | ⚠️ 仅信息卡内 10 个徽章槽 | `user_note.py:244-280` | ✅ 独立成就查询 | 缺独立入口 |
| 卡片 | 日历 / 活动 | ❌（`/api/bili-wiki/activities` 只用来取当前 UP 名） | `gacha_analysis.py:557-579` | ✅ | 缺 |
| 卡片 | 帝江号 / 地区建设 | ⚠️ 仅信息卡里 2 个地区等级块 | `user_note.py:325-391` | ✅ 两张独立卡 | 缺 |
| 查询 | 签到 | ⚠️ 裸「签到」；失败静默无回复 | `user_signin.py:13-28` | ✅ 全账号遍历 + 汇总 | 缺汇总与错误反馈 |
| 查询 | 自动签到 | ❌ 全插件无后台任务 | 全仓无 `scheduler`/`create_task` | ✅ | 缺 |
| 公告 | 最新公告 | ✅ 图片 + 原文链接，300 s 内存缓存 | `get_last_announce.py:14,31-68` | ✅ | 相当 |
| 公告 | 公告列表 / 详情 / 订阅推送 | ❌ | — | ✅ 8 条命令 | 缺 |
| 抽卡 | 抽卡记录（分页） | ✅ 850 宽图 + 文本回退 | `gacha_analysis.py:29,860-907` | ✅ | 相当 |
| 抽卡 | 抽卡分析 | ✅ 1500 宽图（保底/UP/时间线） | `gacha_analysis.py:910-948` | ✅ | 相当 |
| 抽卡 | 全服统计 | ✅ 1100 宽图 + 关键词过滤 | `gacha_analysis.py:951-1025` | ✅（多国际服） | 缺国际服 |
| 抽卡 | 手动同步（服务端轮询） | ✅ `fetch` + `sync/status` 轮询 | `gacha_analysis.py:781-852` | ✅ | 相当 |
| 抽卡 | 批量同步全部账号 | ✅ **仅 superusers** | `gacha_analysis.py:1028-1080` | ❌ | **官方独有** |
| 抽卡 | 多账号选择交互 | ⚠️ 独立 `on_message(priority=29, block=False)` 抢数字 | `gacha_analysis.py:33,1083-1132` | ✅ 面板式 | 实现更脆 |
| 系统 | 帮助 | ⚠️ 纯文本 | `help.py:4-26` | ✅ 帮助图 + 文本 | 缺图 |
| 系统 | 健康检查 | ❌ 模块存在但**未注册**，且缺 `await` | `command/__init__.py:1`、`health_check.py:5-9` | ❌ | 死代码 |
| 系统 | 后台任务（公告/理智/调度券推送） | ❌ | — | ✅ 4 个 | 全缺 |
| 系统 | 命令总数 | 12 个 `on_command` + 1 个选择器 | §3.3 | 31 个注册点 | 约 1/2.5 |

### 2.2 文档与实现漂移（事实，影响接入）

| 项 | 文档说法 | 代码实际 |
|---|---|---|
| 抽卡命令名 | `抽卡记录`、`抽卡分析`、`全服抽卡统计`、`同步全部抽卡`（`README.md:79-82`） | 匹配器带前缀：`终末地抽卡记录` 等（`gacha_analysis.py:29-32`）；帮助文本也这么写（`help.py:22-24`） |
| 字体覆盖 | 可用 `ENDFIELD_FONT_BOLD_PATH` / `ENDFIELD_FONT_PATH` / `ENDFIELD_FONT_REGULAR_PATH` 覆盖（`README.md:96`） | **全仓零引用**，未实现 |
| 安装方式 | `plugins = ["nonebot_plugin_endfield"]`（`README.md:40-43`） | 双 entry point（`pyproject.toml:38-40`），两个名字都能加载 |
| 数据位置 | 只说「localstore 插件专属 data 目录」（`README.md:86-92`） | 实际为 localstore 默认目录，非项目 `data/`（§4.4） |

---

## 3. 架构与命令

### 3.1 包结构（事实）

```
nonebot_plugin_endfield/          # 真实实现
├── __init__.py                   # 元数据 + require(localstore) + 启动钩子 + 按 api_key 决定是否加载命令
├── config.py                     # 2 个配置项
├── command/                      # 命令层（2,043 行）
│   ├── __init__.py               # health_check 被注释掉
│   ├── help.py  user_bind.py  user_card.py  user_char_list.py
│   ├── user_signin.py  get_last_announce.py  health_check.py
│   └── gacha_analysis.py         # 1,132 行，占全仓 29%
├── lib/
│   ├── api.py (66)  utils.py (117)  render.py (19，纯 re-export)
│   └── renderers/                # 1,654 行
│       ├── runtime.py (328)  user_note.py (487)  gacha.py (644)
│       └── announce.py  report.py  helpers.py
└── assets/ (36 MB)
    ├── fonts/ (32 MB)  img/ (3.7 MB)  templates/ (user_note.html 24 KB, table.json 9 KB)
endfield/__init__.py              # 2 行 shim
```

| 模块 | 行数 | 性质 |
|---|---:|---|
| `command/gacha_analysis.py` | 1,132 | 抽卡同步/分析/统计/选择器，全仓最重 |
| `lib/renderers/gacha.py` | 644 | 三张抽卡图的内联 HTML |
| `command/user_bind.py` | 618 | 扫码状态机 + sqlite 绑定表 + 迁移 |
| `lib/renderers/user_note.py` | 487 | 模板字符串替换式信息卡 |
| `lib/renderers/runtime.py` | 328 | Playwright 启动/安装/截图 |
| 合计 | 3,899 | 无测试 |

`lib/render.py` 只是把 `renderers/__init__.py` 的导出再导出一遍（`render.py:1-19`），没有逻辑——它存在的价值是让 `command/*` 只 import 一个入口。

【推断】分层意图清晰（命令层 / 协议层 / 存储层 / 渲染层），但这三层都直接依赖同步 I/O 与本地文件，没有面向测试的接缝，也没有任何 mock 点——所以「复用其中一层」的粒度实际比目录看起来更粗。

### 3.2 双 entry point 的加载语义（特别核实）

| 问题 | 结论 | 依据 |
|---|---|---|
| `endfield/__init__.py` 是什么 | **2 行别名 shim**：`from nonebot_plugin_endfield import __plugin_meta__` + `import *` | `endfield/__init__.py:1-2` |
| 是否声明了两个入口 | 是：`endfield` 与 `nonebot_plugin_endfield` | `pyproject.toml:38-40`；wheel 内 `entry_points.txt` 同样两条 |
| 会不会重复注册命令 | **不会**。模块体只执行一次（`sys.modules` 缓存）。`PluginLoader.create_module` 命中缓存即置 `loaded=True`，`exec_module` 直接返回 | `nonebot/plugin/manager.py:237-246` |
| 两个名字都装会发生什么 | 产生**两个 Plugin 记录**（`endfield`、`nonebot_plugin_endfield`），后者在加载前者时顺带被加载；matcher 全部归属 `nonebot_plugin_endfield`（在 `nonebot_plugin_endfield.command.*` 里创建，`_current_plugin` 是它） | `manager.py:244-266`（`_new_plugin` → `_current_plugin.set`） |
| 禁用其中一个有用吗 | **没用**。两者共享同一模块与同一批 matcher；禁用 `endfield` 后 `nonebot_plugin_endfield` 仍工作，反之亦然 | 同上 |
| 有什么实际风险 | ① `nb plugin list` 出现两个插件，易误配；② 若在 `load_plugin` 之前被别的插件 import，加载会报 `Module … is not loaded as a plugin!` | `manager.py:180-186`（`__plugin__` 检查） |
| 另有一个隐式副作用 | `require("nonebot_plugin_localstore")` 与 `asyncio.to_thread` 启动钩子都写在包顶层，因此**只要加载任一入口就会执行** | `nonebot_plugin_endfield/__init__.py:6,26-35` |

> 含义：双入口不是「重复加载 bug」，而是「冗余身份 + 无法按名禁用」。移植时应只保留一个入口。

### 3.3 命令注册表（事实）

NoneBot 2.5 的 `Matcher.new` 默认 `priority=1, block=False`（`nonebot/internal/matcher/matcher.py:202-203`），插件绝大多数命令**没显式传参**，即用默认值。

| 命令 | 别名 | 注册位置 | priority | block | rule / 权限 | 场景 |
|---|---|---|---:|---:|---|---|
| `终末地帮助` | `终末地` | `help.py:4` | 1 | False | 无 | 群/私聊 |
| `终末地绑定` | `endfield绑定`、`终末地扫码绑定` | `user_bind.py:23` | 1 | False | 无 | 群/私聊 |
| `终末地切换账号` | `endfield切换账号`、`终末地账号切换` | `user_bind.py:24` | 1 | False | 无 | 群/私聊 |
| `终末地fmt绑定` | `endfield_fmt绑定`、`终末地direct绑定` | `user_bind.py:25` | 1 | False | 无 | 群/私聊（会把 token 发到群里） |
| `终末地信息卡` | `终末地名片`、`终末地卡片`、`endfield信息卡` | `user_card.py:14` | 1 | False | 无 | 群/私聊 |
| `终末地角色列表` | `终末地角色`、`endfield角色列表` | `user_char_list.py:14` | 1 | False | 无 | 群/私聊 |
| `签到` | — | `user_signin.py:13` | 1 | False | 无 | **全局裸词** |
| `终末地公告` | — | `get_last_announce.py:14` | 1 | False | 无 | 群/私聊 |
| `终末地抽卡记录` | `终末地同步抽卡记录`、`终末地更新抽卡记录` | `gacha_analysis.py:29` | 30 | True | 无 | 群/私聊 |
| `终末地抽卡分析` | — | `gacha_analysis.py:30` | 30 | True | 无 | 群/私聊 |
| `终末地全服抽卡统计` | — | `gacha_analysis.py:31` | 30 | True | 无 | 群/私聊 |
| `终末地同步全部抽卡` | — | `gacha_analysis.py:32` | 30 | True | **superusers** | 仅管理员 |
| （数字选择器） | 1–999 | `gacha_analysis.py:33` | 29 | False | 无 | 每条消息都匹配，读到 pending 才动作 |
| `check`（健康检查） | — | `health_check.py:5` | — | — | `to_me()` | **未注册**（`command/__init__.py:1` 注释） |

事实要点：

- **没有任何 matcher 声明 `to_me()` 或 `permission=`**（`终末地同步全部抽卡` 的权限是 handler 内手写判断，见下一条），即扫码绑定、信息卡、签到在群聊里对任何人、任何 @ 状态都可用（`health_check.py:5` 有 `to_me()`，但模块未注册）。
- 命令加载被 API Key 门控：无 `endfield_api_key` 时只注册元数据、不注册命令，并打 warning（`__init__.py:38-43`）。
- 唯一的权限检查是手写函数 `_is_superuser`（`gacha_analysis.py:854-857`），读 `driver.config.superusers`，**不是** NoneBot 的 `permission=SUPERUSER`。
- 健康检查是死代码，且 bug 级缺陷：`result = api_request(...)` 漏了 `await`（`health_check.py:9`），即使注册也只会回显 coroutine。

### 3.4 命令名冲突扫描（事实）

| 对照 | 结果 | 依据 |
|---|---|---|
| denia-bot 现有插件是否有终末地/签到命令 | **没有**。18 个插件里无「终末地」「签到」「信息卡」「抽卡」类命令 | `nonebot/plugins/*.py` 全量扫描 |
| 与 MuiceBot 命令名 | 无重叠。MuiceBot 自带命令用 `[".", "/"]` 前缀（`about/help/status/reset/…`） | `muicebot/onebot.py:49,127-234` |
| 与 MuiceBot 兜底 matcher 的优先级 | 安全。MuiceBot 昵称兜底 `priority=99, block=True`、@ 兜底 `priority=100`，官方插件最晚 30 | `muicebot/onebot.py:240-250` |
| 启动字符 | 风险点。denia 的 `COMMAND_START=["/", ""]`，空串使命令**不带前缀**即可触发，`签到` 会在任何以「签到」开头的群聊句子上抢答 | `nonebot/.env:13`、`.env.example:18` |
| 其他 priority=1/3/4 的 denia matcher | 均为 rule 门控（gate、media_parser、statuscard、disaster_warning 等），不会误吞终末地命令；但同优先级下**注册顺序**决定先后，插件顺序变化可能影响「谁先 finish」 | `nonebot/plugins/gate.py:259`、`media_parser.py:60,104`、`statuscard.py:1213`、`disaster_warning.py:130` |

【推断】`签到` 是本插件最该在移植时改名的命令：它既无前缀约束、又无权限、又静默失败，在 denia 这种高流量群里会与自然语言撞车。

---

## 4. API 与存储

### 4.1 客户端（`lib/api.py`，事实）

| 项 | 实现 | 含义 |
|---|---|---|
| 传输 | 模块级懒加载单例 `httpx.AsyncClient`，`timeout=10.0`，`max_connections=50 / keepalive=20`（`api.py:12-19`） | 无重试、无退避、无熔断 |
| 生命周期 | **从不关闭**（无 `on_shutdown`） | 退出时可能有未关闭连接的告警；无法热重载 |
| URL 拼接 | `_build_url` 处理 base 尾斜杠与绝对 URL 直通（`api.py:22-30`） | 支持把单个请求指向别的主机 |
| 鉴权 | **不在客户端内**，调用方各自传 header（`api.py:33-38`） | 导致三套头写法并存（见 4.2） |
| 错误处理 | `HTTPStatusError` 4xx 记 debug、5xx 记 warning，其余异常 debug；**一律 return None**（`api.py:49-67`） | 上层无法区分「没数据」与「鉴权失败」；消息提示常是笼统的「请稍后重试」 |
| 返回值 | `dict` 或 `None`，不校验 `code` 字段 | `code != 0` 由每个 handler 自行判断，重复且不一致 |

### 4.2 端点与鉴权头（事实，22 个路径）

| # | Method | Path | 鉴权头 | 用途 | 调用点 |
|---|---|---|---|---|---|
| 1 | GET | `/login/endfield/qr` | `X-API-KEY` | 取绑定二维码 + `framework_token` | `user_bind.py:366` |
| 2 | GET | `/login/endfield/qr/status?framework_token=` | `X-API-KEY` | 扫码状态轮询 | `user_bind.py:405` |
| 3 | POST | `/login/endfield/qr/confirm` | `X-API-KEY` | 换取最终 `framework_token` | `user_bind.py:422` |
| 4 | POST | `/api/v1/bindings` | `X-API-Key` | 在网关登记绑定记录（带 QQ 号） | `user_bind.py:446,565` |
| 5 | GET | `/api/endfield/binding` | `X-Framework-Token` + `X-API-KEY` | 取角色/服务器信息 | `user_bind.py:464,583` |
| 6 | GET | `/api/endfield/note` | `x-api-key` + `x-framework-token` | 账号总览（信息卡/角色列表数据源） | `user_card.py:48`、`user_char_list.py:46`、`gacha_analysis.py:235` |
| 7 | GET | `/api/endfield/spaceship` | 同上 | 帝江号（**取回后被丢弃**） | `user_card.py:53` |
| 8 | GET | `/api/endfield/domain` | 同上 | 地区建设（仅用等级） | `user_card.py:58` |
| 9 | POST | `/api/endfield/attendance` | 同上 | 森空岛签到 | `user_signin.py:23` |
| 10 | GET | `/api/announcements/latest` | 可选 `X-API-Key` | 最新公告 | `get_last_announce.py:38` |
| 11 | GET | `/api/endfield/gacha/stats` | 同上 | 抽卡统计 | `gacha_analysis.py:400,654` |
| 12 | GET | `/api/endfield/gacha/records` | 同上 | 拉取云端抽卡记录 | `gacha_analysis.py:414` |
| 13 | GET | `/api/endfield/gacha/accounts` | 同上 | 账号列表（选择器） | `gacha_analysis.py:734,1044` |
| 14 | GET | `/api/endfield/gacha/sync/status` | 同上 | 同步进度 | `gacha_analysis.py:719,806,1061` |
| 15 | POST | `/api/endfield/gacha/fetch` | 同上 | 触发单个账号同步 | `gacha_analysis.py:795` |
| 16 | POST | `/api/endfield/gacha/sync/fetch` | 同上 | 批量触发同步 | `gacha_analysis.py:1068` |
| 17 | GET | `/api/endfield/gacha/global-stats` | 同上 | 全服统计 | `gacha_analysis.py:601,964,971` |
| 18 | GET | `/api/endfield/gacha/pool-chars` | 同上 | 卡池 UP 角色 | `gacha_analysis.py:389` |
| 19 | GET | `/api/endfield/search/chars` | 同上 | 角色检索 | `gacha_analysis.py:379` |
| 20 | GET | `/api/endfield/search/weapons` | 同上 | 武器检索 | `gacha_analysis.py:380` |
| 21 | GET | `/api/bili-wiki/activities` | 同上 | 当前 UP 池（B 站 wiki 代理） | `gacha_analysis.py:567` |
| 22 | GET | `/health` | 无 | 健康检查（模块未注册） | `health_check.py:9` |

事实：鉴权头存在 **3 种拼写**——`X-API-KEY`（`user_bind.py:364`）、`X-API-Key`（`user_bind.py:448`）、`x-api-key` + `x-framework-token`（`utils.py:60-62`）。HTTP 头大小写不敏感，功能无碍，但说明没有统一的鉴权封装，迁移时容易漏。

事实：`/api/endfield/spaceship` 的返回值在渲染前被**显式丢弃**（`user_note.py:119` `del spaceship_data`）——即「信息卡」命令每次都多发一个无用请求。

事实：绑定流程会把 **QQ 号**作为 `user_identifier` 发往第三方网关（`user_bind.py:451-454`）。数据出境面与 AstrBot 版同源（对照 `endfield-upstream.md:392-402`）。

### 4.3 存储与多账号模型（事实）

| 项 | 实现 | 位置 |
|---|---|---|
| 数据库 | sqlite `endfield_bindings_v3.db`，表 `endfield_bindings_v3` | `utils.py:15,35-40` |
| 表结构 | `user_id, framework_token, user_info, binding_info, binding_id, role_id, server_id, is_active, expires_at, updated_at`；`UNIQUE(user_id, role_id, server_id)` | `user_bind.py:107-131` |
| 写入 | `UPDATE … is_active=0` → `INSERT … ON CONFLICT DO UPDATE`（同一事务） | `user_bind.py:274-304` |
| 迁移 | 缺列则整表改名 `_legacy_<ts>`，重建后回填，最后按 `updated_at` 选一条置 `is_active=1` | `user_bind.py:134-246` |
| 读取当前账号 | `ORDER BY is_active DESC, updated_at DESC, id DESC LIMIT 1`——**没有活跃行时自动降级到最后更新的那条** | `utils.py:79-90` |
| 切换 | 清空该用户所有 `is_active`，再置目标为 1 | `user_bind.py:346-355` |
| 抽卡缓存 | `gacha/<qq>_<roleId>.json`，含 `stats_data`、`records_by_pool`、`gacha_icon_map` | `gacha_analysis.py:76-114` |
| 选择器状态 | `gacha_pending_select.json`，TTL 300 s | `gacha_analysis.py:117-160` |
| 图标缓存 | `gacha_icon_cache/<md5>.<ext>`，渲染时转 data URI | `lib/renderers/gacha.py:114-182` |

安全事实：`framework_token` 是访问游戏数据的唯一凭据，**明文存 sqlite**（`user_bind.py:294`）；建库/写盘没有设 0600 权限，也没有字段级加密。对照 denia-bot 的既有约定（`data/` 目录整体忽略、写入用临时文件 + `os.replace`、0600），这是接入时必须改的一条。

性能事实：所有 sqlite 调用都是**同步**的，且发生在 async handler 内部（`utils.py:80`、`user_bind.py:211,274`、`gacha_analysis.py:41`）。单次为毫秒级，【推断】在低频命令场景可接受，但 `gacha_select` 在**每条消息**上都读一次 JSON（见 §5.4），叠加后是真开销。

### 4.4 localstore 目录（特别核实）

- 插件在包顶层 `require("nonebot_plugin_localstore")`（`__init__.py:6`），并在 `utils.get_data_dir()` 里 `store.get_plugin_data_dir()`，异常兜底到 `Path.cwd()/".nonebot_plugin_endfield"`（`utils.py:21-32`）。
- localstore 0.7.4 的 `get_plugin_data_dir()` 是**同步函数**，返回 `BASE_DATA_DIR/<plugin_id>`（`nonebot_plugin_localstore/__init__.py:179-183`、`137-151`）。
- `BASE_DATA_DIR` 默认取 `user_data_dir("nonebot2")`（平台用户目录）；只有配置了 `localstore_use_cwd` 或 `localstore_data_dir` 才改（`nonebot_plugin_localstore/__init__.py:54-61`）。
- denia-bot 的 `.env` 里**没有任何 `LOCALSTORE_*` 配置**（`nonebot/.env` 全文核对），但其业务数据都写在仓库内 `nonebot/data/`（`nonebot/data/gate.json` 等）。

结论（事实 + 推断）：直接装这个插件，数据会落到**平台用户目录**——本机实测 localstore 的根是 `C:\Users\16907\AppData\Local\nonebot2\`（已有 `muicebot`、`nonebot_plugin_orm` 子目录），故本插件会写到 `…\nonebot2\nonebot_plugin_endfield\`，而不是 denia 认的 `nonebot/data/`。要让它进 `data/`，只能设 `LOCALSTORE_DATA_DIR`——但这是 **localstore 全局配置**，会同时挪动 MuiceBot 等所有用 localstore 的插件的数据目录（`muicebot/onebot.py:87` 打印的就是这个目录，本机现为 `…\nonebot2\muicebot`），属于高风险改动。更稳的做法是移植时把存储层换成项目自己的 `data/` 相对路径。

---

## 5. 渲染

### 5.1 管线（事实）

```
command/*.py  --(asyncio.to_thread)-->  render_*  -->  render_html_to_image / render_page_html_to_image
                                                          |
                                                          ├─ 有 base_dir：把 HTML 写进 localstore/render_tmp/*.html，
                                                          │  注入 <base href="file://…/assets/templates/">，page.goto(file://)
                                                          └─ 无 base_dir：page.set_content(html)
                                                          |
                                                          └─ with sync_playwright(): chromium.launch() → new_page(viewport)
                                                             → wait networkidle + 350 ms → screenshot(full_page) → close()
```

三种出图策略并存：

| 策略 | 使用者 | 资源处理 | 依赖网络 |
|---|---|---|---|
| 内联 HTML + `set_content` | `announce.py`、`gacha.py`、`report.py` | CSS 内联；图片用远程 URL 或 data URI | 是（公告图、抽卡图标） |
| 模板文件 + 字符串/正则替换 + `<base href>` | `user_note.py` | 相对路径 `../fonts`、`../img` 由 Playwright 从磁盘加载 | 否（除头像/徽章外链） |
| 大卡网格 | `user_char_list.py` | 内联 CSS + 远程 `avatarRtUrl` | 是 |

### 5.2 同步 Playwright 与事件循环（特别核实）

`playwright.sync_api.sync_playwright` 在 `runtime.py:9` **顶层 import**，即：只要加载本插件，就硬性要求 `playwright` 已安装，否则 `nonebot_plugin_endfield/__init__.py:9` 的导入链会直接 ImportError，连命令都不会注册。这与「声明依赖」一致，但意味着**不能只拷渲染器而不装 playwright**。

逐个调用点核实（这是本票最关键的一条）：

| 调用点 | 是否 `to_thread` | 说明 |
|---|---|---|
| `user_card.py:76-83` | ✅ | `asyncio.to_thread(_render_note_card, …)` |
| `user_char_list.py:55-60` | ✅ | 同上 |
| `get_last_announce.py:55` | ✅ | `asyncio.to_thread(render_announce_data_image, result)` |
| `gacha_analysis.py:837,844` | ✅ | 分析/记录图 |
| `gacha_analysis.py:903,944,1021` | ✅ | 同上 |
| `__init__.py:29` | ✅ | 启动钩子预装 Chromium |
| `runtime.py:265` → `render_page_html_to_image` | —（被上面的线程包裹） | 内部 `with sync_playwright()`（`runtime.py:291`）、`subprocess.run` 安装（`runtime.py:112-118`）均为阻塞调用，仅在线程中安全 |

结论（事实）：**没有**任何路径在事件循环线程里直接调用 `sync_playwright`；作者的 `to_thread` 纪律是完整的。

但有三条真实风险（推断，附依据）：

1. **纪律靠人工维持**。`render_*` 都是普通同步函数，没有断言/装饰器保护；新增一个调用点忘记 `to_thread` 就会卡死整个事件循环（`runtime.py:291` 是阻塞入口）。
2. **唯一确认的事件循环同步网络调用不在渲染层**：`user_bind.py:375` 调 `_normalize_qrcode_for_onebot_image`，其中 `httpx.get(url, timeout=10.0)`（`user_bind.py:50`）在 async handler 里同步执行，最坏阻塞事件循环 10 s。这是全仓唯一的此类问题。
3. **并发无上限**。每次渲染都 `chromium.launch()` 一个新进程（`runtime.py:292`），没有 semaphore/队列；`asyncio.to_thread` 默认线程池可并发到 `min(32, cpu+4)`，多人群聊同时发图时有内存峰值风险（每个 Chromium 数百 MB）。`_PLAYWRIGHT_INIT_LOCK`（`runtime.py:13,92-94`）只保护「是否已安装」，不限制渲染并发。

### 5.3 HTML 内联、字体与资源（事实）

| 项 | 事实 | 位置 |
|---|---|---|
| 基础样式 | 1,000+ 字 CSS 直接写在 Python 字符串里（`_BASE_STYLE`），用 `WIDTH_PLACEHOLDER` 占位后替换 | `runtime.py:149-254,313-328` |
| 信息卡模板 | 24 KB、844 行、固定 1920×1080 的 HTML；**靠正则/字符串替换**填数据（如把 `<h1 class="profile-name">名称</h1>` 整体换掉、用 `re.sub` 替换徽章与干员行） | `user_note.py:392-481` |
| 模板脆弱性 | 替换点写死占位文本（`名称`、`2026-03-27`、`第二章 —— XXXX`、`00`）。模板一改版，替换静默失效（`_replace_once` 找不到就原样返回，`user_note.py:402-405`） | `user_note.py:414-470` |
| 资源解析 | `<base href="file://…/assets/templates/">`，模板内 `../fonts/*.ttf`、`../img/*.webp` 与 `*.png` 由 Chromium 从磁盘读 | `runtime.py:269-287`、`assets/templates/user_note.html:10,19,67,751-839` |
| 字体 | 模板用 HarmonyOS Regular/Medium；抽卡卡用 Noto Sans CJK **Bold** 的 `@font-face`（绝对 `file://` 路径） | `user_note.html:8-20`、`gacha.py:42-46,209,505,570` |
| 未引用资源 | `NotoSansCJKsc-Regular.otf`（16 MB）、`HarmonyOS_Sans_Thin.ttf`（145 KB）全仓无引用 | grep `NotoSans` / `HarmonyOS` 结果 |
| 图标缓存 | 抽卡图标先同步 `httpx.get` 下载、落盘、再转 base64 data URI；缓存目录无淘汰 | `gacha.py:114-182` |
| 公告图片 | HTML 里直接 `<img src="{API 返回的 URL}">`，由 Chromium 加载；失败 `onerror` 移除节点 | `announce.py:85-91` |

### 5.4 并发与逐消息开销（事实）

- `gacha_select = on_message(priority=29, block=False)`（`gacha_analysis.py:33`）在**每条消息**的 handler 里先 `_get_pending(user_id)`（`gacha_analysis.py:1086`），而它每次都读盘解析 `gacha_pending_select.json`（`gacha_analysis.py:143-153`）。`block=False` 意味着它不阻断后续 matcher，但读盘开销照付。
- 抽卡同步是**前台轮询**：`POLL_INTERVAL_SECONDS = 1.5`、`POLL_TIMEOUT_SECONDS = 180`（`gacha_analysis.py:22-23`），期间 matcher 一直被占用；超时只给一句「稍后再看」，不做后台续跑。

### 5.5 与 AstrBot「16 图卡」的覆盖关系（回答 Q6）

AstrBot 版实际引用 16 个 HTML 模板（`docs/research/endfield-upstream.md:301-326`）。逐个对照官方 NoneBot 版：

| AstrBot 模板 | 用途 | 官方 NoneBot 是否有对应产物 |
|---|---|---|
| `note/note.html` | 便签/账号总览 | ✅ 信息卡（`user_note.py`） |
| `operator/list.html` | 干员列表 | ✅ 干员列表（`user_note.py:54-109`） |
| `gacha/gacha-record.html` | 抽卡记录 | ✅（`gacha.py:207-289`） |
| `gacha/gacha-analysis.html` | 抽卡分析 | ✅（`gacha.py:295-560`） |
| `gacha/global-stats.html` | 全服统计 | ✅（`gacha.py:566-645`） |
| `announcement/detail.html` | 公告详情 | ✅ 最新公告图（`announce.py`） |
| `help/help.html` | 帮助图 | ⚠️ 只有纯文本（`help.py`） |
| `achievement/achievement.html` | 成就列表 | ⚠️ 仅信息卡内 10 个徽章槽 |
| `enduid/bind-list.html` | 绑定列表 | ❌（切换账号只回文本） |
| `stamina/stamina.html` | 理智 | ❌ |
| `crisis-contract/crisis-contract.html` | 危机合约 | ❌ |
| `operator/operator.html` | 干员面板 | ❌ |
| `announcement/list.html` | 公告列表 | ❌ |
| `calendar/calendar.html` | 日历 | ❌ |
| `area/spaceship.html` | 帝江号 | ❌（仅信息卡内等级块） |
| `area/area.html` | 地区建设 | ❌（同上） |

结论（事实 + 推断）：

- **6 个完全覆盖、2 个部分覆盖、8 个无覆盖**。官方插件已能产出 6 张与 AstrBot 同类的卡，所以「要出图」不必回 AstrBot。
- **缺的 8 类必须新写 HTML**；AstrBot 的模板只能当视觉参考——直接拿走是 AGPL 衍生（§1.3）。
- **不是「必须回 AstrBot 模板」**：denia 的远程渲染只需要自包含 HTML（内联 CSS/字体/图片），官方插件这 6 条的 HTML 结构本身就能改成自包含；真正的改造点是 `user_note.py` 的 `<base href>` 方案（§5.3）与 `<img src>` 外链（§5.4 公告图）。
- **「只移植 AstrBot 的少量模板/客户端」性价比不高**：客户端自写更干净（两份端点清单已足够，§4.2 与 `endfield-upstream.md:76-139`）；模板移植则要连带接受 AGPL 与 `_adapt_template` / `_inline_assets` 兼容层（`endfield-upstream.md:287-300`）。
- **pyecharts 与两边都无关**：官方插件零使用（§6.1），AstrBot 的 `requirements.txt` 也只有 `httpx/jinja2/playwright`（`endfield-upstream.md:383`）。两边的「图表」都是 HTML/CSS 画出来的，没有可复用的图表库层。

---

## 6. 依赖与部署

### 6.1 依赖表（声明 vs 实际使用）

| 声明（`pyproject.toml:19-32`） | 版本约束 | 代码是否引用 | 实际用途 / 备注 |
|---|---|---|---|
| `nonebot2` | `>=2.3.0,<3.0.0` | ✅ | 框架 |
| `nonebot-adapter-onebot` | `>=2.2.2,<3.0.0` | ✅ | `MessageSegment.image`、`GroupMessageEvent` |
| `nonebot-plugin-localstore` | `>=0.7.4,<1.0.0` | ✅ | 数据目录 |
| `httpx` | `>=0.23.1,<1.0.0` | ✅ | 异步 API + 同步下载（二维码/图标） |
| `playwright` | `>=1.52.0`（无上限） | ✅ | 截图；**顶层 import**，未装即插件加载失败 |
| `nonebot-plugin-apscheduler` | `>=0.3.0,<1.0.0` | ❌ | 全仓无 `scheduler` 引用 |
| `aiofiles` | `>=23.2.1,<25.0.0` | ❌ | 全仓无引用（纯同步写盘） |
| `pyecharts` | `>=2.0.0,<3.0.0` | ❌ | **没有任何图表代码**；抽卡图是纯 CSS/HTML |
| `httpx-ws` | `>=0.7.2,<1.0.0` | ❌ | 全仓无 WebSocket 代码 |
| `numpy` | `<3.10` 用 1.26.0，`>=3.10` 用 2.x | ❌ | 全仓无引用 |

事实：9 个依赖里 **5 个零引用**。这意味着「装这个插件」会平白引入 `pyecharts`、`numpy`、`httpx-ws`、`aiofiles` 四份包（体积与供应链面），以及一个用不到的 `apscheduler`。

> 任务描述里提到「Playwright/pyecharts 渲染」——**pyecharts 并未使用**，本插件所有图都是 Playwright 截图的内联 HTML。这一点会影响对「图表能力」的预期：它没有可复用的图表层，只有 HTML 模板。

### 6.2 发布产物缺资源（事实，已实证）

下载并解包 PyPI `nonebot_plugin_endfield-0.2.5-py3-none-any.whl`（28,013,229 字节，33 个条目）：

| 应有 | wheel 内是否存在 |
|---|---|
| `.py` 源码（`nonebot_plugin_endfield/**`、`endfield/__init__.py`） | ✅ |
| `assets/fonts/*`（5 个，约 32 MB） | ✅ |
| `assets/templates/user_note.html` | ❌ |
| `assets/templates/table.json` | ❌ |
| `assets/img/*`（背景、图标、干员展示，3.7 MB） | ❌ |

原因在打包声明：`[tool.setuptools.package-data]` 只写了 `assets/fonts/*`（`pyproject.toml:45-46`），仓库又没有 `MANIFEST.in`。

后果（事实链，已在代码中确认）：`render_user_note_card` 找不到模板即 `raise FileNotFoundError`（`user_note.py:392-398`）→ `user_card.py:84-86` 捕获后回「生成信息卡失败」→ **pip 安装用户的「终末地信息卡」100% 不可用**；`table.json` 缺失则职业/属性图标为空（不致命，`user_note.py:220-235` 有兜底）；`img` 缺失则信息卡的背景/图标全部 404。

【推断】这是发布配置漏项，不是设计取舍；也解释了为什么 README 只教 `pip install` 而没人报「信息卡空白」——用户量太小（5 star）。

### 6.3 与 denia-bot 的版本对齐

| 依赖 | 官方插件要求 | denia-bot 现状 | 结论 |
|---|---|---|---|
| `nonebot2` | `>=2.3.0,<3.0.0` | `2.5.0`（`requirements.lock:56`） | ✅ 满足 |
| `nonebot-adapter-onebot` | `>=2.2.2,<3.0.0` | `2.4.6`（`requirements.lock:50`） | ✅ 满足（插件用到的 API 都在） |
| `nonebot-plugin-localstore` | `>=0.7.4,<1.0.0` | `0.7.4`（`requirements.lock:53`） | ✅ 正好卡下界 |
| `httpx` | `>=0.23.1,<1.0.0` | `0.28.1`（`requirements.lock:31`） | ✅ 满足 |
| `playwright` | `>=1.52.0` | **未安装**（venv 内不存在） | ❌ 新增大头 |
| `pyecharts` / `httpx-ws` | — | 未安装 | 不必装（零引用） |
| `numpy` | 2.x | `2.5.4` | ✅（但插件不用） |
| `Pillow` | 未声明 | `13.0.0` | 无交集；插件的图像全交给 Chromium |
| Python | `>=3.9` | `3.12.13`（`.venv/pyvenv.cfg`） | ✅（`numpy==1.26.0` 的分支用不到） |

事实：denia-bot 的 `requirements.lock` 里**没有** `aiofiles`，但 venv 里装了 `aiofiles 25.1.0` 且 `media_parser` 在用（`nonebot/media_parser/core/download.py:7`）→ 锁文件与实际环境不一致，属于既有问题，与本次调研无直接关系，但说明「锁文件即真相」在本仓库不成立，接入时要以 venv 为准。

### 6.4 部署成本（事实 + 推断）

| 项 | 成本 | 依据 |
|---|---|---|
| `pip install playwright` + `playwright install chromium` | 包体约数十 MB + Chromium 约 150 MB；需在镜像里单独加层 | 本插件启动即尝试自动安装（`nonebot_plugin_endfield/__init__.py:26-35`、`lib/renderers/runtime.py:112-118`）；AstrBot 版同样要求（`docs/research/endfield-upstream.md:387`） |
| 首启自动下载 | 插件会自己跑 `python -m playwright install chromium`，支持 `PLAYWRIGHT_DOWNLOAD_HOST` 与中国镜像（`runtime.py:57-84,112-118`） | 启动期间会阻塞该线程（已被 `to_thread` 隔离） |
| 资源体积 | 36 MB（可裁掉 16 MB 未引用的 Noto Regular） | §5.3 |
| denia 现有部署形态 | 已经有一套**远程** HTML→PNG 服务（`docker/playwright-renderer/`），机器人只发 HTML 收 PNG（`nonebot/disaster_render.py:1-5`） | 见 §7 |

---

## 7. 与 denia-bot 的兼容与冲突

| 维度 | 官方插件 | denia-bot 现状 | 冲突 / 处置（推断） |
|---|---|---|---|
| 框架 | NoneBot2 + OneBot v11 | 同 | ✅ 无 |
| 命令入口 | `on_command` 默认 `priority=1, block=False` | 自研插件 `priority=1~4, block=True`；MuiceBot 兜底 99/100 | ✅ 优先级安全；建议移植时统一 `block=True` |
| 权限 | 无（仅 1 条 superuser 手写判断） | `SUPERUSER` + `gate` 群聊闸门 | ⚠️ **gate 只把规则挂到 MuiceBot 的两个 matcher**（`nonebot/plugins/gate.py:185-199`），对外部 matcher 完全不生效 → 终末地命令会绕过「群聊 @only / 每人限额」 |
| superusers | 读 `driver.config.superusers`（`gacha_analysis.py:856`） | `plugins/superusers.py:47` 用 `data/superusers.json` 覆盖 `driver.config.superusers` | ✅ 兼容，但**依赖启动顺序**（superusers 先于命令被调用） |
| 渲染栈 | 本地 `sync_playwright` + `file://` 相对资源 | **远程** HTML→PNG（`disaster_render.py:1-5`，`docker/playwright-renderer/`），卡片自包含、渲染端禁 JS、拦外网 | ❌ 结构性冲突：远程端拿不到 `file://` 资源与外部字体，`<base href>` 方案失效；要么容器挂载 assets，要么改成 base64 内联（AstrBot 的 `_inline_assets` 思路） |
| Playwright 依赖 | 必需 | 完全没有（`requirements.lock` 无、venv 无） | ❌ 一期引入成本高，与既有研究结论「不建议一期引入」相左（`docs/research/endfield-integration.md` 结论表） |
| 存储 | localstore 平台目录 + sqlite | 仓库内 `nonebot/data/`（gitignored），自管 sqlite/json | ⚠️ 目录不一致；`LOCALSTORE_DATA_DIR` 是全局开关，会挪动 MuiceBot 数据 → 建议移植时改存储层 |
| 凭据落盘 | `framework_token` 明文 sqlite，无 0600 | 约定：`data/` 整体忽略 + 临时文件 `os.replace` + 0600 | ❌ 需改 |
| 后台任务 | 无 | 用 `asyncio.create_task` + 轮询（不用 apscheduler，`docs/research/endfield-integration.md`） | ✅ 互不干扰；但也意味着**没有任何自动签到/推送**可继承 |
| 自动加载 | entry point 声明 | `bot.py` 只 `load_plugin("muicebot")`，全仓无 `load_all_plugins`（`nonebot/bot.py:30`） | ⚠️ **pip 安装不会自动生效**；必须显式 `load_plugin("nonebot_plugin_endfield")`，或把源码放进 `nonebot/plugins/`（由 MuiceBot 加载器扫描，`muicebot/onebot.py:50`） |
| 与 MuiceBot 的上下文 | 无 | `statuscard`/`media_parser` 等已占 priority 1/3/4 | ✅ 无重名，无规则重叠 |

---

## 8. 复用路径对比

工作量均为【推断】，基于 3,899 行代码与 36 MB 资源、以及 denia 侧既有的渲染/存储约定。

| 方案 | 具体做法 | 工作量 | 维护成本 | 许可风险 | 与 denia 的冲突 |
|---|---|---|---|---|---|
| **A. pip 依赖，直接用** | `pip install nonebot-plugin-endfield` + 显式 `load_plugin` | 最低（约半天） | 跟上游，但上游 6 个月无提交 | 分发形态若不变，风险低—中 | ① **信息卡必挂**（wheel 缺模板/图片，§6.2）；② 需新增 playwright + Chromium；③ 数据落平台目录；④ 无 gate 覆盖 |
| **B. fork / 裁剪成项目内插件** | 拷源码进 `nonebot/plugins/`，删掉未用依赖、换存储与渲染、改命令名 | 中（3–5 天） | 自己维护，需跟上游安全修复 | **高**：fork 即衍生作品，denia（MIT）须整体 AGPL 或另获授权 | 仍需解决渲染栈与 gate；但可顺手修死代码/权限/日志泄漏 |
| **C. 只借鉴 client / store / render** | 按官方端点契约自写 `endfield_client.py` + 存储 + 图卡，参考其 HTML 设计 | 中（3–5 天，图卡越多越久） | 完全自控 | 低（不复制表达式） | 需自建渲染：denia 远程渲染只吃自包含 HTML，正好可承接 |
| **D. 只用协议契约** | 抽取 22 个端点 + 字段语义，作为「数据层规格」，渲染完全另做 | 低（1–2 天，端点已有两版盘点可对照） | 最低 | 最低 | 无 |

关键比较（事实约束）：

- A 的「最低成本」是假象：装完信息卡不可用，6 个出图命令里只剩 5 个（干员列表/抽卡记录/抽卡分析/全服统计/公告可用，信息卡不可用）。
- B 与 C 的分界不是工作量，而是**许可证**：B 必然衍生，C 大概率不衍生。在许可证澄清前，C/D 是唯一无条件安全的路。
- 无论哪条路，**渲染栈都要重做**：denia 没有本地 Chromium，而插件的 6 个图卡命令全部依赖它。这是真正的成本大头，不是代码量。

---

## 9. 对基线决策的含义（只列事实与含义）

### 9.1 AstrBot 全量移植

| 事实 | 含义 |
|---|---|
| 31 个注册点、`main.py` 6,122 行、16 个在用模板、12 MB 资源（`docs/research/endfield-upstream.md:11-19,301-326`） | 功能最全，但要重写消息收发、4 个后台任务、全部 handler |
| 渲染层 `core/render.py` 有 `_inline_assets`（把 CSS/图片转 base64）与 art-template→Jinja2 兼容层（`endfield-upstream.md:287-300`） | 这套「自包含 HTML」思路恰好匹配 denia 的远程渲染；但模板是 AGPL 衍生物 |
| AGPL-3.0（`endfield-upstream.md:375-379`） | 与官方 NoneBot 版同一个许可证问题，不能「换个上游就没风险」 |
| 功能面覆盖便签/理智/合约/成就/日历/建设/订阅/自动签到 | 与官方插件相比，是**多一倍功能**，也是**多一倍重写量** |

### 9.2 官方 NoneBot 二次开发

| 事实 | 含义 |
|---|---|
| 已是 NoneBot2 + OneBot v11 原生实现，事件模型与 denia 一致（§3.3） | 接线成本最低，命令层几乎可用 |
| 缺 10 类图卡与全部后台任务（§2.1） | 「二次开发」很快会退化成「补功能」，补的部分照样要自己写 |
| wheel 缺资源、无测试、6 个月无提交、5 star（§1.1、§6.2） | 上游不可依赖；实质等于自己维护一个 fork |
| 死代码与缺陷：健康检查未注册且漏 `await`、`spaceship` 请求被丢弃、API Key 进日志（`get_last_announce.py:37`）、签到失败静默（§2.1、§3.3） | 拿来即用会把这些问题一并继承 |

### 9.3 混合（协议自写 + 图卡择一移植 + 渲染走 denia 远程服务）

| 事实 | 含义 |
|---|---|
| 两份上游盘点的端点高度重叠，同一网关 `end-api.shallow.ink`（本文 §4.2 与 `endfield-upstream.md:76-139`） | 客户端只需自写一次，就能同时支撑「对标 AstrBot」的功能面 |
| denia 已有远程 HTML→PNG（`disaster_render.py:1-5`） | 渲染只需产出**自包含 HTML**（内联 CSS/字体/图片），这正是 AstrBot 内联方案的形态，而非官方插件的 `file://` 方案 |
| denia 约定 `xxx_logic.py` 纯逻辑 + 薄接线（`docs/research/endfield-integration.md`） | 新写代码天然适配；移植代码则处处要拆 |
| 图卡视觉可参考两份上游，但代码不能照抄（AGPL） | 需要「重画」而不是「搬图」 |

### 9.4 三方案的取舍要点（各 3–5 条，供 #35 决策）

**AstrBot 全量移植**
1. 得：功能面最全（唯一覆盖订阅/自动签到/便签/理智/合约）。
2. 失：6,122 行的 `main.py`（handler + 后台任务）+ 16 张模板全部要重写，工作量最大。
3. 险：AGPL 与官方版相同；模板/资源是衍生作品。
4. 险：AstrBot 专属 API（`event.image_result`、`filter.command`）不能直接复用。
5. 变数：AstrBot 版是团队主力（66 star、2026-08 仍在更新），后续功能会继续拉大差距。

**官方 NoneBot 二次开发**
1. 得：事件模型、适配器、框架版本全部对齐，接线最快。
2. 失：功能只有对方一半；补功能的工作量会追平「重写」。
3. 险：上游已停滞（5 star、6 个月无提交、无测试、发布产物缺资源），实际是自维护 fork。
4. 险：许可证冲突同上；fork 后必须 AGPL 化或取授权。
5. 变数：其 `fmt 绑定` 与「批量同步全部账号（superuser）」是 AstrBot 没有的显式命令，值得保留。

**混合（协议自写 + 选择性移植）**
1. 得：许可证干净（不复制代码），可先做「最小可用」再逐步加卡。
2. 得：可复用 denia 既有远程渲染与 `xxx_logic.py` 约定，不必引入本地 Chromium。
3. 失：所有图卡都要重画 HTML，短期出图量少于「直接搬」。
4. 险：自写客户端要自己承担网关字段变更（上游无契约文档，只有两份逆向盘点）。
5. 变数：若运营方能给出 MIT 双授权，混合方案里「移植图卡」的成本会显著下降。

---

## 10. 未解问题

| # | 问题 | 为什么仓库内无法回答 | 建议核对方式 |
|---|---|---|---|
| 1 | 许可证到底是 AGPL 还是 MIT | 两套声明互相矛盾，且 MIT 自 0.0.2 起就存在，无法判断是遗留还是本意 | **需运营方确认**：向 `Entropy-Increase-Team` 要书面澄清或 MIT 双授权 |
| 2 | PyPI 0.2.5 与 HEAD `526dc466` 是否同一份代码 | 浅克隆只有 1 个提交；0.2.5 上传于 2026-05-07，而 HEAD 提交于 2026-03-31 | 比对 sdist 与 GitHub 归档的 diff |
| 3 | `end.shallow.ink` 的 Key 是否对 denia 开放、限流与 SLA | 网关是第三方服务，仓库只有默认地址 | 运营方申请试用；观察 `/health` 与 429/5xx 行为 |
| 4 | 上游是否知道 wheel 缺模板/图片 | 无 issue、无 CHANGELOG | 提 issue 或 PR 修 `package-data`；若上游接受，A 方案可用性提升 |
| 5 | 信息卡模板的正则替换在真实数据下的失败率 | 无测试、无 fixture，占位文本写死 | 用 3–5 个真实账号数据跑一遍（需 API Key） |
| 6 | 并发渲染的内存峰值 | 无压测数据 | 本地压测 5/10 并发截图，观察 RSS |
| 7 | `COMMAND_START` 含空串时，@ 前缀消息是否触发 `on_command` | 依赖适配器对 `extract_plain_text` 的处理，未实测 | 在 denia 测试群发「@bot 签到」验证 |
| 8 | 迁移后 gate 是否应覆盖终末地 matcher | 属产品决策，非事实问题 | 交 #35 决策；技术上可仿 `gate.py:194-198` 的 `matcher.rule & gate_rule` |

---

## 附录 A：核对方法（可复现）

```bash
# 许可证
head -3 LICENSE; sed -n '100,108p' README.md; sed -n '14p' pyproject.toml
curl -s https://api.github.com/repos/Entropy-Increase-Team/nonebot-plugin-endfield | jq .license
curl -s https://pypi.org/pypi/nonebot-plugin-endfield/json | jq .info.license_expression

# 发布产物缺资源（关键证据）
pip download nonebot-plugin-endfield==0.2.5 --no-deps -d /tmp/whl
python -m zipfile -l /tmp/whl/*.whl | grep assets        # 只有 fonts/

# 依赖是否真被使用
grep -rniE "pyecharts|httpx_ws|numpy|aiofiles|apscheduler" --include=*.py .

# 同步 Playwright 的调用点是否都在线程里
grep -rn "to_thread\|render_" nonebot_plugin_endfield/command/*.py

# denia 侧
grep -n "playwright\|pyecharts\|aiofiles" requirements.lock
sed -n '1,5p' nonebot/disaster_render.py
grep -n "COMMAND_START" nonebot/.env
```

## 附录 B：本文引用的上游文件行号索引

| 主题 | 位置 |
|---|---|
| 许可证 / 版本 / 入口 | `LICENSE:1-3`、`README.md:105-107`、`pyproject.toml:7,14,19-32,38-40,45-46` |
| 元数据 / require / 启动钩子 / 命令门控 | `nonebot_plugin_endfield/__init__.py:6,11-20,26-35,38-43` |
| 别名 shim | `endfield/__init__.py:1-2` |
| 命令注册 | `command/help.py:4`、`user_bind.py:23-25`、`user_card.py:14`、`user_char_list.py:14`、`user_signin.py:13`、`get_last_announce.py:14`、`gacha_analysis.py:29-33`、`health_check.py:5`、`command/__init__.py:1` |
| 扫码状态机 | `user_bind.py:358-441`（轮询 150×2 s 在 `401`） |
| 绑定表 / 迁移 / 切换 | `user_bind.py:107-131,134-246,274-304,346-355` |
| HTTP 客户端 | `lib/api.py:8-19,22-30,33-67` |
| 存储与当前账号 | `lib/utils.py:15,21-40,48-63,66-117` |
| Playwright 运行时 | `lib/renderers/runtime.py:9,13,35,57-84,87-146,149-254,257-310,291-304` |
| 信息卡 | `lib/renderers/user_note.py:119,215-235,244-280,325-391,392-398,481` |
| 抽卡图 / 图标缓存 | `lib/renderers/gacha.py:42-46,114-182,209,505,570` |
| 公告图 | `lib/renderers/announce.py:85-91` |
| 资源 | `assets/templates/user_note.html:8-20,67,751-839` |
