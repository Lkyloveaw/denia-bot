# 运维手册

面向 Linux 部署。Windows 单机开发入口的说明在最后「附录」，生产部署仍以 Linux + systemd 为准。

## 1. 服务清单

| 服务 | 端口 | 进程/容器 | 配置 |
|---|---|---|---|
| SnowLuma（QQ 协议端） | 5099 WebUI / 6081 noVNC | Docker `qqbot-snowluma` | compose 环境变量 + `/opt/qqbot/snowluma/config` |
| NoneBot2 + MuiceBot | 8080 | `python bot.py` | `nonebot/.env`、`nonebot/configs/models.yml` |
| RAG 检索服务（可选） | 8091 | `rag/.venv/bin/python rag/serve_cloud.py --port 8091` | SiliconFlow `BAAI/bge-m3`；`KB_CLOUD_INDEX` 默认 `rag/index-siliconflow/`；Key 在 `nonebot/data/embedding_secrets.json` |
| 管理面板 | 8092 | `uvicorn dashboard:app` | `nonebot/data/dashboard-auth.json`（0600）；监听地址用 `DASHBOARD_HOST` / `DASHBOARD_PORT`（默认 `127.0.0.1:8092`） |
| 模型网关 AxonHub（可选） | 8090 | 独立二进制 | `axonhub/config.yml` |
| gsuid_core（可选） | 8765 | `cd gsuid/core && uv run core`，或 compose 服务 | `gsuid/core/data/config.json` |

所有业务服务**只监听回环地址**（面板按需绑内网 IP），外面访问一律走 SSH 隧道或反代 + HTTPS。

## 2. 启动顺序

默认入口（Docker SnowLuma + systemd NoneBot/面板/gsuid）：

```bash
sudo bash deploy-linux/install.sh   # 首次：venv、依赖、配置、迁移、面板密码、systemd 单元
sudo bash deploy-linux/start.sh     # 启动 SnowLuma → gsuid（可选）→ qqbot → 面板
```

`deploy-linux/README.md` 记录参数、首次扫码、配置和排障。SnowLuma 镜像默认 `motricseven7/snowluma:latest`；公开镜像不可用时，在 `docker/snowluma/.env.local` 设置 `SNOWLUMA_IMAGE`（私有仓库先 `docker login`）。`install.sh` 在 pull 失败但本地已有镜像时会继续，不会覆盖缓存。

以下手动顺序用于自定义部署或排查：

1. `docker compose --env-file docker/snowluma/.env.local -f compose.yml up -d`（SnowLuma，需要先有登录态；否则去 6081 扫码）
2. RAG（可选，需先按 §7 配置 SiliconFlow Key 并构建云端索引）：`rag/.venv/bin/python rag/serve_cloud.py --port 8091`
3. gsuid_core（启用游戏账号查询时）：先运行 `gsuid/install.sh` 完成安装；启动时 `cd gsuid/core && uv run core`（或 compose `docker compose up -d`）；**必须先于 NoneBot 启动**，否则适配器首连失败；core 启动后下一条消息会重试，必要时重启机器人
4. NoneBot：`cd nonebot && ../.venv/bin/python bot.py`
5. 面板：首次启动前先在 `nonebot/` 执行 `python dashboard.py --init`（生成认证文件与一次性密码，见 §8.0），再 `systemctl start qqbot-dashboard`（见 `nonebot/qqbot-dashboard.service`）。未初始化时面板可以启动，但登录必然返回 401。

NoneBot 启动时会打印面板地址，首次部署不用再猜端口：

```text
[dashboard] 管理面板: http://127.0.0.1:8092（独立进程，需单独启动）
```

日志里的地址按 `DASHBOARD_HOST` / `DASHBOARD_PORT` 生成，未设置时是默认回环地址 `127.0.0.1:8092`（见 §8.0）。Linux 上看这一行：`journalctl -u qqbot -n 50 | grep dashboard`。

Linux 常驻 gsuid_core：`sudo cp gsuid/qqbot-gsuid-core.service /etc/systemd/system/ && sudo systemctl enable --now qqbot-gsuid-core`；NoneBot 单元用 drop-in 增加 `After=qqbot-gsuid-core.service`。局域网 WebConsole 需要 core `HOST=0.0.0.0` 且只对本子网放行 TCP 8765；Windows 用 `tools\gsuid-局域网访问.ps1`，详见 `gsuid/README.md` §6.1。

SnowLuma 的反向 WS 必须指向 `ws://127.0.0.1:8080/onebot/v11/ws`，且 `accessToken` 与 `.env` 的
`ONEBOT_ACCESS_TOKEN` 一致；握手还需要 `X-Self-ID` 头（NoneBot 靠它认 Bot，缺了会 403）。

## 2.5 健康检查与重启握手

`GET http://127.0.0.1:8080/dashboard-health` 返回 `service`、`ready`、`connected`、`pid`、`start_token` 和新增的 `started_at`：

| 字段 | 含义 |
|---|---|
| `ready` | NoneBot 应用已启动；不代表协议端已连上 |
| `connected` | 配置的机器人账号（`QQBOT_SELF_ID`）是否已连接；未配置时看是否有任意 Bot |
| `pid` | 当前进程号；重启后排障用 |
| `start_token` | 每个进程都有，不会为空：面板重启时通过 `QQBOT_START_TOKEN` 传入新令牌并优先采用；普通启动没有该变量时，进程启动时生成一次。它是重启/进程身份握手令牌，Windows 面板重启用它确认连上新进程，Linux 重启靠 PID 变化；**不是启动时间，不能拿来算 uptime** |
| `started_at` | 进程启动时刻（Unix 时间戳，`time.time()`），监控运行时长用它，不要用 `start_token` 推算 |

判断标准：`ready=true` 且 `connected=true` 才算服务正常；`ready=true, connected=false` 检查协议端登录态与 `X-Self-ID`。重启卡住时同时看 `pid` 是否变化（Linux）或 `start_token` 是否等于面板这次下发的令牌（Windows），不要只看 HTTP 200。`start_token` 只用于判断「是不是这次重启后的新进程」，与运行了多久无关。

## 3. 账号隔离

SnowLuma 是**进程级注入**，同一 QQ 客户端里登录的所有号都会被 hook。

- 生产 compose 里 `SNOWLUMA_HOOK_AUTOLOAD: "0"`，只恢复之前成功接入过的账号，不再自动钩别的号
- 改 `hookAutoLoad` 只能在配置文件里改（WebUI 没有这个开关），改完重启
- SnowLuma **不支持按 UIN 过滤注入**，粒度只有 PID

## 4. 日志

| 内容 | 位置 |
|---|---|
| NoneBot 主日志（UTF-8 文件 sink） | `nonebot/logs/<日期>.log` |
| RAG | `logs/rag.out.log`、`logs/rag.err.log`（Windows 启动器与索引激活） |
| 协议端 | `docker logs -f qqbot-snowluma`（或 `docker compose --env-file docker/snowluma/.env.local -f compose.yml logs -f snowluma`） |
| 面板 | `journalctl -u qqbot-dashboard -f` |

**优先看 loguru 自己的文件 sink**：重定向到控制台/文件的那份在非 UTF-8 环境下会因表情符号编码失败而丢行。

## 5. 修改人格

人格 = `nonebot/templates/<Name>.jinja2` + `nonebot/configs/templates.yml` 里的变量。

管理面板「人格模板」使用一个可持久演进的当前正文 `nonebot/templates/current.jinja2`：

- 选择一个预设（`templates/` 下的 `*.jinja2`/`*.j2`，不含 `current`）后点「加载预设到 current.jinja2」：面板原子复制预设字节到 `current.jinja2`（权限 0600），再把 `configs/models.yml` 的 `template` 字段改成 `current`。预设文件本身不被修改。
- 加载预设后，面板「编辑当前人格正文」可直接修改 `current.jinja2` 并点「保存当前人格」。保存前校验内容非空、UTF-8、不含 NUL、不超过 128 KiB，并通过 Jinja2 语法解析；失败返回错误行且保留旧文件，写入使用原子替换和 0600。该操作只改正文，不修改 `models.yml`、不切换模型或模板。
- 若当前生效模板不是 `current`，面板会明确提示：此时保存正文不会影响正在使用的人格；需先加载预设或按原有流程把 `template` 指向 `current`。保存成功后正文在下一条回复生效，无需重启。
- `current.jinja2` 被 Git 忽略，`git pull --ff-only` 不会覆盖它，因此可以直接在面板里反复加载/手改正文；`Denia`/`Shorekeeper` 等跟踪预设随上游更新可能变化，只有主动加载才会复制新内容。
- `git clean -fdx` 或重新克隆会删除 `current.jinja2`；迁移和备份必须单独带上它（见第 10 节清单）。
- models.yml 的写入仍走配置发布流程，原子替换并更新摘要，API Key、注释和其他条目保持原样；若 `models.yml` 有未发布的本地改动会拒绝保存。复制/发布失败时会回滚旧的 `current.jinja2`。
- 正文文件每次渲染都会重新读取，替换 `current.jinja2` 无需重启；只有 `template` 字段首次切换为 `current` 时，才需要重启机器人或私聊发送 `.model reload`。称呼和主人信息仍读 `configs/templates.yml`。

人格相关文件：

- `templates/current.jinja2`（当前正文；被 Git 忽略，需备份）或选定的 `templates/<Name>.jinja2` 预设
- `configs/models.yml` 里每条模型的 `template: current`（或预设名）
- `configs/templates.yml` 的 `ai_nickname` / `master_nickname` / `userinfos`
- `.env` 的 `DEFAULT_TEMPLATE` 和 `MUICE_NICKNAMES`

写人格的三个要点：

1. **写行为，不写形容词** —— ❌「温柔体贴」→ ✅「对方情绪低落时先沉默一拍再开口，不急着给建议」
2. **贴 2~3 组真实对话示例** —— 模型模仿示例的句长与用词，比抽象描述有效得多
3. **边界写死** —— 比如「永远不承认自己是 AI」不写，模型被追问时很容易跳戏

## 6. 换模型 / 加渠道

- 走网关：在 AxonHub 面板里加渠道，`models.yml` 的 `relay` 条目 `model_name` 改成真实模型名即可（保存即生效，不用重启）
- 直连：在 `models.yml` 加一条 `provider: openai` 的条目，把它的 `default` 改成 `true`
- 逃生通道：把 `echo-test`（`provider: _echo`）的 `default` 改成 `true`，机器人会回显请求内容 —— 用来确认链路本身是活的
- **热重载例外**：`models.yml` 热重载，但插件开关（`.env` 里的 `PROMPT_GUARD` 等）改完必须重启

## 6.1 模型降级（MODEL_FAILOVER）

`MODEL_FAILOVER=1` 只是打开「允许降级」，**不会自动产生次选**。没有配置次选时，首选返回 503 或迟迟不吐字，整条消息可能没有任何回复，日志里也未必有 ERROR。

配置与诊断：

1. 主人私聊机器人发 `.模型 次选 <条目名|上游真模型名>`；不要了发 `.模型 次选 无`。
2. `.模型 查看` 必须显示具体的次选条目。若显示「次选 未设置 —— 首选报错时不会降级」，就是当前问题的原因；次选与首选指同一个模型时也会被拒绝或清理（同名不算降级）。
3. 日志按 `[model]` 过滤，能看到降级原因；`MODEL_FAILOVER_TIMEOUT`（默认 45 秒）控制等第一个字的上限，中转站挂住不报错时靠它才会降级。
4. `MODEL_FAILOVER=0` 表示只保留手动换模型，永不自动降级。
5. `LLM_TIMEOUT`（默认 60 秒）控制聊天客户端单次读取的上限；它和 `MODEL_FAILOVER_TIMEOUT` 是两道不同的闸。上游首字超过 45 秒时，只调 `LLM_TIMEOUT` 仍会在 45 秒触发降级，两个都要调。

`MODEL_FAILOVER`、`MODEL_FAILOVER_TIMEOUT` 与 `LLM_TIMEOUT` 都在 `nonebot/.env`，改完重启机器人。首选这一回合已经吐过字时不会重跑，避免同一轮说两遍。

## 6.5 管理面板的 SnowLuma 状态显示

面板上「SnowLuma 注入」显示 **未运行** 时，先分清是真没跑还是探测不到：

| 面板显示 | 含义 | 处理 |
|---|---|---|
| 未运行 | 所有候选地址都连不上 | `docker ps` 看 `qqbot-snowluma` 是否在跑；`systemctl status qqbot-dashboard` 看面板是否正常 |
| 已注入 / 已注入并连接 | 正常 | 无需处理 |
| 状态未知 | WebUI 通了但拿不到注入状态 | 见下方两种原因 |

**原因一：端口绑在内网 IP 而不是回环地址。**
compose 里 `ports: "<部署机IP>:5099:5099"` 只监听该 IP，探 `127.0.0.1:5099` 会直接失败。
探测会依次尝试 `SNOWLUMA_URL`（如设置）→ `127.0.0.1:5099` → `host.docker.internal:5099` →
本机各内网 IP 的 `:5099`，命中后记住该地址。所以要固定用某个地址，就在面板的 service 里设：

```ini
Environment=SNOWLUMA_URL=http://<部署机IP>:5099
```

**原因二：没有 WebUI 密码，读不到注入状态。**
面板读 `tools/snowluma-webui-pass.txt` 登录 SnowLuma WebUI。文件缺失时仍能判定
「在运行」，但看不到注入情况，提示里会直接写明。写入密码即可：

```bash
mkdir -p /opt/qqbot/tools
printf '%s' '你的SnowLuma访问令牌' > /opt/qqbot/tools/snowluma-webui-pass.txt
chmod 600 /opt/qqbot/tools/snowluma-webui-pass.txt
systemctl restart qqbot-dashboard
```

> `snowluma/config/webui.json` 里只有 scrypt 哈希，反推不出明文；令牌丢了重置密码。
> 拿不到也没关系，注入状态可以直接看容器日志：
> `docker logs qqbot-snowluma | grep "login detected"`

## 6.6 图片理解（看图）

前提两条，少一条都不生效：

1. `configs/models.yml` 当前条目的 `multimodal: true`（MuiceBot 只在这个开关打开时才把
   `Resource` 编进请求，`muice.py:254`/`:323`）；
2. 上游模型真的支持 `image_url`。直连中转站时先拿一张带文字的图打一次
   `/chat/completions` 确认，别只看模型名。

`plugins/image_guard.py` 负责把图片变成可用的请求：

- 只留图片（语音/视频/文件不进多模态请求），一条最多 `IMAGE_MAX_COUNT` 张；
- 下载后缀不可信（QQ 的 JPEG 常被存成 `.png`，因为 `uniseg.Image.name` 默认 `image.png`），
  统一转成 JPEG 并把后缀改对；
- **历史图片全部剥掉**：MuiceBot 会把 `Msg.resources` 落库，开多模态后 20 轮内的旧图
  会在后续每轮重新 base64 发一遍；
- 纯图片消息先补一个 `[图片]` 占位符，MuiceBot 的 `at_event`（命令 `.+`）才进得来；
  随后换成「（对方发来一张图片，没有配文字）」。占位符必须插在图片**前面**，
  「图片在前、文字在后」时 Alconna 实测仍会匹配失败；
- 回复结束、存库前删缓存图并清空 `resources`。

开关在 `.env`（改完重启）：

```ini
IMAGE_VISION=1        # 0 = 不看图，本轮媒体进模型前丢掉（回滚用）
IMAGE_MAX_SIDE=1280   # 最长边像素；越大越清楚、token 越多
IMAGE_MAX_COUNT=4     # 一条消息最多带几张图
```

日志看 `[image]`：

| 日志 | 含义 |
|---|---|
| `本轮 N 张图进模型，历史媒体剥离 M 个` | 正常，图已经送进模型 |
| 只有 `本轮 N 张图（压缩…）`，没有下一行 | 多半是 `multimodal: false`，图被 MuiceBot 丢了 |
| `IMAGE_VISION=0，本轮媒体已丢弃` | 开关关了，按预期不看图 |

不覆盖的情况：QQ 表情包如果是 `mface` 段而不是 `image` 段，进不来；引用消息里的图
被 `quote_fix` 渲染成文字 `[图片]`，也不会作为图送给模型。

## 7. 知识库（RAG）

RAG/嵌入只保留一条路径：**SiliconFlow `BAAI/bge-m3`**（1024 维，`/embed` 标识
`siliconflow-bge-m3-v1`）。本地 ONNX 入口已移到 `rag/legacy-local/`，仅作历史参考，
不受支持；不要再把 `rag/index/` 或本地模型产生的向量接回 8091。

```
knowledge/*.md  ──rebuild_index.py──▶  rag/index-siliconflow/{chunks,tokens,lexicon,vectors}
                                             │
                                             ▼
                              serve_cloud.py :8091  ──/health /search /embed──▶  NoneBot
```

`knowledge/*.md`（`达妮娅.md`、`梗知识.md`、`鸣潮库街区百科.md`）由 Git 跟踪；
`rag/index-siliconflow/` 是可重建产物，已被 `.gitignore` 忽略，新检出没有索引是正常的。
索引目录用 `KB_CLOUD_INDEX` 覆盖（默认 `rag/index-siliconflow/`）。索引的 `meta.json`
必须带 `embed_backend=siliconflow`、`embed_model=BAAI/bge-m3`，否则 `serve_cloud.py`
启动直接报模型不匹配。

前置条件：

- SiliconFlow API Key（免费 `BAAI/bge-m3`），管理面板「知识库 Embedding」保存到
  `nonebot/data/embedding_secrets.json`（0600，不回显）；也可以用 `KB_EMBED_KEY_FILE`
  指定其他路径。
- 一个已经构建好的云端索引（见下文）。只有 Key、没有索引时服务不会启动。

RAG 运行依赖**不在** `requirements.lock` 里，单独建环境：

```bash
cd rag
python3.12 -m venv .venv
. .venv/bin/activate
pip install jieba numpy fastapi uvicorn
python rebuild_index.py --knowledge ../knowledge --output index-siliconflow
python serve_cloud.py --port 8091
```

`rebuild_index.py` 会调用 SiliconFlow 批量编码并校准词条闸门。语料全量重建耗时与费用
取决于规模，不要当日常刷新；`--limit N` 可做小规模冒烟，`--dry-run` 只列出语料。
服务的环境变量：

| 变量 | 默认 | 作用 |
|---|---|---|
| `KB_CLOUD_INDEX` | `rag/index-siliconflow` | 服务实际加载的索引目录 |
| `KB_RAG_PORT` | `8091` | `serve_cloud.py` 监听端口（也可 `--port`） |
| `KB_RAG_HOST` | `127.0.0.1` | 监听地址，保持回环 |
| `KB_EMBED_TIMEOUT` | `20` | 单次 SiliconFlow 请求超时（秒） |
| `KB_EMBED_RETRIES` | `2` | 网络错误/429/5xx 的有限重试次数（0~5） |
| `KB_RAG_TIMEOUT` | `8` | NoneBot 侧 `/health`、`/embed` 请求超时 |

`/health` 返回 `ready`、`chunks`、`embed_backend=siliconflow`、`model=BAAI/bge-m3` 和
实际加载目录 `index`。管理面板显示「生效索引」时优先读 `/health`；服务不可达时退回
`nonebot/data/rag-rebuild.json` 的 `active_index` 或显式 `KB_CLOUD_INDEX`/systemd
`Environment`，不再按目录名猜最新索引。

### Windows 索引激活

`rebuild_index.py --activate` 在 Windows（以及没有 systemd 的 POSIX）会：

1. 找到监听 8091 的进程并停止（`taskkill /T /F`），确认端口释放；
2. 把旧目录改名为 `<active>.previous-<时间戳>`，再把新索引原子改名到 `active`；
3. 以 `rag/serve_cloud.py --port 8091` 拉起独立进程，工作目录 `rag/`，日志
   `logs/rag.out.log` / `logs/rag.err.log`，环境里写死 `KB_CLOUD_INDEX=<active>`；
4. 轮询 `/health`，要求 `ready`、`chunks` 等于新索引块数、`index` 等于 active 目录；
5. 任一步失败：停掉新进程、把失败目录改名为 `.failed-<时间戳>`、恢复旧目录并重新
   拉起旧服务；回滚也失败时错误信息会同时列出原因。

实现位于 `rag/service_control.py`（仅标准库），测试会 mock 其中的停/启函数。
Linux 且系统里有 `systemctl` 时仍走原来的 `systemctl stop/start qqbot-rag-cloud`，
行为保持不变。

### 向量一致性护栏

长期记忆（`nonebot/plugins/memory.py`）和表情学习（`nonebot/meme_learning.py`）在每次
写向量前都通过 `nonebot/rag_client.py` 检查 `/health`：

- `ready != true`、`embed_backend != siliconflow` 或 `model != BAAI/bge-m3`：直接抛
  `RagConsistencyError`，不写库，也不回退到本地向量；错误会进日志/任务失败原因。
- `/embed` 返回的模型标识或维度不对：同样拒绝写入。

一次性迁移（旧版本可能把本地 ONNX 向量按 `siliconflow-bge-m3-v1` 存过；先停机器人并
备份数据库）：

```bash
sqlite3 nonebot/data/memories.sqlite3 'DELETE FROM memory_vectors;'
sqlite3 nonebot/data/memes/library.sqlite3 'DELETE FROM meme_details;'
```

记忆向量会在用户下次访问时按需重建；表情包的 `meme_details` 含描述和向量，删掉后需要
重新学习/重新描述（已上传图片仍保留，手工图片仍可随机选择）。不要只改 `model` 标签，
那正是混库的来源。

其他运行约束：

- 检索只在判定「相关」时注入 1~2 块，无关一条都不塞；服务挂了或超时（2.5s）插件当
  「没命中」，不影响正常聊天。
- 改检索阈值前后跑 `rag/eval_gate.py`（它只调 8091 的 HTTP 服务）做闸门回归。
- `/reindex` 在云端服务上返回 409；必须离线构建、验证后按上面的激活流程切换。
- 面板「知识库 Embedding」的「重建 RAG 索引」会先展示本次读取的 `knowledge/` 下全部
  `.md`/`.txt` 和可选的 `rag/imports/character-profiles-*/chunks.jsonl`，二次确认后
  后台调用 `rag/rebuild_index.py`；新索引写到临时目录，验证成功后激活并健康检查，
  失败自动回滚。

## 8. 管理面板

`nonebot/dashboard.py` 提供运行状态与配置管理接口：群话题、天气推送、主人设置、解析器配置、B站动态接收目标。
面板要求密码（`data/dashboard-auth.json`），所有写操作需要二次确认。

- 「服务器状态」顶部显示主机名、系统、CPU 型号与逐核占用、内存/SWAP、可执行文件、进程 PID 和依赖版本；下方保留历史趋势、磁盘、进程、异常与状态卡配置。
- 「运行日志」可按来源、级别和关键词筛选；支持暂停自动刷新、复制、下载和实时轮询，并在同一页提供默认模型提供商的 WebUI 对话测试。
- 「表情包」可从固定目录 `nonebot/data/stickers` 一键导入，按文件夹建分类并自动写入角色、场景、风格、情绪标签和分组权重；重复图片按 SHA-256 跳过。

## 8.0 首次启动与登录

首次部署先在 `nonebot/` 下执行：

```bash
python dashboard.py --init
```

该命令只创建认证文件，不加载 NoneBot、不启动 uvicorn。未指定 `--password` 时随机生成密码，只在终端显示一次，并写入 `data/dashboard-initial-password`。

| 文件/行为 | 说明 |
|---|---|
| `data/dashboard-auth.json` | 0600；只存 scrypt 盐和哈希，以及 `must_change` 标记 |
| `data/dashboard-initial-password` | 0600 的明文一次性密码；首次改密成功后自动删除 |
| `data/dashboard-onboarding.json` | 首次改密后写入的引导状态；完成或跳过后保留 `basics_pending:false` |
| 未初始化就登录 | `POST /api/login` 返回 401「密码错误或后台尚未配置」 |
| 未登录访问管理接口 | 401「请登录」 |
| 首次登录后 | 改密前其他接口返回 403「请先修改初始密码」 |
| 已有认证文件时 `--init` | 拒绝覆盖并以非零退出；重置需先备份并移走该文件，或在面板内改密 |
| 密码规则 | 12–256 位；新密码不能与原密码相同 |
| 暴力尝试 | 同一 IP 5 分钟内失败 8 次后返回 429 |
| 会话 | Cookie `admin_session`，8 小时；面板重启后仍然有效，改密后全部失效 |

认证文件、一次性密码文件和引导状态文件权限必须是 0600；Windows 上至少保证 `nonebot/data/` 不对其他用户共享。不要删除认证文件来「重置」面板——那会让面板回到未初始化状态，所有登录继续 401。

首次改密成功后，面板会在下次登录时自动跳到「Bot 基本设置」并显示分步引导（第 1/5 步至第 5/5 步）：Bot 名称、人格模板、模型设置、主人识别、超级管理员，用「上一步 / 下一步」切换。步骤进度和完成状态写入同目录的 `data/dashboard-onboarding.json`，完成或跳过后不再显示；引导未完成时，插件首次引导会延后到它之后。

面板默认地址是 `http://127.0.0.1:8092`。机器人与面板共用同一组进程环境变量 `DASHBOARD_HOST` / `DASHBOARD_PORT`（写在 `nonebot/.env` 里不会生效，systemd 用 `Environment=`，Windows 用系统环境变量或启动前 `set`）：

- 两个变量都没设置时，`bot.py` 启动日志和 Windows 启动脚本都显示默认回环地址 `http://127.0.0.1:8092`。
- 面板绑内网 IP 时（§10 的 systemd drop-in），把同样的 `Environment=` 也加到托管 `bot.py` 的单元（或启动它的 shell 环境）里，启动日志才会照实打印那个地址；只给面板单元设置时，机器人日志仍显示默认回环地址，面板本身按 drop-in 监听。
- 面板是独立进程：`bot.py` 只在启动日志里打印地址，不会拉起面板；Windows 一键启停脚本会启动和停止面板，Linux systemd / 直接运行 `bot.py` 时仍需单独启动面板。

## 8.1 机器人显示名称

面板「bot基本信息」设置状态卡、日报 / 周报、Proxmox 集群卡、灾害卡和 `.help` 指令菜单统一显示的机器人名称（`bot-name`）。面板写入 `data/bot-name.json`，机器人每次出图时读取，因此保存后下一张卡立即生效，不需要重启。

解析顺序：`data/bot-name.json` → 进程环境或 `local/bot.env` / `nonebot/.env` 的 `BOT_NAME` / `QQBOT_NAME` → 默认 `达妮娅`。名称仅影响显示，不改人格模板、唤醒词、账号或模型设置。面板可恢复默认；文件损坏时自动回退到环境变量或默认名称。

## 8.2 二维码登录与 Cookie 路径

管理面板「链接解析」顶部是「通用设置」和按 schema 顺序排列的 18 个平台标签，点击标签只显示该平台的设置。小红书与 B 站的二维码登录都在面板进程内完成，不依赖额外服务或容器。

### 小红书（面板内置 Python Playwright）

链路：

1. 面板 `POST /api/parser/xhs-login/start` → `xhs_login.start()` → 在当前面板进程启动 headless Chromium。
2. Playwright 打开小红书官网二维码页并截图；面板轮询 `/api/parser/xhs-login/status`，取消走 `/cancel`。同一时间只允许一个待扫码会话。
3. 用户点击「保存解析设置」→ `POST /api/parser` 携带 `xhs_login_id` → `xhs_login.claim()` / `settle()` → `parser_settings.save()`。
4. Cookie 写入 `nonebot/data/media_parser/config.json` 的 `xhs.cookies`；重启机器人后生效。面板 API 和网页都不返回 Cookie 明文，浏览器在保存、取消、过期或错误后关闭。

依赖与浏览器：

```bash
# 面板 venv 已按 requirements.lock 安装 playwright 包
./.venv/bin/python -m playwright install chromium

# Linux root 首次缺少系统库时
./.venv/bin/python -m playwright install --with-deps chromium
```

`deploy-linux/install.sh` 和 `tools/startup-preflight.ps1` 会安装/补齐 Python 包与 Chromium。没有额外端口、令牌文件、systemd unit 或 compose；启动面板即可使用。重启面板会中断未保存的扫码会话，重新点击「生成二维码」即可。

Linux root 默认关闭 Chromium 沙箱（root 下沙箱通常无法启动）；非 root 和 Windows 默认开启。可用 `QQBOT_XHS_LOGIN_SANDBOX=0/1` 覆盖。浏览器仍以面板进程权限运行，建议面板使用非 root 账户。页面请求只允许小红书 HTTPS 域名，Cookie 只在面板进程内保存，保存时才通过 `claim/settle` 交给解析设置。

官网风控、页面结构变化、网络异常或缺少 Playwright/Chromium 时，面板会显示明确错误；此时仍可手动填写 Cookie。二维码登录可能使同一账号的其他网页端下线。

### B 站（宿主机 Python，无需容器）

链路：

1. 面板 `POST /api/parser/bilibili/qr` → `parser_credentials.create_bilibili_qr()` → `BilibiliLogin.login_with_qrcode()`。
2. 后者用 Python `qrcode` 生成二维码，并用 `curl_cffi` 直接请求 `passport.bilibili.com` 的二维码接口；不经过浏览器或容器。
3. 面板轮询 `/api/parser/bilibili/qr/status` → `poll_bilibili_qr()` → `BilibiliLogin.poll_qr_state()`。
4. 扫码成功后，`BilibiliLogin.save_cookies()` 原子写入 `nonebot/data/media_parser/cookies/bilibili_credential.json`；`save_bilibili_config()` 同时写入 `nonebot/data/media_parser/config.json` 的 `bilibili.cookies`。机器人按文件 mtime 自动重载登录态，不需要重启。

面板进程会自动把 `nonebot/parser_deps` 加入 `sys.path`，与机器人共用同一份解析依赖（清单见 `nonebot/media_parser/requirements.txt`）；缺少依赖时面板返回 503 并提示缺哪个包，手动 Cookie 仍可保存。

入口在「链接解析 → B站 → 账号凭证」；手动 Cookie/JSON 导入与扫码共用同一保存路径。此路径在 Windows 和 Linux 都只依赖宿主 Python 环境，不依赖 Docker。

## 8.5 服务器状态卡

`nonebot/plugins/statuscard.py` 负责采集与出图，配置在 `nonebot/data/statuscard.json`。

**命令**（仅超级管理员，名单在面板「超级管理员」里维护）：

| 命令 | 作用 |
|---|---|
| `查看状态` | 出一张状态卡图片（同会话默认 10 秒冷却） |
| `查看集群状态` | 出一张 Proxmox VE 集群卡（节点 / 访客 / 存储；独立 10 秒冷却） |
| `状态卡诊断` | 平台、在线状态、采样库、告警/监测/日报开关、最近推送结果 |
| `测试通知` | 往当前会话推一条，验证推送链路 |
| `立即检测` | 跑一遍所有监测项，只报异常与汇总 |
| `预览日报` / `预览周报` | 生成昨天 / 最近 7 天的报表 |

**数据文件**：

| 路径 | 内容 | 保留 |
|---|---|---|
| `data/statuscard.json` | 配置（面板可改关键项；命令即时读取，后台任务最多一分钟热加载） | — |
| `data/statuscard-state.json` | 网络采样点、本月流量、告警规则进度、掉线时间、推送记录 | 自动 |
| `data/statuscard.sqlite3` | 资源/网站/进程采样、异常事件、每日快照、报表发送记录 | 资源 30 天 / 事件 90 天 |
| `data/statuscard/avatar-<QQ>.png` | QQ 头像缓存 | 24 小时 |

**Grafana CPU 温度**（可选）：

- 面板「服务器状态 → Grafana CPU 温度」填写地址、Prometheus 数据源 UID、PromQL、平均窗口和 API Key。
- API Key 单独保存在 `data/status_grafana_secrets.json`（0600，不回显）；`statuscard.json` 只保留非密钥字段。
- 查询走 `GET /api/datasources/proxy/uid/<uid>/api/v1/query_range`；当前值取窗口最后一个样本，平均值取窗口内全部样本均值。数据源需为 Prometheus。
- 查询失败只省略温度，不影响状态卡其他内容；面板「测试连接」可直接验证。
- Grafana 服务账号需要 `Data sources: Query` 权限；默认查询 `max(node_hwmon_temp_celsius)`，按实际指标调整。

**Proxmox VE 集群状态卡**（可选）：

- 面板「服务器状态 → Proxmox VE 集群」启用，填写卡片标题、InfluxDB Bucket（默认 `proxmox`）、可选的 Datasource UID 覆盖、数据窗口（默认 10 分钟）、节点筛选和 CPU 温度开关。
- 复用上面的 Grafana 地址、Datasource UID 与 API Key；`cluster.datasource_uid` 留空时使用 `grafana.datasource_uid`。
- 查询走 Grafana `POST /api/ds/query` 的 InfluxDB Flux 接口，固定读取 Telegraf Proxmox 输入的 `cpustat`、`memory`、`system` 和 `hwmon` measurement。
- 卡片包含节点 CPU/内存/SWAP/负载/运行时长/CPU 温度、VM/LXC 访客表和存储使用率；每类数据缺失时只留空对应区块，不伪造 0。
- Grafana 只持续采样运行中的访客；已停止且没有采样的访客不会出现在卡上。命令失败时回退为文字摘要，不影响 `查看状态`。
- 面板「测试集群查询」可直接验证当前填写的连接与 Bucket，不需要先保存。

**依赖**：`psutil`（采集）、`Pillow`（素材预处理和缩放）与同机 Playwright 容器（排版截图）。
`requirements.lock` 已包含。

**注意**：状态卡经回环 HTTP 使用**同机 Playwright 容器**，保留 D 浅色泡泡外观，主机信息不发往外部服务。
这是与上游 AstrBot 插件最大的差别——上游默认把整页 HTML 发给远程 T2I 服务出图。

**渲染**：状态卡、集群卡与日报 / 周报都由 `nonebot/status_image.py` 排版，顶部是达妮娅横幅：状态卡固定用 `resources/statuscard/denia-bubbles.jpg`，集群卡与报表从 `resources/statuscard/banners/` 随机取一张（候选集为空时回落 `denia-bubbles.jpg`），图从顶部渐入、向下渐隐，报表只在第一页出现。素材缺失或损坏时只丢背景（损坏的单张会被跳过），卡片版式与数据不变；集群卡与报表的横幅区固定高 340px。候选图来源、规格与新增方式见 `resources/statuscard/banners/README.md`。

## 8.6 超级管理员

以前改权限要编辑 `nonebot/.env` 的 `SUPERUSERS` 再重启；现在在面板「超级管理员」
里增删即可，**最多 30 秒对机器人生效**，不用重启。

| 项 | 说明 |
|---|---|
| 名单文件 | `nonebot/data/superusers.json`（权限 600，不进仓库） |
| 初始值 | `.env` 的 `SUPERUSERS`，只在名单文件**不存在**时使用 |
| 优先级 | 名单文件存在就只认文件——面板里删掉的人不会被 `.env` 再加回来 |
| 生效方式 | 机器人启动时加载；运行中每 30 秒按文件 mtime 热应用 |
| 回退 | 面板「回退到 .env」会删除名单文件，重新使用 `.env` 的值 |
| 面板登录 | 与名单**无关**：QQ 超管不持有面板密码也进不来；面板密码丢了也仍可用 SSH 改文件 |

能做什么：`限流 …`、换模型、`查看状态 / 状态卡诊断`、`开启解析 / 关闭解析 / blogin`、
`群话题 …`（私聊）。普通群员与群主/群管理员都**没有**这些权限。

验证：私聊发 `状态卡诊断`，返回里有一行「超级管理员：N 人（面板名单 / .env 初始值）」；
非管理员发同样的指令只会收到「这个指令仅主人可用」。

## 8.7 游戏活动日历

面板侧栏「游戏活动」统一管理本插件，配置保存后无需重启。面板仅修改 SQLite 或排队任务，网络抓取与 OneBot 消息只在机器人进程执行。初始没有订阅，紧急提醒默认关闭。

**可用会话**与解析器采用相同规则：

```text
group:123456789
private:987654321
```

白名单与黑名单均按一行一个会话填写；黑名单优先，白名单非空时只允许名单内会话，空白名单表示不限。群聊、私聊总开关优先于名单。被禁止的会话不回复活动命令，也不产生 LLM 回复。名单只暂停订阅，不删除它；恢复允许后按当前档位处理。创建自群聊的紧急目标会记录来源群，来源群被名单禁止时，派生的私人提醒也暂停。

「全局推送」和单订阅「启用」只控制主动推送，允许范围内仍可查询。SRA/AKEData/PRTS 的源开关同时暂停该源抓取和推送，查询可查看带时间标记的旧缓存。编辑时遇到版本冲突，请重新载入后再保存。订阅机器人账号与目标地址创建后不可更改；迁移目标须删除旧订阅再新建，避免继承旧目标的投递历史。

| 命令 | 用法与权限 |
|---|---|
| `活动游戏`、`活动查询 [游戏…]`、`活动列表` | 普通用户可读；不指定游戏时使用当前会话订阅 |
| `活动订阅 鸣潮 原神`、`活动退订 鸣潮`、`活动退订 全部` | 群主/管理员/主人管理本群，私聊管理自己 |
| `活动过滤 包含 版本 联动`、`活动过滤 排除 签到` | 替换对应关键词列表；包含至少命中一个，排除命中即隐藏；`查看`或`清空` |
| `活动阈值 7 3 1` | 最多10档，1–365天；`默认`继承，`关闭`停用倒计时 |
| `活动封面 图片/链接/关闭/默认`、`活动开关 开/关` | 当前会话配置；暂停不清通知历史 |
| `活动状态`、`活动刷新 数据 [游戏…]` | 仅主人；刷新只更新数据，默认也是数据模式 |
| `活动刷新 推送 [游戏…]` | 仅主人；按现有订阅规则立即检查，不绕过名单、阈值或去重 |
| `活动私人提醒 自己 [游戏…]` | 保存自己的独立游戏范围；省略时复制当前会话订阅和过滤，后续不会自动扩大 |
| `活动私人提醒 添加 @某人 鸣潮`、`活动私人提醒 移除 @某人` | 仅主人可指定其他人；添加必须明确游戏 |
| `活动私人提醒 列表/测试/停止`、`我已知晓 [活动名或编号]` | 列表私聊返回本人；测试仅本人、每60秒一次；停止/确认在被禁止会话仍静默清理本人状态 |

所有命令均以半角 `.` 开头，例如 `.活动`、`.已知晓`。查询按用户与会话各限10秒，刷新任务至少间隔60秒。所有英文及辅助别名列在 `.help`。

首次订阅等待后台初始化，默认发送当前概览并建立去重基线；概览每游戏最多30项，剩余数量在末段提示并同时建基线。发送失败保留未成功段；成功回执后才记状态。平台超时属于回执未知，不立即降级重发；下轮可重试，因此回执丢失或发送后写库前崩溃仍可能重复。

普通推送默认每30分钟检查、7/3/1天三级提醒；只使用最近6小时核验过的完整快照，失效源自动暂停推送。查询最多使用7天缓存并标注核验时间。紧急私聊默认关闭；开启后默认UTC+8 23–8静默，每人120分钟一次、每天最多3条，按目标汇总，最多8项。群推送开关不修改已建立的独立紧急目标，但访问名单仍约束它。

面板「预览」只读缓存；「刷新数据」仅排队抓取；「立即检查并推送」可能向现有允许目标发送，使用前查看配置。任务进度、错误与最近投递可在同页查看。机器人离线时任务保留排队，不由面板进程代发。每个数据目录仅允许一个活动调度实例，第二实例会记录错误并停止本插件服务。

数据位置：`nonebot/data/game_events.sqlite3` 保存设置、订阅、初始化进度、确认/额度、快照、任务和日志；`nonebot/data/game_events/` 保存源/封面缓存与调度锁。结束活动与日志保留30天，封面最长7天、总计200MiB。备份应使用 SQLite 在线 backup，或停机器人及面板后备份数据库及 WAL/SHM；不要在写入期间只复制主文件。数据库版本超前或损坏时停用此插件并排查，不清空数据库重新群发。

封面默认使用系统 DNS。若网络返回 `198.18.0.0/15` Fake-IP，设置 `GAME_EVENT_COVER_DNS=google` 并重启机器人，可仅对活动封面使用 Google DoH 获取真实 IPv4；不改系统 DNS，仍校验所有地址为公网、固定连接 IP 并验证原域名证书。解析失败或原站重定向异常时回退文字和封面链接。开发服务器已使用此模式。

2026-10-31 已在开发服务器完成面板、10 个游戏源和指定私聊 `10000001` 的三种消息模式验收，临时订阅已清理；PRTS 原站封面重定向异常时仍回退文字。详见 `docs/research/game-events-development-deployment.md`。正式使用先配置明确的会话限制和订阅，再逐步扩展。回退时先关闭全局推送，再停用插件，保留数据库；不影响其它插件。

## 8.8 统一图片渲染容器

2026-11-01 起采用同机 qqbot-playwright 容器，入口 127.0.0.1:3100。九类卡片共用 nonebot/render_client.py；面板「图片渲染服务」管理唯一有效地址。配置在 nonebot/data/renderer.json，默认不需要文件；进程环境 QQBOT_RENDER_URL 优先。

Playwright / Chromium 版本固定 1.60.0 对应版本，字体包含原 HYS 字体与 Noto 中文/Emoji。分页、并发、就绪、构建和回退说明见 [渲染容器说明](../docker/playwright-renderer/README.md)。旧 qqbot-playwright-renderer.service 只保留回退用途，正式运行应为 inactive。

检查：curl --fail http://127.0.0.1:3100/health，应有 ready:true、deployment:container；docker logs --tail 40 qqbot-playwright 查看错误分类。HTTP 422 表示内容超出分页边界，413 为完整请求过大，429 为容量已满。排查时不要打印原始 HTML 或隐私数据。

本仓库历史部署包含未跟踪功能，部署必须按审核过的文件清单并备份；不能用旧 Git checkout 覆盖运行目录。回退要同时恢复业务文件和旧渲染服务。

## 8.9 Epic 喜加一

面板「Epic 喜加一」管理地区、轮询间隔、推送开关与目标（群/私聊，最多 50 个），并可触发一次「立即推送」。配置写 `nonebot/data/epic_free.json`，机器人每轮重读，保存后**不用重启**；「立即推送」写触发文件 `nonebot/data/epic_free_trigger.json`，机器人最多 30 秒内消费。

| 项 | 说明 |
|---|---|
| 查询命令 | `.喜加一`；仅主人、群主、群管理员；普通群成员与非超管私聊静默拒绝 |
| 预览命令 | `.Epic 测试`（仅主人）；抓取真实接口后把卡片/文本 + 链接发到当前会话 |
| 推送去重 | 键 = 每款免费游戏的 `id` + 该条 0% offer 的 `startDate`；首装只记录、重启不补发、纯下架不推、失败/空数据不更新状态 |
| 投递格式 | 每次固定两条：卡片（失败回退文本）+ 一条全部领取链接；`即将免费` 受面板开关控制，最多 3 条 |
| 状态文件 | `nonebot/data/epic_free_state.json`；按目标保存 `last_key` / `last_sent_at` / `last_games`，可删除后重新建立基线（删除后下一轮只记录不推送） |
| 地区/语言 | `country` 默认 `CN`，`locale` 默认 `zh-CN`；非法地区会得到 HTTP 200 错误体，本轮按失败跳过 |
| 轮询 | `poll_minutes` 默认 30，范围 5–180；改小只会更快发现换期，不会因此多推 |

部署与验证：

```bash
# 本地定向测试（应全绿；全量见仓库测试说明）
cd nonebot && python -m pytest tests -q -k epic_free

# 远程重启与日志（测试服务器 root@<部署机IP>:/opt/qqbot）
systemctl restart qqbot
systemctl is-active qqbot
journalctl -u qqbot -n 80 --no-pager | grep -i epic
```

测试服务器在 2026-10-31 已同步 Epic 相关文件，远程原文件备份为 `*.bak-epic-20261031`；`dashboard.py` / `dashboard.html` / `gate_logic.py` 用定点补丁保留远程已有改动。以后升级时不要整文件覆盖这三个接线文件，先比对再合并。

实跑记录见 `docs/research/epic-free-acceptance.md`：真实接口抓取、760 宽卡片、模拟换期推送、卡片失败回退文本、接口失败跳过/命令回文案、面板增删目标均逐项留痕。

## 8.9.1 插件管理与首次引导

首次登录面板会先显示分步 Bot 基本设置引导，完成或跳过后再显示一次插件引导，可选择要启用的可选插件。之后在侧栏「插件管理」中修改；开关保存在 `nonebot/data/plugins.json`（0600，Git 忽略），普通 `git pull` 不会覆盖。

- 插件引导的「完成并重启bot」会先保存开关再重启机器人；「稍后设置」只保存，之后可在「插件管理」中修改并手动重启。
- 核心插件（消息入口、模型接线、提示词/引用/图片保护、QQ 超级管理员、主人识别等）在面板中锁定，不能关闭。
- 关闭插件不会删除插件代码、数据或配置；机器人下次启动时不再加载。
- `gsuid` 开关还会尝试处理对应的外部服务：Linux 优先停止并禁用 `qqbot-gsuid-core.service`（或 Docker 容器 `gsuid_core`），Windows 停止 `logs/gsuid-core.pid`/命令行匹配的 gsuid_core 进程；重新启用时再启动。服务操作失败会在面板显示原因，不会静默忽略。
- Windows 下次运行 `启动所有` 时会读取 `nonebot/data/plugins.json`，关闭 gsuid 后不再拉起 gsuid_core。
- 修改后需重启机器人；数据库和私有配置不会被清理。

## 8.10 B站动态订阅

面板「B站动态订阅」展示每个群聊 / 私聊订阅了哪些 UP 主、订阅时间、最近检查时间与投递计数，并可增删「接收目标」；接收目标写入 `.env` 的 `BILI_TARGETS`，重启机器人生效。增删订阅仍然只在会话里做。数据来自机器人持有的 `nonebot/data/bilibili_dynamic/state.sqlite3`，面板以只读 URI（`mode=ro`）打开同一文件，不写库、不触发抓取或投递；机器人正在写入时最多等 2 秒，超时提示稍后刷新。

| 项 | 说明 |
|---|---|
| 命令 | `.b站订阅 <UID>` / `.b站退订 <UID>`（仅主人）、`.b站订阅列表`、`.b站搜索 <关键词>`、`.b站选 <序号>` |
| 接收目标 | `.env` 的 `BILI_TARGETS`（`bot:group:id` / `bot:private:id` 的 JSON 数组）；只有目标里的会话能新建订阅并收到投递，目标外的历史订阅休眠（不检查也不投递，面板会标出）。面板可增删并原子写回 `.env`，保存后重启机器人；若 `BILI_TARGETS` 来自进程环境则只读。`.b站搜索` / `.b站选` / `.b站订阅列表` 只是会话内即时回复、不产生投递，任何会话都能用 |
| 面板读取的配置 | `BILI_ENABLED`、`BILI_INTERVAL`、`BILI_TARGETS`、`BILI_STATE_FILE`；先看进程环境，再看 `nonebot/.env` |
| 状态库 | `state.sqlite3`（`user_version=31`）+ `state.sqlite3.lock`（机器人独占）；随机器人数据一起备份 |
| 来源暂停 | 命中 B站 403/412/429 时机器人写入 `source_stop`，面板显示「来源已暂停」；确认后把 `BILI_RESUME_SOURCE=true` 重启一次即可恢复 |
| 投递口径 | 首次订阅只记录基线、不补发历史；`待发` 含 `pending`/`retry`；`送达未知` 不自动重发 |
| 卡片头像 | 取动态自带的 UP 头像（只允许 `https://*.hdslb.com`，≤2 MiB，5 秒超时）；下载或解码失败时卡片不带头像，仍然正常推送 |
| 视频动态封面 | 视频动态额外取 `archive.cover`，在标题下方绘制视频封面（只允许 `.hdslb.com`，≤6 MiB，5 秒超时）；失败时退回纯文字卡片，不影响投递|

面板与插件版本必须一致：状态库 `user_version` 不是 31 时面板直接报错，不猜字段。部署后检查：

```bash
cd nonebot && python -m pytest tests -q -k bilibili_panel
systemctl restart qqbot-dashboard && systemctl is-active qqbot-dashboard
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8092/api/bilibili   # 未登录应为 401
```

2026-11-17 已在测试服务器完成部署，并修正侧栏与内容顺序不一致（「表情包」卡片在实际内容里排在「运行状态」前面）；面板、只读读取真实状态库与侧栏顺序均已验证。

## 8.11 B站解析凭据

面板「链接解析 → B站解析」维护 B 站解析所需凭据，不必再手工编辑数据目录：

- **粘贴导入**：支持 `SESSDATA=...; bili_jct=...; DedeUserID=...`、JSON 对象、JSON 数组，以及带 `{"cookies": [...]}` 包装的 JSON。`SESSDATA`、`bili_jct`、`DedeUserID` 必填，`DedeUserID` 必须是数字；格式错误返回 400，原凭据保持不变。
- **扫码登录**：点击「获取二维码」，用 B 站 App 扫码；二维码 2 分钟有效，过期或出错后可点「刷新二维码」重新获取。面板每 2 秒轮询一次状态，登录成功后显示「B 站 Cookie 已配置」。
- **保存位置**：凭据写入 `nonebot/data/media_parser/cookies/bilibili_credential.json`（POSIX 0600，原子替换），同时更新 `nonebot/data/media_parser/config.json` 中 B 站 `cookies` 字段；面板不回显明文，日志不记录 Cookie。
- **免重启**：解析器的 `BilibiliLogin` 在每次取 Cookie 前检查凭据文件 mtime，面板导入或扫码成功后，正在运行的机器人下一次解析请求即使用新凭据，无需重启。
- **失败保护**：粘贴内容或二维码轮询失败不会覆盖已有凭据；只更新 B 站 `cookies` 字段，不覆盖其他解析器设置。

相关接口（均要求面板登录 + CSRF）：`POST /api/parser/bilibili/cookies`、`POST /api/parser/bilibili/qr`、`POST /api/parser/bilibili/qr/status`。定向测试：

```bash
cd nonebot && python -m pytest tests/test_parser_credentials.py tests/test_parser_credentials_api.py -q
```

## 9. 上线前检查

- [ ] 5099 / 6081 / 8080 全都只绑内网或 `127.0.0.1`，**不要对公网开**
- [ ] WebUI、VNC、面板、数据库密码全是强的且各不相同
- [ ] 数据卷 `qqbot-qq-config` / `qqbot-qq-data` **绝对不能删**，删了要重新扫码
- [ ] `sysctl kernel.yama.ptrace_scope` ≤ 2（= 3 会直接拒绝注入）
- [ ] 给容器加 `mem_limit` / `cpus`
- [ ] 备份：数据库、`gate.json`、`model_pref.json`、`nonebot/templates/current.jinja2`、`rag/index-siliconflow*`、`data/statuscard*`、`data/theme-presets.json`、`data/appearance-backgrounds/`、`gsuid/core/data`（启用 gsuid_core 时含账号绑定与 `config.json`）
- [ ] 面板已执行 `python dashboard.py --init` 且首次密码已修改；`data/dashboard-auth.json` 权限为 0600，一次性密码文件 `data/dashboard-initial-password` 已删除
- [ ] 启用 RAG 时：`rag/.venv` 已装 `jieba numpy fastapi uvicorn`，SiliconFlow Key 已保存，云端索引已构建；`/health` 返回 `embed_backend=siliconflow`、`model=BAAI/bge-m3`；本地 ONNX 索引/向量没有混入
- [ ] 状态卡默认已勾选「主机名」「进程信息」脱敏；公开群再加「网络数据」

## 10. 部署本地文件与同步排除

以下文件只存在于部署机，仓库里只保留 `*.example` 模板。**更新或迁移检出时必须原样保留它们**，否则真实配置会被占位值覆盖：

| 文件 | 仓库对应物 | 说明 |
|---|---|---|
| `nonebot/.env` | `nonebot/.env.example` | OneBot token、`SUPERUSERS`、`QQBOT_SELF_ID` 等 |
| `nonebot/configs/models.yml` | `nonebot/configs/models.yml.example` | 模型地址与密钥 |
| `nonebot/configs/templates.yml` | `nonebot/configs/templates.yml.example` | 人格配置；`userinfos` 含主人 QQ |
| `nonebot/templates/current.jinja2` | — | 面板加载的当前人格正文；被 Git 忽略，`git pull` 保留，迁移必须单独复制 |
| `nonebot/data/` | — | 运行时状态与凭据 |
| `rag/index-siliconflow*/` | — | 可重建的云端索引；不随 Git 更新，迁移时复制可省去数小时重建 |
| `local/bot.env` | — | 被 Git 忽略的本机私有值；`QQBOT_SELF_ID` 优先从这里读取 |
| `local/private/` | — | 私有交接文档、旧备份和调试快照；不进入版本控制 |
| `/etc/systemd/system/qqbot-dashboard.service` 及 `.d/` | `nonebot/qqbot-dashboard.service` | 仓库 unit 默认绑 `127.0.0.1`，真实监听地址放 drop-in |
| `snowluma/config/`、`snowluma/data/`、`compose.yml` | `docker/snowluma/` 模板（已跟踪） | 协议端配置与登录态；`compose.yml` 由 `deploy-linux/install.sh` 从模板生成 |
| `docker/snowluma/.env.local` | `docker/snowluma/.env.example` | VNC 密码与 SnowLuma 绑定地址，0600，已忽略 |

敏感值持久化规则：

- 真实 token、API Key、Cookie、密码只放在上述被忽略文件的对应位置；仓库只保留带占位符的 `*.example`。
- 机器人自身 QQ 不在代码/工具里硬编码。`bot.py`、`bot_monitor.py` 和 `tools/只注入小号.py` 通过 `nonebot/private_env.py` 读取 `QQBOT_SELF_ID`，查找顺序为进程环境 → `local/bot.env` → `nonebot/.env`。
- 私有交接文档和旧备份放在 `local/private/`；根目录只保留不含账号信息的占位说明。
- 这些文件随部署机数据一起备份；迁移时复制对应目录并保持 `600`/目录权限，不要复制进 Git。
- 如果敏感值曾经进入 Git 历史，先轮换凭据，再用 `git filter-repo` 等工具清理历史；只改当前文件不能消除历史泄露。

### 更新方式（Git 检出）

部署目录必须是 `git clone` 出来的检出，或 `git worktree add` 出来的工作树。更新只在这棵树上做：

```bash
git -C /opt/qqbot fetch --all --prune
git -C /opt/qqbot pull --ff-only
sudo systemctl stop qqbot qqbot-dashboard
sudo bash /opt/qqbot/deploy-linux/install.sh --no-pull
sudo systemctl start qqbot qqbot-dashboard
```

先停 qqbot/面板再跑安装脚本，避免迁移和 pip 安装与正在使用的进程并发；SnowLuma 容器可以保持运行。`install.sh` 会按 `requirements.lock` 的摘要判断是否需要更新 `.venv`，并重装 systemd 单元；不覆盖已有的 `.env`、模型/人格配置和 SnowLuma 数据。手动排查时也可以继续用 `cd /opt/qqbot/nonebot && ../.venv/bin/python migrate_db.py upgrade`。

**禁止把下载的 zip/tar 解压覆盖到旧检出。** 覆盖不会删除旧检出里已从仓库移除的文件，`git status` / `git diff` 失去可比对基线，示例配置和旧数据还可能盖掉真实文件。需要全新目录时：

```bash
git clone <仓库地址> /opt/qqbot-new
# 或：git -C /opt/qqbot worktree add /opt/qqbot-new <分支>
```

然后从旧目录只复制上表列出的被忽略配置与数据，再切换 systemd 的 `WorkingDirectory`。不要用 `git clean -fdx`：它会删掉 `nonebot/data/`、`local/`、`rag/index-siliconflow*/` 等未跟踪内容。

`nonebot/configs/models.yml` 与 `templates.yml` 本来就被 Git 忽略、未被跟踪，`git pull` 不会碰它们；不要为了「保险」再从 `.example` 复制一遍。

面板监听地址用 drop-in 覆盖（不要把真实内网地址写进仓库）：

```ini
# /etc/systemd/system/qqbot-dashboard.service.d/host.conf
[Service]
Environment=DASHBOARD_HOST=<部署机内网地址>
```

改完执行 `systemctl daemon-reload && systemctl restart qqbot-dashboard`，再用 `systemctl show -p ExecStart` 核对实际监听地址。想让 `bot.py` 的启动日志也打印这个地址，把同一行 `Environment=DASHBOARD_HOST=<部署机内网地址>` 加到机器人单元（`qqbot.service`）的 drop-in 里；不设置时日志显示默认 `127.0.0.1:8092`，不影响面板实际监听。

## 附录：Windows 单机（本机开发）

Windows 上使用 `tools\启动所有.bat` / `tools\停止所有.bat` 启停；根目录的
`启动机器人.bat` / `停止机器人.bat` 只是调用它们的薄入口（旧版 `.ps1` 仅保留作兼容）：

- 脚本按自身所在目录解析检出路径（`%~dp0`），检出放哪个盘都行；不要改回写死的绝对路径。
- 启动前先运行 `tools\startup-preflight.ps1`：检查 `nonebot\.venv`、SnowLuma 运行时和 `requirements.lock`，必要时创建虚拟环境并安装/补齐依赖。检查失败时不会启动任何服务；安装耗时期间也不会并行拉起其他服务。`-CheckOnly` 只做文件检查、不运行 pip；`-SkipInstall` 只读检查、缺依赖时报告而不安装或修复（8080 上已有机器人时启动脚本会自动使用它）。
- 依赖安装使用当前生效的 pip index（`PIP_INDEX_URL` / `pip config`）。预检在安装前打印实际 index 和 HTTP(S)_PROXY / NO_PROXY，安装失败时会再次打印并给出镜像/代理提示；`-IndexProbeOnly` 只打印后退出。国内网络可先执行 `$env:PIP_INDEX_URL = 'https://pypi.tuna.tsinghua.edu.cn/simple'`，或运行 `tools\startup-preflight.ps1 -IndexUrl <url>`（`tools\启动所有.ps1` 也接受 `-IndexUrl`）。这是进程环境变量，不写进 `nonebot\.env`；输出中的 URL 凭据会脱敏。
- 有效的 `nonebot\.venv` 无需下载 Python。若 `nonebot\.venv\Scripts\python.exe` 缺失，脚本可用有效的 `runtime\python312\python.exe` 离线创建虚拟环境，不复用系统或 uv 的 Python。若缓存解释器也缺失，正常启动会从 GitHub 下载固定版本的 python-build-standalone 3.12.13（约 21 MB），校验 SHA256 后解压，再安装到缓存目录。`runtime\python312` 已被 `.gitignore` 忽略；preflight 的 `-CheckOnly` 和 `-SkipInstall` 都不会下载。
- 若 `runtime\python312` 已存在但缺少 `python.exe`，脚本会在下载解压后因目标目录已存在而停止；直接重试会重复下载。若缓存解释器存在但无法运行或不是 Python 3.12，则在下载前停止。两种情况都应先删除 `runtime\python312` 再正常启动；若虚拟环境也损坏，或要强制重新下载，请同时删除 `runtime\python312` 和 `nonebot\.venv`。
- 启动前检查 8080：已有机器人在跑时不会重复拉起第二个 NoneBot 实例，而是提示先停止。
- 启动前清理 `NO_PROXY` 中的 IPv6 回环条目（`::1` / `[::1]`），其他条目原样保留；机器人进程启动时也会做同样清理，避免本机回环请求被系统代理拦住。
- 启动横幅和结尾的 connection info 都会打印管理面板地址，默认 `http://127.0.0.1:8092`；设置了 `DASHBOARD_HOST` / `DASHBOARD_PORT` 就照实显示。面板是独立进程，脚本只显示地址、不启动它，按 README 的命令在 `nonebot` 目录单独启动。
- 含中文的 `.ps1` 必须存成带 UTF-8 BOM，否则 PowerShell 5.1 按 GBK 解析中文会报「字符串缺少终止符」；`tools\startup-preflight.ps1` 保持 ASCII-only，避免依赖 BOM。
- 直接运行 `tools\启动所有.bat` 或 `tools\启动所有.ps1` 失败时会暂停并等待 Enter；根目录 `启动机器人.bat` 无论成功失败都会暂停。需要无人值守时请直接用 `tools` 入口并设置 `QQBOT_NO_PAUSE=1`（PowerShell 也可传 `-NoPause`），不要走根目录入口。
- 账号隔离仍用 `tools\只注入小号.py`（探针识别 PID 后只 load 小号）。

这些入口只用于本机开发与调试；生产部署以 Linux + systemd 为准（见 §2、§10），不要把 Windows 上的路径、代理变量或服务脚本搬进服务器。
