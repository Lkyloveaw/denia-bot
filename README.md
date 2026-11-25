# denia-bot

**可自托管的 QQ 机器人：人格聊天、群聊工具和订阅推送。**

[核心能力](#核心能力) · [Linux 快速开始](#快速开始linux-服务器) · [Windows 本机开发](#windows-本机开发) · [运维手册](docs/OPERATIONS.md) · [版本发布](https://github.com/Lkyloveaw/denia-bot/releases)

## 项目简介

Denia Bot 面向希望自行管理机器人的 QQ 群管理员和个人部署者。接入 OneBot v11 协议端与自己的模型服务后，可以在群聊和私聊使用人格聊天，在 Web 管理面板调整人格与功能配置，并为不同会话设置活动提醒、B 站动态和服务器告警的接收目标。

机器人基于 NoneBot2 和 MuiceBot，QQ 协议端与模型服务需独立配置；知识库检索、游戏账号查询和 Playwright 图片渲染可按需部署。

> 当前版本标识为 **V0.1.0-RC1**（`0.1.0rc1`）。本页描述当前源码，功能不一定都包含在已发布的源码包中；部署前请核对目标版本和 [更新记录](CHANGELOG.md)。账号、密钥及本地数据的保存与备份见 [数据与安全](#数据与安全)。

## 核心能力

| 功能 | 说明 |
| --- | --- |
| 人格聊天 | MuiceBot 对接模型；支持模型首选和故障切换、图片理解、按会话隔离及群聊限流。 |
| 群聊工具 | 默认只回应 @ 机器人的群消息，每人每群按日限制回话次数，超限完全静默；主人可私聊「限流」调整。群话题统计按群开关，只记关键词和发言次数，不记录消息原文。 |
| 表情包 | 管理面板上传、分类、一键导入 `nonebot/data/stickers`、自动打标签/权重及设置会话范围，由聊天模型按场景选择发送。 |
| 知识库与记忆 | 可选 RAG 检索注入和长期记忆；RAG 只走 SiliconFlow `BAAI/bge-m3`，需要单独配置 API Key 和云端索引。 |
| 链接解析 | 解析受支持平台的链接，发送预览卡片和原始媒体；平台和网络状态会影响结果。 |
| 天气和灾害预警 | 天气查询及定时推送；灾害 V1 覆盖 Jian 气象、海啸和 Wolfx 地震，EQSC／台风不在当前验收范围。 |
| 游戏活动 | 管理不同游戏和区服的活动订阅、查询与到期提醒；初始无订阅，支持按会话限制。 |
| 游戏账号查询 | 可选：接入 gsuid_core 服务（127.0.0.1:8765），提供鸣潮、终末地的账号查询与签到，需按 `gsuid/README.md` 单独安装并启动。 |
| Epic 喜加一 | 查询免费游戏及配置换期推送；主动查询限主人、群主或群管理员，未授权时静默。 |
| 服务器状态 | 状态卡、Proxmox VE 集群状态卡、资源及服务告警、日报和周报；面板同时显示 CPU 核心、主机、系统与依赖版本。 |
| 运行日志 | 按来源、级别和关键词筛选运行日志/解析事件，支持暂停、复制、下载和 WebUI 模型对话测试。 |
| B站动态订阅 | 主人按会话订阅 UP 主动态，机器人以卡片推送（视频动态带封面）；管理面板展示各会话订阅与投递状态，并可增删接收目标。 |
| bot基本信息 | 在管理面板设置状态卡、日报、集群卡、灾害卡和指令菜单的显示名，保存后立即生效。 |
| 插件管理 | 首次登录管理面板会先引导完成 Bot 基本设置，再通过引导选择可选插件；插件引导的「完成并重启bot」会保存开关并重启机器人，「稍后设置」只保存，之后可手动重启。之后也可在面板中调整。核心插件（含 QQ 超级管理员和主人识别）不可关闭；禁用的插件在机器人下次启动时不再加载。切换 gsuid 时，面板会尝试启停已识别的 gsuid_core 服务，不会联动共享的 RAG 或 Playwright 服务。 |
| 管理面板 | 管理超级管理员、功能配置、推送目标、图片渲染服务和运行状态。 |

默认命令前缀为 `.`，发送 `.help` 查看当前加载插件的命令。群聊查询通常需要 @ 机器人；各功能的权限、会话范围和配置以帮助菜单及对应文档为准。未知命令和无权限命令按静默策略处理。

## 架构

```text
QQ 协议端 ── OneBot v11 反向 WebSocket ──> NoneBot2 + MuiceBot
                                                  ├── 模型服务 / 网关
                                                  ├── 可选 RAG 检索服务
                                                  ├── gsuid_core（可选，8765）
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
| `meme` | 自定义表情包：在面板管理分类和图片，聊天模型可按场景选择分类并发送。 |
| `model_pref` | 回话用哪个模型：首选 + 次选，首选这一回合没吐字则降级次选；也支持按上游真实模型名点名 |
| `reply_guard` | 去掉模型未加标签的自述计划，避免思考链进入聊天与历史 |
| `prompt_guard` | 群名片被当成话题、历史重复/串群 —— 在调模型之前把提示词洗干净 |
| `quote_fix` | QQ「回复」引用会崩在 `muicebot/onebot.py`（UniMessage 收到框架原生 MessageSegment），槽住整条消息不回 |
| `name_guard` | 「问这个人叫什么」时协议端 API 没有超时 → 整条消息永远挂着；这里加超时兜底 |
| `master_aware` | 根据 QQ 身份区分主人与其他人，说话口径不同 |
| `group_topics` | 按群开关的近期话题与发言人数统计，**不记录消息原文** |
| `time_context` | 提供北京时间和历史对话时间戳，并为可明确识别的相对时间问题提供计算结果 |
| `weather` | 天气查询 + 显式配置的定时推送 |
| `media_parser` | 多平台链接解析（在 AstrBot 原插件基础上适配到 NoneBot2） |
| `bilibili` | B站动态订阅：按会话订阅 UP 主动态并推送卡片（仅主人增删）、关键词搜索；逻辑在 `nonebot/bilibili/`，面板展示订阅与投递状态并可增删接收目标。详见 `docs/OPERATIONS.md` §8.10 |

管理面板：`nonebot/dashboard.py`（FastAPI + 单页 HTML），提供群话题、天气推送、主人设置、B站动态接收目标等管理接口。
侧栏优先显示 Bot 基本设置、表情包、用户记忆、群聊话题和服务器状态。在「Bot 基本设置」内切换基本信息、人格模板、模型设置、主人识别、超级管理员和 Token 消耗；`/#persona` 等原有链接仍可直达，前进、后退不重新加载页面。首次启动完成管理员密码设置后，面板会自动进入「Bot 基本设置」并显示分步引导（第 1/5 步至第 5/5 步），用「上一步 / 下一步」依次完成 Bot 名称、人格模板、模型设置、主人识别和超级管理员。
「服务器状态」的检测总开关默认开启。关闭会暂停服务器资源采样、服务探测、告警、报表和手动检测，包括 QQ 状态查询；不会清除历史数据或子项设置。可在同一栏目重新开启，聊天和独立的「运行状态」检查不受影响。打开后上方显示主机、系统、CPU 核心、内存/SWAP、本机可执行文件与依赖版本，下面保留历史趋势、磁盘、进程、异常和状态卡配置。
另有 `bot_monitor.py` 做只读状态检查与串行重启。

表情包在管理面板的「表情包」中添加分类、填写使用场景并上传图片。支持 PNG、JPEG、GIF、WebP，单张不超过 5 MB；空图库不会影响聊天。也可以一键扫描 `nonebot/data/stickers`：按文件夹建立分类，自动生成角色、场景、风格和情绪标签，并按分组写入统一权重；重复图片会跳过，单张超过 5 MB 或格式不支持会列在错误摘要中。标签和权重可逐张修改，`Library.choose()` 会按权重和标签偏好选图。模型只在认为合适时选择分类；已有语义向量的图片可按对话匹配，其他情况按元数据权重随机选取。可关闭全部表情或按 `group:群号`、`private:QQ号` 禁用会话；设置与图片保存在 `nonebot/data/memes/library.sqlite3`，需随机器人数据一同备份。首次部署代码后需重启机器人及面板，之后图片与规则改动无需重启。

主人可私聊发送「开启表情包学习」，之后发来的图片会加入后台队列，使用视觉模型识别内容和使用场景，再通过向量服务建立索引并分类入库；发送「关闭表情包学习」停止接收新图，已接收任务继续处理。发送「表情包学习状态」查看进度，失败后可用「表情包学习重试」；确认不再需要失败图片时可发送「表情包学习清理失败 确认」。学习模式仅接受配置中的主人私聊，开启状态跨重启保留。QQ 商城表情依赖协议端提供可获取的图片；无法读取、超过 5 MB 或队列满时会提示。动态图的识别使用第一帧，保存和发送保留原图。视觉模型默认 `deepseek-v4.1-flash`，可通过 `.env` 的 `MEME_VISION_MODEL` 修改；使用当前聊天模型的网关，调用视觉和向量服务可能产生费用。发送表情时优先按当前对话与已学习图片的描述匹配，向量服务不可用时回退原有随机选图。

## 外部插件：gsuid_core

`gsuid_core` 是可选的游戏账号查询服务：独立进程监听 `127.0.0.1:8765`，NoneBot 通过适配器 `nonebot-plugin-genshinuid==6.1.1`（模块 `GenshinUID`）连接。不启动它时其余功能不受影响；启用后按 `gsuid/README.md` 安装核心与插件，再在 `nonebot/.env` 设置 `GSUID_CORE_*` 连接参数。

| 组件 | 作用 | 上游仓库 | 固定版本 | 许可证 |
|---|---|---|---|---|
| `gsuid_core` | 核心服务：账号绑定、数据存储与插件调度，监听 8765 | <https://github.com/Genshin-bots/gsuid_core> | 提交 `aaa8bd6a6a1061d624e72ffaa547a3f1baa33931` | GPL-3.0 |
| `XutheringWavesUID` | 鸣潮账号绑定与查询：面板、评分与伤害计算 | <https://github.com/Loping151/XutheringWavesUID> | 提交 `a17e3154a89f63cb8ce2ef176b07880613d33582` | GPL-3.0 |
| `EndUID` | 明日方舟：终末地账号绑定与查询 | <https://github.com/Loping151/EndUID> | 提交 `a6fff57a527400bd008a85fbf72e49ddac849a14` | GPL-3.0 |
| `RoverSign` | 鸣潮、战双、库街区签到（依赖 `XutheringWavesUID`） | <https://github.com/Loping151/RoverSign> | 提交 `98be9d36144b46471c5094dd751f8f3b63d623bf` | GPL-3.0 |
| `nonebot-plugin-genshinuid` | NoneBot2 与核心的 WebSocket 适配器（模块 `GenshinUID`） | <https://github.com/KimigaiiWuyi/GenshinUID/tree/v4-nonebot2> | PyPI `6.1.1`（分支 `v4-nonebot2`） | GPL-3.0 |

安装与启动：

1. 运行 `gsuid/install.sh`（Linux）或 `gsuid/install.ps1`（Windows），把核心与三个插件按上表提交克隆到 `gsuid/core`，插件位于 `gsuid/core/gsuid_core/plugins`。
2. 启动核心（`cd gsuid/core && uv run core`，或用 compose 服务），首次启动后在控制台完成插件配置。
3. 适配器已固定在 `requirements.lock`（`nonebot-plugin-genshinuid==6.1.1`），按快速开始安装依赖即可。在 `nonebot/.env` 设置 `GSUID_CORE_HOST`、`GSUID_CORE_PORT=8765` 和 `GSUID_CORE_WS_TOKEN`（与控制台里的一致），再重启机器人；核心未启动时适配器只报连接失败。

上述来源均为 GPL-3.0，代码不随本仓库分发，安装脚本只在部署时拉取。配置与账号数据在 `gsuid/core/data`，需随机器人数据一起备份。生产环境应部署在 Linux bot 服务器（`/opt/qqbot`，systemd）；仓库里的 `tools\启动所有.bat` / `tools\gsuid-局域网访问.ps1` 只用于 Windows 本机开发，具体见 [gsuid 安装说明](gsuid/README.md) 与 [第三方来源与许可](docs/third-party/gsuid-NOTICE.md)。

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
├─ rag/                   云端检索服务（serve_cloud.py / rebuild_index.py / core.py；legacy-local 不支持的旧 ONNX）
├─ deploy-linux/          Linux 一键部署：venv/依赖、配置、systemd、SnowLuma Docker
├─ tools/                 启停脚本、账号隔离、自检脚本
├─ docker/snowluma/       QQ 协议端 compose 模板
├─ gsuid/                 可选游戏账号查询：gsuid_core 核心与插件的安装脚本
├─ docs/                  设计文档与研究笔记
└─ requirements.lock      生产环境实测依赖锁
```

---

## 快速开始（Linux 服务器）

### 一键部署（推荐）

Linux 默认使用 Docker 跑 SnowLuma、systemd 跑 NoneBot/面板/gsuid。脚本会创建 `.venv`、安装 `requirements.lock`、复制缺失配置、生成 OneBot token 和 SnowLuma VNC 密码、执行迁移和面板初始化，并安装 systemd 单元：

```bash
git clone https://github.com/Lkyloveaw/denia-bot.git
cd denia-bot
sudo bash deploy-linux/install.sh
sudo bash deploy-linux/start.sh
```

首次安装后按 [deploy-linux/README.md](deploy-linux/README.md) 完成 SnowLuma 扫码和面板首次登录。`stop.sh` / `status.sh` 分别用于停止和查看状态；`install.sh --check` / `--dry-run` 可在不改系统的前提下检查。

### 手动步骤

以下步骤适用于已有 OneBot v11 协议端、或不想使用 systemd 的环境；需要 Python 3.12 和可用的模型服务。部署到其他环境时请按实际路径和网络修改。

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
python dashboard.py --init    # 首次部署：生成面板密码，只写认证文件
python bot.py
```

`python bot.py` 启动后，日志里会打印一行 `[dashboard] 管理面板: http://127.0.0.1:8092（独立进程，需单独启动）`。地址按 `DASHBOARD_HOST` / `DASHBOARD_PORT` 生成，未设置时就是默认回环地址；Linux 或直接运行 `bot.py` 时面板仍需按下面「管理面板启动」的命令另行启动，Windows 一键启动脚本会自动拉起它。Linux 上可用 `journalctl -u qqbot -n 50 | grep dashboard` 查看。

在 `.env` 中配置 `ONEBOT_ACCESS_TOKEN`、初始 `SUPERUSERS` 和监听地址；在 `configs/models.yml` 中配置模型地址、模型名和密钥。协议端的反向 WebSocket 地址指向 `ws://<机器人地址>:8080/onebot/v11/ws`，并使用相同的 OneBot token。初次运行后，超级管理员可在面板维护。

`dashboard.py --init` 只创建 `nonebot/data/dashboard-auth.json`（0600，只存 scrypt 盐和哈希）并在同目录写一份 0600 的 `dashboard-initial-password`，明文密码只在终端显示这一次；加 `--password` 可指定初始密码（12–256 位），已有认证文件时拒绝覆盖。**没有初始化就启动面板，登录不会成功**：`POST /api/login` 返回 401「密码错误或后台尚未配置」，其他管理接口返回 401「请登录」。首次登录后必须修改密码，改完一次性密码文件和所有会话一起失效。改密成功后会在同目录写入 `dashboard-onboarding.json`；下次登录时面板自动进入「Bot 基本设置」并显示分步引导，完成或跳过后不再出现。

`configs/models.yml.example` 使用 `Denia` 模板；请根据实际可用的模板调整模型配置。人格内容、语料和账号凭据应自行准备，不要提交私有数据。

需要图片卡片时，按 [渲染服务说明](docker/playwright-renderer/README.md) 构建并启动可选的 Playwright 渲染服务，默认地址为 `http://127.0.0.1:3100`：

```bash
cd docker/playwright-renderer
docker compose build
docker compose up -d
curl --fail http://127.0.0.1:3100/health
```

首次部署的 Python 依赖安装不会自动安装 Playwright 浏览器；Windows 的 `启动所有` 脚本也不会自动启动这个服务。没有渲染服务时，具体功能按各自实现回退文本或原始媒体。

管理面板启动（Linux 或直接运行 `bot.py` 时手动启动；Windows 一键启动已包含），例如在已激活虚拟环境的 `nonebot` 目录运行：

```bash
uvicorn dashboard:app --host 127.0.0.1 --port 8092
```

默认地址是 `http://127.0.0.1:8092`。需要绑内网 IP 或换端口时用进程环境变量 `DASHBOARD_HOST` / `DASHBOARD_PORT` 覆盖（写在 `.env` 里不生效；Linux systemd 通过 drop-in 注入，见运维手册 §10）；机器人启动日志读同一组变量，所以两个进程都配置时日志显示的地址与实际监听一致。

管理面板「运行状态 → Bot 更新」可以检查当前 Git 远端并执行 `git pull --ff-only`。更新只允许在干净的工作区、无本地领先提交时进行，成功后自动重启机器人，不执行 `git clean`，也不会改写被忽略的人格、模型和数据文件。它不会自动执行 `pip install` 或数据库迁移；如果依赖锁或迁移脚本有变化，面板会提示。面板进程本身不会被自动重启，面板代码的新版本需要单独重启面板后生效。

### 发布与打包

推送 `v*` 标签会触发 `.github/workflows/release.yml`：先校验标签版本与 `nonebot/app_version.py` 的 `PACKAGE_VERSION` 一致并编译应用，再用 `git archive` 生成只包含 Git 跟踪文件的 `tar.gz`、`zip` 和 `SHA256SUMS`，最后附加到对应 GitHub Release。也可在 Actions 页面手动运行并填写已有标签，例如 `v0.1.0rc1`。工作流不代替完整测试；发布前应在目标检出上单独运行 `python -m pytest tests -q`。

```bash
git tag -a v0.1.0rc1 -m 'V0.1.0-RC1'
git push origin v0.1.0rc1
```

发布包是应用源码部署包，不是 Python wheel：本项目依赖 `.env`、本地数据目录、可选 RAG 服务和独立 Playwright 渲染服务，不能仅靠 `pip install` 完成部署。

面板配置、协议端连接、RAG、systemd 和故障排查见 [运维手册](docs/OPERATIONS.md) 与 [常见问题](docs/PITFALLS.md)。远程访问建议使用 SSH 隧道或受保护的 HTTPS 反向代理。

### 可选：知识库检索（RAG）

RAG 唯一支持的路径是 **SiliconFlow `BAAI/bge-m3`**（1024 维，`siliconflow-bge-m3-v1`）。
`knowledge/*.md` 是仓库跟踪的语料；`rag/index-siliconflow/` 是
`rag/rebuild_index.py` 生成的可重建产物，已被 `.gitignore` 忽略，新检出没有索引是正常的。
本地 ONNX 方案（`serve.py` / `build_index.py` / 模型权重）已移到
`rag/legacy-local/`，仅作历史参考，不受支持，也不要把它的索引或向量接回服务。

需要准备：一个 SiliconFlow API Key（管理面板「知识库 Embedding」保存到
`nonebot/data/embedding_secrets.json`，权限 600）和云端索引。RAG 运行依赖不在
`requirements.lock` 里，单独建环境：

```bash
cd rag
python3.12 -m venv .venv
. .venv/bin/activate
pip install jieba numpy fastapi uvicorn
python rebuild_index.py --knowledge ../knowledge --output index-siliconflow
python serve_cloud.py --port 8091
```

`/health` 会返回 `embed_backend=siliconflow`、`model=BAAI/bge-m3` 和**实际加载的索引目录**
`index`；管理面板按它显示当前生效索引，不再猜最新目录。`serve_cloud.py` 支持
`KB_CLOUD_INDEX`、`KB_RAG_PORT`；`rag/siliconflow.py` 的超时由 `KB_EMBED_TIMEOUT`
控制（默认 20 秒），网络错误、429 和 5xx 有有限重试并记录 provider 错误。

长期记忆和表情学习在写入向量前会检查 `/health`：服务未就绪、后端不是 `siliconflow`
或模型不是 `BAAI/bge-m3` 时直接报错，不写混合向量。旧版本可能已经写入过按云端标签
保存的本地向量，升级后按 §7 的一次性迁移说明清理并重算。未启动检索服务时知识库插件
按「未命中」处理，不影响聊天。详见 [运维手册 §7](docs/OPERATIONS.md#7-知识库rag)。

## Windows 本机（开发）

Windows 上使用 `tools\启动所有.bat` / `tools\停止所有.bat` 启停；根目录的 `启动机器人.bat` / `停止机器人.bat` 只是调用它们的薄入口。脚本按自身位置解析检出目录，启动前先运行 `tools\startup-preflight.ps1`：检查 `nonebot\.venv` 和 SnowLuma 运行时，必要时用 Python 3.12 创建虚拟环境，并按 `requirements.lock` 安装/校验依赖；依赖检查失败时不会启动任何服务。依赖安装可能耗时，完成后才继续后续启动。启动前还会检查 8080 是否已有实例，并清理 `NO_PROXY` 中无法识别的 IPv6 回环条目（`::1` / `[::1]`），避免本机回环请求被系统代理或代理例外规则拦住。启动横幅和结尾的 connection info 都会打印管理面板地址（默认 `http://127.0.0.1:8092`，设置了 `DASHBOARD_HOST` / `DASHBOARD_PORT` 就照实显示）；启动器会拉起管理面板，停止器会一并停止它；直接运行 `bot.py` 或 Linux systemd 时面板仍是独立进程，需按上面的命令单独启动。直接运行启动脚本失败时控制台会暂停；根目录入口始终保留控制台。它们是本机开发/调试入口；生产部署仍走 Linux + systemd。

RAG 由启动器按云端路径拉起：只有 `rag\index-siliconflow`（或 `KB_CLOUD_INDEX`）和 `nonebot\data\embedding_secrets.json` 里的 `api_key` 都存在时才运行 `rag\serve_cloud.py`；缺任何一项都会明确打印「RAG 未启用（需要 SiliconFlow Key/索引）」，不会假装启动，也不会回退到本地 ONNX。停止脚本同时清理 `serve_cloud.py` 和残留的旧 `serve.py` 进程。

若 `nonebot\.venv\Scripts\python.exe` 缺失，脚本优先用 `runtime\python312\python.exe` 创建虚拟环境，不复用系统或 uv 的 Python。若缓存解释器也缺失，正常启动会从 GitHub 下载固定版本的 python-build-standalone 3.12.13（约 21 MB），校验 SHA256 后解压到临时目录，再安装到 `runtime\python312`。

若 `runtime\python312` 已存在却缺少 `python.exe`，脚本会在下载解压后停止，不会覆盖该目录；若缓存解释器存在但无法运行或不是 Python 3.12，则直接停止，不下载。遇到这两种情况，请删除 `runtime\python312` 后重试；若虚拟环境也损坏，或要强制重新下载，请同时删除 `runtime\python312` 和 `nonebot\.venv` 后正常启动。

SnowLuma 运行时不再要求放在仓库根目录的 `snowluma\`。preflight 按以下顺序查找：`-SnowLumaPath`（`启动所有.ps1` 支持）、`SNOWLUMA_HOME`、`QQBOT_SNOWLUMA`、仓库根 `snowluma\`、上级目录的 `SnowLuma\`；都没有时，自动解压 `SnowLuma-v2.4.2-win-x64.zip`（依次查找仓库根、`downloads\`、`tools\`、上级目录和 `%USERPROFILE%\Downloads`）。解压目标固定为仓库根 `snowluma\`，已有 `config\` / `data\` 不覆盖。解析结果写入 `logs\snowluma-home.txt`（UTF-8）和 `logs\snowluma-home.ansi.txt`（供批处理读取），启动和停止脚本都从这些文件读取实际路径；删除它们即可让下次启动重新探测。示例：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\启动所有.ps1 -SnowLumaPath "<SnowLuma 安装目录>"
```

或设置用户环境变量 `SNOWLUMA_HOME` 后直接运行 `启动所有.bat`（批处理不转发 `-SnowLumaPath`）。

`-CheckOnly` 和 `-SkipInstall` 都不会下载 Python，也不会解压 SnowLuma zip；需要 zip 回退时请正常启动。这两个开关仅供 `tools\startup-preflight.ps1` 使用，`tools\启动所有.bat` 不会转发。请在仓库根目录的 PowerShell 中按需运行其一：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\startup-preflight.ps1 -Root (Get-Location).Path -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File tools\startup-preflight.ps1 -Root (Get-Location).Path -SkipInstall
```

## 功能说明

### 模型降级

`MODEL_FAILOVER=1`（`.env.example` 的默认值）只表示允许降级，真正救场还需要先配置次选：私聊机器人发 `.模型 次选 <条目名|上游真模型名>`，再用 `.模型 查看` 确认显示的是具体条目，而不是「次选 未设置 —— 首选报错时不会降级」。没有次选时，首选返回 503 或一直不吐字可能表现为整条消息没有回复。次选必须与首选不同；超时由 `MODEL_FAILOVER_TIMEOUT` 控制，诊断日志按 `[model]` 过滤。详见 [运维手册 §6.1](docs/OPERATIONS.md#61-模型降级model_failover)。

### 游戏活动

常用命令包括 `.活动游戏`、`.活动订阅 鸣潮`、`.活动查询` 和 `.活动退订 全部`。群主、管理员和主人可管理本群订阅，私聊用户管理自己的订阅；会话白名单、黑名单和群聊／私聊开关在面板配置。详见 [游戏活动运维](docs/OPERATIONS.md#87-游戏活动日历)。

### Epic 喜加一

`.喜加一` 查询当前和即将免费的游戏；`.Epic 测试` 仅限主人。面板可配置地区、轮询间隔与群聊／私聊推送目标。首装建立基线，之后根据免费游戏集合变化推送；通常先发送卡片或文本，再发送一条链接汇总。详见 [Epic 运维](docs/OPERATIONS.md#89-epic-喜加一)。

### 灾害预警

V1 的验收范围为 Jian 气象／海啸和 Wolfx 地震。验收、观察和回滚步骤见 [灾害验收 runbook](docs/research/disaster-acceptance.md)；实际进度以 GitHub Issue #39 的最新记录和用户签字为准，不应将合成样例视为真实灾害事件验证。

## 开发与测试

```bash
# 在仓库根目录，虚拟环境已激活
pip install -r requirements.lock -r requirements-dev.txt
cd nonebot
python -m pytest tests -q
```

测试包含纯逻辑和 NoneBot 接线测试。真实 QQ、模型服务、外部数据源及渲染器的验收需分别执行；离线测试通过不能替代真实服务验证。

主要目录：

| 路径 | 内容 |
| --- | --- |
| `nonebot/bot.py`、`nonebot/plugins/` | 机器人入口和插件接线 |
| `nonebot/*_logic.py` | 功能逻辑 |
| `nonebot/dashboard.py`、`nonebot/dashboard.html` | 管理面板 |
| `nonebot/media_parser/` | 链接解析 |
| `nonebot/tests/` | 自动化测试 |
| `rag/` | 可选知识库检索服务 |
| `docker/playwright-renderer/` | 图片渲染服务 |
| `docs/`、`tools/` | 运维、研究文档和工具 |

## 数据与安全

### 敏感数据状态

当前提交树已清理仓库中的生产敏感数据：机器人 QQ／群号、私聊账号、内网与部署 IP、服务器地址、令牌、Cookie、API Key、面板密码、账号绑定、数据库、日志和本机备份均不进入版本控制。示例与测试使用合成值或占位符（如 `<bot-qq>`、`<部署机IP>`、`<内网网段>`、`10000001`）；不要把它们替换成真实值后提交。

本轮的清理对象是当前提交树，**不是 Git 历史**：旧提交可能仍包含已删除的本地备份、旧部署地址或测试中的真实账号。不要把当前工作树干净误认为远程历史也已干净；公开或推送前按下方「历史与轮换」处理。

`.gitignore` 已覆盖以下本地文件：

- `.env`、模型配置、人格模板和 `local/` 私有环境；
- `nonebot/data/` 下的面板配置、状态库、密钥、图片和头像缓存；
- `gsuid/core/`、`gsuid/data/` 下的上游源码、账号绑定和插件数据；
- SnowLuma 密码、协议端配置、二维码、Cookie、日志与备份。

### 推荐配置方式

从示例复制配置，实际文件只保存在部署机：

```bash
cd nonebot
cp .env.example .env
cp configs/models.yml.example configs/models.yml
cp configs/templates.yml.example configs/templates.yml
```

| 配置内容 | 模板 | 实际文件（Git 忽略） |
|---|---|---|
| 协议端连接、OneBot token、`SUPERUSERS`、机器人自身 QQ、显示名、gsuid 连接 | `nonebot/.env.example` | `nonebot/.env` |
| 模型地址、模型名、API Key、首选／备用模型 | `nonebot/configs/models.yml.example` | `nonebot/configs/models.yml` |
| 人格模板、称呼、主人别名 | `nonebot/configs/templates.yml.example` | `nonebot/configs/templates.yml` |
| 面板加载的当前人格正文 | — | `nonebot/templates/current.jinja2`（Git 忽略，`git pull` 保留；需随数据备份） |
| 本机私有覆盖值（优先级高于 `.env`） | — | `local/bot.env` |
| 管理面板保存的功能配置、密钥、状态库和上传图片 | — | `nonebot/data/` |
| 插件启用状态与首次引导完成标记 | — | `nonebot/data/plugins.json`（Git 忽略，普通 `git pull` 保留） |
| gsuid_core 的 `WS_TOKEN`、账号绑定和插件配置 | `gsuid/core/.env.example`（安装后生成） | `gsuid/core/data/` |
| SnowLuma WebUI 密码 | — | `tools/snowluma-webui-pass.txt` |

机器人名称（`bot-name`）优先使用管理面板「bot基本信息」写入的 `nonebot/data/bot-name.json`；未设置时才读取 `BOT_NAME`／`QQBOT_NAME`，默认「达妮娅」。它只影响卡片显示，不改变唤醒词或人格。

管理面板「人格模板」中还有一个 `current.jinja2` 原始正文编辑器：加载预设后可直接修改正文并点「保存当前人格」。保存前会校验内容非空、UTF-8、不含 NUL、不超过 128 KiB，且 Jinja2 语法有效；失败时返回错误行并保留原文件。保存只写 `nonebot/templates/current.jinja2`，不会修改 `configs/models.yml` 或切换当前模型／模板；正文在下一条回复生效，无需重启。若当前生效模板不是 `current`，面板会提示此时编辑不会影响正在使用的人格。`current.jinja2` 被 Git 忽略，升级、迁移或清理前必须单独备份。

### 更新已有部署

更新必须发生在 Git 检出里：在部署目录 `git pull --ff-only`，或 `git worktree add` 检出到新目录后再切换。**不要把下载的 zip/tar 解压覆盖到旧检出**——旧检出里已从仓库删除的文件不会消失，`git status`/`git diff` 也会失去可比对的基线，示例配置还可能盖掉真实配置。

```bash
cd <检出目录>
git pull --ff-only
# 被 Git 忽略的本地配置与数据必须原样保留，不要复制任何 .example 覆盖：
# nonebot/.env、configs/models.yml、configs/templates.yml、local/、nonebot/data/、rag/index-siliconflow*/
cd nonebot
python migrate_db.py upgrade
python dashboard.py --init     # 已有认证文件时提示并退出，不会重置密码
python -m pytest tests -q
```

重启机器人及面板使代码生效；配置文件的修改方式见上一节。升级前先备份 `nonebot/data/`、`nonebot/.env`、`nonebot/templates/current.jinja2`、`gsuid/core/data/`（如果启用游戏查询）；不要把备份复制回仓库。需要全新目录时用 `git clone` 或 `git worktree add`，再从旧目录只复制上表列出的被忽略文件与数据。

### 历史与轮换

清理工作区只能防止新提交继续泄露。如果真实凭据曾进入 Git 历史，必须先在对应平台轮换该凭据，再使用 `git filter-repo` 等工具清理历史后推送；只删除当前文件不能消除历史记录。详细步骤见 [运维手册 §10](docs/OPERATIONS.md#10-部署本地文件与同步排除)。

其他安全边界：

- 默认将服务限制在本机或可信网络；不要直接将无保护的面板、OneBot 和渲染接口暴露到公网。
- 使用非官方 QQ 协议端存在账号风险；协议端的安装和账号管理由部署者自行负责。
- 机器人 QQ、群号等私有标识放在被 Git 忽略的 `nonebot/.env` 或 `local/bot.env`，代码通过 `private_env.py` 读取；完整交接文档和旧备份放在 `local/private/`，不进入仓库。

## 许可与来源

仓库原创代码以 [MIT](LICENSE) 许可发布。第三方依赖保留各自许可；人格模板和语料的权利不因本仓库代码许可而改变。

- `nonebot/status_*.py`、`nonebot/plugins/statuscard.py` 及其测试移植自 `SX0YYYY/astrbot_plugin_statuscard`，按 AGPL-3.0 分发，见 [许可](docs/third-party/statuscard-LICENSE) 和 [来源说明](docs/third-party/statuscard-NOTICE.md)。
- `nonebot/disaster_*.py`、`nonebot/plugins/disaster_warning.py` 及相关测试移植或重写自 `Pancakes-Labs/astrbot_plugin_disaster_warning`，按 AGPL-3.0 分发，见 [许可](docs/third-party/disaster-warning-LICENSE) 和 [来源说明](docs/third-party/disaster-warning-NOTICE.md)。
- Epic 相关文件参考 `CJSen/astrbot_plugin_epic_free_games_notice` 的行为和接口事实独立重写；上游 README 声明 AGPL-3.0，但未附 LICENSE 文件。本项目按 AGPL-3.0 保守分发，见 [许可](docs/third-party/epicfree-LICENSE)、[来源说明](docs/third-party/epicfree-NOTICE.md) 和 [署名](docs/third-party/epicfree-ATTRIBUTION.md)。
- 可选的 `gsuid_core` 生态（核心服务、XutheringWavesUID、EndUID、RoverSign 与 NoneBot 适配器 `nonebot-plugin-genshinuid`）在部署时按固定提交／版本下载到 `gsuid/core`，不随本仓库分发，均按 GPL-3.0 授权。上游见 <https://github.com/Genshin-bots/gsuid_core>、<https://github.com/Loping151/XutheringWavesUID>、<https://github.com/Loping151/EndUID>、<https://github.com/Loping151/RoverSign>、<https://github.com/KimigaiiWuyi/GenshinUID/tree/v4-nonebot2>，详见 [来源与许可说明](docs/third-party/gsuid-NOTICE.md)。

分发包含上述文件的组合时，请同时考虑其相应许可要求。
