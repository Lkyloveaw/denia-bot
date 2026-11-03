# denia-bot

基于 NoneBot2、OneBot v11 和 MuiceBot 的 QQ 机器人。项目提供人格聊天、可选的知识库检索，以及游戏活动、天气、灾害预警、终末地账号查询等功能。QQ 协议端、模型服务和图片渲染服务需要分别部署。

> 本文描述当前工作区的功能。当前分支、GitHub `main` 和服务器部署可能包含不同版本；部署前请核对目标检出的代码和配置。部分功能文件当前尚未提交，干净检出不一定包含本文所有功能。

## 功能

| 功能 | 说明 |
| --- | --- |
| 人格聊天 | MuiceBot 对接模型；支持模型首选和故障切换、图片理解、按会话隔离及群聊限流。 |
| 知识库与记忆 | 可选 RAG 检索注入和长期记忆；语料及模型服务需要单独配置。 |
| 链接解析 | 解析受支持平台的链接，发送预览卡片和原始媒体；平台和网络状态会影响结果。 |
| 天气和灾害预警 | 天气查询及定时推送；灾害 V1 覆盖 Jian 气象、海啸和 Wolfx 地震，EQSC／台风不在当前验收范围。 |
| 游戏活动 | 管理不同游戏和区服的活动订阅、查询与到期提醒；初始无订阅，支持按会话限制。 |
| Epic 喜加一 | 查询免费游戏及配置换期推送；主动查询限主人、群主或群管理员，未授权时静默。 |
| 终末地 | 网页授权后查询便签、理智、签到、抽卡记录和干员信息，也支持订阅提醒。扫码绑定尚未开放；部分接口需真实账号验收。 |
| 服务器状态 | 状态卡、资源及服务告警、日报和周报。 |
| 管理面板 | 管理超级管理员、功能配置、推送目标、图片渲染服务和运行状态。 |

默认命令前缀为 `.`，发送 `.help` 查看当前加载插件的命令。群聊查询通常需要 @ 机器人；各功能的权限、会话范围和配置以帮助菜单及对应文档为准。未知命令和无权限命令按静默策略处理。

## 架构

```text
QQ 协议端 ── OneBot v11 反向 WebSocket ──> NoneBot2 + MuiceBot
                                                  ├── 模型服务 / 网关
                                                  ├── 可选 RAG 检索服务
                                                  ├── 游戏与公共数据接口
                                                  └── 图片渲染服务

管理面板 ──> 本地配置和状态文件 ──> 机器人进程
```

机器人、协议端、面板、RAG 和渲染器各自运行。生产环境应限制监听地址，并保护配置、数据库、账号数据和日志。

## 自研插件

每个插件都是 **纯逻辑层 + 接线层** 两层：`xxx_logic.py` 不 import nonebot/muicebot，
所以 `tests/test_xxx_logic.py` 可以秒级跑完；`plugins/xxx.py` 只做 hook/rule 接线。

| 插件 | 解决什么 |
|---|---|
| `gate` | 群聊闸门：只回 @ 的、每人每群每天有回话上限，超了**完全静默**；主人私聊可用「限流 …」改上限 |
| `kb` | 知识库检索注入：只在判定相关时注入设定片段；检索服务挂了就当没命中，不影响聊天 |
| `memory` | 长期记忆：按用户维度的语义召回与持久化抽取（详见 `docs/research/mnemosyne-memory.md`） |
| `model_pref` | 回话用哪个模型：首选 + 次选，首选这一回合没吐字则降级次选；也支持按上游真实模型名点名 |
| `prompt_guard` | 群名片被当成话题、历史重复/串群 —— 在调模型之前把提示词洗干净 |
| `quote_fix` | QQ「回复」引用会崩在 `muicebot/onebot.py`（UniMessage 收到框架原生 MessageSegment），槽住整条消息不回 |
| `name_guard` | 「问这个人叫什么」时协议端 API 没有超时 → 整条消息永远挂着；这里加超时兜底 |
| `master_aware` | 根据 QQ 身份区分主人与其他人，说话口径不同 |
| `group_topics` | 按群开关的近期话题与发言人数统计，**不记录消息原文** |
| `time_context` | 给模型提供当前时间和历史对话的时间戳 |
| `weather` | 天气查询 + 显式配置的定时推送 |
| `media_parser` | 多平台链接解析（在 AstrBot 原插件基础上适配到 NoneBot2） |
| `endfield` | 终末地：绑定/便签/理智/签到、抽卡/干员/危机/日历/成就/建设查询、图卡、公告与订阅；详见 `docs/endfield.md` |

管理面板：`nonebot/dashboard.py`（FastAPI + 单页 HTML），提供群话题、天气推送、主人设置等管理接口。
另有 `bot_monitor.py` 做只读状态检查与串行重启。终末地已接入主面板侧栏；`/endfield` 跳转到 `/#endfield`。

---

## 目录

```
denia-bot/
├─ nonebot/               NoneBot2 + MuiceBot 主体
│   ├─ bot.py             入口
│   ├─ *_logic.py         各插件的纯逻辑（可单测）
│   ├─ plugins/           插件接线层
│   ├─ tests/             pytest 用例（纯逻辑 + 接线）
│   ├─ configs/           models.yml.example / templates.yml
│   ├─ templates/         Jinja2 人格模板
│   ├─ dashboard.py|html  管理面板
│   └─ media_parser/      链路解析插件（适配版）
├─ rag/                   本地检索服务（serve.py / core.py / build_index.py / eval_gate.py）
├─ tools/                 启停脚本、账号隔离、自检脚本
├─ docker/snowluma/       QQ 协议端 compose 模板
├─ docs/                  设计文档与研究笔记
└─ requirements.lock      生产环境实测依赖锁
```

---

## 快速开始（Linux 服务器）

以下步骤适用于 Linux 和已有 OneBot v11 协议端的环境；需要 Python 3.12 和可用的模型服务。部署到其他环境时请按实际路径和网络修改。

```bash
git clone https://github.com/Lkyloveaw/denia-bot.git
cd denia-bot

python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock

cd nonebot
cp .env.example .env
cp configs/models.yml.example configs/models.yml
# 编辑 .env 和 configs/models.yml 后执行
python migrate_db.py upgrade
python bot.py
```

在 `.env` 中配置 `ONEBOT_ACCESS_TOKEN`、初始 `SUPERUSERS` 和监听地址；在 `configs/models.yml` 中配置模型地址、模型名和密钥。协议端的反向 WebSocket 地址指向 `ws://<机器人地址>:8080/onebot/v11/ws`，并使用相同的 OneBot token。初次运行后，超级管理员可在面板维护。

`configs/models.yml.example` 使用 `Denia` 模板；请根据实际可用的模板调整模型配置。人格内容、语料和账号凭据应自行准备，不要提交私有数据。

需要图片卡片时，按 [渲染服务说明](docker/playwright-renderer/README.md) 启动服务，默认地址为 `http://127.0.0.1:3100`。未配置或服务不可用时，具体功能按各自实现回退文本或原始媒体。

管理面板单独启动，例如在已激活虚拟环境的 `nonebot` 目录运行：

```bash
uvicorn dashboard:app --host 127.0.0.1 --port 8092
```

面板配置、协议端连接、RAG、systemd 和故障排查见 [运维手册](docs/OPERATIONS.md) 与 [常见问题](docs/PITFALLS.md)。远程访问建议使用 SSH 隧道或受保护的 HTTPS 反向代理。

## 功能说明

### 游戏活动

常用命令包括 `.活动游戏`、`.活动订阅 鸣潮`、`.活动查询` 和 `.活动退订 全部`。群主、管理员和主人可管理本群订阅，私聊用户管理自己的订阅；会话白名单、黑名单和群聊／私聊开关在面板配置。详见 [游戏活动运维](docs/OPERATIONS.md#87-游戏活动日历)。

### Epic 喜加一

`.喜加一` 查询当前和即将免费的游戏；`.Epic 测试` 仅限主人。面板可配置地区、轮询间隔与群聊／私聊推送目标。首装建立基线，之后根据免费游戏集合变化推送；通常先发送卡片或文本，再发送一条链接汇总。详见 [Epic 运维](docs/OPERATIONS.md#89-epic-喜加一)。

### 终末地

在私聊发送 `.授权登陆`，按提示同意并打开授权链接，完成后再次发送该命令。绑定后可使用 `.便签`、`.抽卡记录` 和 `.抽卡分析` 等命令；抽卡数据需先尝试 `.同步抽卡`。抽卡分析展示网关返回的总抽数、六星数量和六星率，统计口径由网关决定。

当前工作区支持网关创建绑定失败时的本地绑定回退：授权凭据经只读便签查询验证后才保存；该路径不代表网关已新建绑定。授权、同步及其他查询能否使用仍取决于对应网关接口。扫码绑定、上游专用卡片等未完全移植。配置、权限、数据保存和已知限制见 [终末地文档](docs/endfield.md)。

### 灾害预警

V1 的验收范围为 Jian 气象／海啸和 Wolfx 地震。验收、观察和回滚步骤见 [灾害验收 runbook](docs/research/disaster-acceptance.md)；实际进度以 GitHub Issue #39 的最新记录和用户签字为准，不应将合成样例视为真实灾害事件验证。

## 开发与测试

```bash
# 在仓库根目录，虚拟环境已激活
pip install -r requirements.lock -r requirements-dev.txt
cd nonebot
python -m pytest tests -q
```

测试包含纯逻辑和 NoneBot 接线测试。真实 QQ、模型服务、终末地授权、外部数据源及渲染器的验收需分别执行；离线测试通过不能替代真实服务验证。

主要目录：

| 路径 | 内容 |
| --- | --- |
| `nonebot/bot.py`、`nonebot/plugins/` | 机器人入口和插件接线 |
| `nonebot/*_logic.py`、`nonebot/endfield/` | 功能逻辑、终末地客户端与状态管理 |
| `nonebot/dashboard.py`、`nonebot/dashboard.html` | 管理面板 |
| `nonebot/media_parser/` | 链接解析 |
| `nonebot/tests/` | 自动化测试 |
| `rag/` | 可选知识库检索服务 |
| `docker/playwright-renderer/` | 图片渲染服务 |
| `docs/`、`tools/` | 运维、研究文档和工具 |

## 数据与安全

- `.env`、`configs/models.yml`、协议端配置、令牌、数据库、日志及 `nonebot/data/` 中的用户数据不要提交。
- 终末地绑定文件包含凭据；备份时限制权限，删除本地绑定不一定撤销第三方授权。
- 默认将服务限制在本机或可信网络；不要直接将无保护的面板、OneBot 和渲染接口暴露到公网。
- 使用非官方 QQ 协议端存在账号风险，协议端的安装和账号管理由部署者自行负责。

## 许可与来源

仓库原创代码以 [MIT](LICENSE) 许可发布。第三方依赖保留各自许可；人格模板和语料的权利不因本仓库代码许可而改变。

- 终末地 `nonebot/endfield/client.py`、`store.py`、`render.py` 为 AGPL-3.0-only 同源移植，见 [AGPL 许可](LICENSE-AGPL-3.0) 和 [来源说明](docs/third-party/endfield-NOTICE.md)。
- `nonebot/status_*.py`、`nonebot/plugins/statuscard.py` 及其测试移植自 `SX0YYYY/astrbot_plugin_statuscard`，按 AGPL-3.0 分发，见 [许可](docs/third-party/statuscard-LICENSE) 和 [来源说明](docs/third-party/statuscard-NOTICE.md)。
- `nonebot/disaster_*.py`、`nonebot/plugins/disaster_warning.py` 及相关测试移植或重写自 `Pancakes-Labs/astrbot_plugin_disaster_warning`，按 AGPL-3.0 分发，见 [许可](docs/third-party/disaster-warning-LICENSE) 和 [来源说明](docs/third-party/disaster-warning-NOTICE.md)。
- Epic 相关文件参考 `CJSen/astrbot_plugin_epic_free_games_notice` 的行为和接口事实独立重写；上游 README 声明 AGPL-3.0，但未附 LICENSE 文件。本项目按 AGPL-3.0 保守分发，见 [许可](docs/third-party/epicfree-LICENSE)、[来源说明](docs/third-party/epicfree-NOTICE.md) 和 [署名](docs/third-party/epicfree-ATTRIBUTION.md)。

分发包含上述文件的组合时，请同时考虑其相应许可要求。
