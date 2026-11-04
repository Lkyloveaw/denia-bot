# 运维手册

面向 Linux 部署。Windows 单机部署的历史记录已归档在最后「附录」里，仅作参考。

## 1. 服务清单

| 服务 | 端口 | 进程/容器 | 配置 |
|---|---|---|---|
| SnowLuma（QQ 协议端） | 5099 WebUI / 6081 noVNC | Docker `qqbot-snowluma` | compose 环境变量 + `/opt/qqbot/snowluma/config` |
| NoneBot2 + MuiceBot | 8080 | `python bot.py` | `nonebot/.env`、`nonebot/configs/models.yml` |
| RAG 检索服务 | 8091 | `python rag/serve.py` | 环境变量 `KB_GATE_MODE` 等；见 `rag/core.py` |
| 管理面板 | 8092 | `uvicorn dashboard:app` | `nonebot/data/dashboard-auth.json` |
| 模型网关 AxonHub（可选） | 8090 | 独立二进制 | `axonhub/config.yml` |

四个业务服务**只监听回环地址**（面板按需绑内网 IP），外面访问一律走 SSH 隧道或反代 + HTTPS。

## 2. 启动顺序

重启机器后：

1. `docker compose up -d`（SnowLuma，需要先有登录态；否则去 6081 扫码）
2. RAG：`.venv/bin/python rag/serve.py`
3. NoneBot：`cd nonebot && ../.venv/bin/python bot.py`
4. 面板：`systemctl start qqbot-dashboard`（见 `nonebot/qqbot-dashboard.service`）

SnowLuma 的反向 WS 必须指向 `ws://127.0.0.1:8080/onebot/v11/ws`，且 `accessToken` 与 `.env` 的
`ONEBOT_ACCESS_TOKEN` 一致；握手还需要 `X-Self-ID` 头（NoneBot 靠它认 Bot，缺了会 403）。

## 3. 账号隔离

SnowLuma 是**进程级注入**，同一 QQ 客户端里登录的所有号都会被 hook。

- 生产 compose 里 `SNOWLUMA_HOOK_AUTOLOAD: "0"`，只恢复之前成功接入过的账号，不再自动钩别的号
- 改 `hookAutoLoad` 只能在配置文件里改（WebUI 没有这个开关），改完重启
- SnowLuma **不支持按 UIN 过滤注入**，粒度只有 PID

## 4. 日志

| 内容 | 位置 |
|---|---|
| NoneBot 主日志（UTF-8 文件 sink） | `nonebot/logs/<日期>.log` |
| RAG | `rag/logs/` |
| 协议端 | `docker compose logs -f snowluma` |
| 面板 | `journalctl -u qqbot-dashboard -f` |

**优先看 loguru 自己的文件 sink**：重定向到控制台/文件的那份在非 UTF-8 环境下会因表情符号编码失败而丢行。

## 5. 修改人格

人格 = `nonebot/templates/<Name>.jinja2` + `nonebot/configs/templates.yml` 里的变量。

切人格只需三处：

- `configs/templates.yml` 的 `ai_nickname` / `master_nickname` / `userinfos`
- `configs/models.yml` 里每条模型的 `template: <Name>`
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

## 6.5 管理面板的 SnowLuma 状态显示

面板上「SnowLuma 注入」显示 **未运行** 时，先分清是真没跑还是探测不到：

| 面板显示 | 含义 | 处理 |
|---|---|---|
| 未运行 | 所有候选地址都连不上 | `docker ps` 看 `qqbot-snowluma` 是否在跑；`systemctl status qqbot-dashboard` 看面板是否正常 |
| 已注入 / 已注入并连接 | 正常 | 无需处理 |
| 状态未知 | WebUI 通了但拿不到注入状态 | 见下方两种原因 |

**原因一：端口绑在内网 IP 而不是回环地址。**
compose 里 `ports: "192.0.2.226:5099:5099"` 只监听该 IP，探 `127.0.0.1:5099` 会直接失败。
探测会依次尝试 `SNOWLUMA_URL`（如设置）→ `127.0.0.1:5099` → `host.docker.internal:5099` →
本机各内网 IP 的 `:5099`，命中后记住该地址。所以要固定用某个地址，就在面板的 service 里设：

```ini
Environment=SNOWLUMA_URL=http://192.0.2.226:5099
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
```
knowledge/*.md  ──build_index──▶  rag/index/{chunks,tokens,lexicon,vectors}  ──serve──▶  :8091
```

- 检索只在判定「相关」时注入 1~2 块，无关一条都不塞
- 索引里记了 `embed_backend`（`cpu`/`gpu`）和 `dim`，**CPU 与 GPU 版索引不能混用**；fp16 只在 CUDA 上更快
- 语料改了要重建（几小时，支持断点续跑）；改检索阈值前后都要跑 `rag/eval_gate.py` 做闸门回归
- 服务挂了或超时（2.5s），插件一律当「没命中」，不影响正常聊天

开发服务器（2026-11-01）：`qqbot-rag-cloud` 提供同一回环地址的 `/health`、
`/search` 和 `/embed`，使用硅基流动 `BAAI/bge-m3`；管理面板「知识库 Embedding」
可更新密钥，密钥保存于 `nonebot/data/embedding_secrets.json`（权限 600，不回显）。
云端索引位于 `rag/index-siliconflow-20261101/`，包括原有语料和角色档案；原索引
`rag/index/` 保留。原本地模型服务 `qqbot-rag` 已停用且取消开机启动，不要和云端
服务同时绑定 8091。`/reindex` 在云端服务上返回 409，必须离线构建并验证新索引，
不能直接用本地模型索引替换云端索引。记忆向量使用不同模型标识，旧向量按用户访问
逐步重新生成；切换提供商时不能混用不同模型产生的向量。

切换部署的原始文件备份在服务器 `/opt/qqbot/.deploy-embedding-siliconflow-20261101/backup/`。
回退需先停止机器人和云端检索服务，再恢复备份的机器人记忆文件及面板文件、启用
`qqbot-rag`，最后启动面板与机器人。回退前先检查这些文件是否有后续修改。

## 8. 管理面板

`nonebot/dashboard.py` 提供只读状态与少量管理接口：群话题、天气推送、主人设置、解析器配置。
面板要求密码（`data/dashboard-auth.json`），所有写操作需要二次确认。

## 8.5 服务器状态卡

`nonebot/plugins/statuscard.py` 负责采集与出图，配置在 `nonebot/data/statuscard.json`。

**命令**（仅超级管理员，名单在面板「超级管理员」里维护）：

| 命令 | 作用 |
|---|---|
| `查看状态` | 出一张状态卡图片（同会话默认 10 秒冷却） |
| `状态卡诊断` | 平台、在线状态、采样库、告警/监测/日报开关、最近推送结果 |
| `测试通知` | 往当前会话推一条，验证推送链路 |
| `立即检测` | 跑一遍所有监测项，只报异常与汇总 |
| `预览日报` / `预览周报` | 生成昨天 / 最近 7 天的报表 |

**数据文件**：

| 路径 | 内容 | 保留 |
|---|---|---|
| `data/statuscard.json` | 配置（面板可改关键项，改完最多一分钟热加载） | — |
| `data/statuscard-state.json` | 网络采样点、本月流量、告警规则进度、掉线时间、推送记录 | 自动 |
| `data/statuscard.sqlite3` | 资源/网站/进程采样、异常事件、每日快照、报表发送记录 | 资源 30 天 / 事件 90 天 |
| `data/statuscard/avatar-<QQ>.png` | QQ 头像缓存 | 24 小时 |

**依赖**：`psutil`（采集）、`Pillow`（素材预处理和缩放）与同机 Playwright 容器（排版截图）。
`requirements.lock` 已包含。

**注意**：状态卡经回环 HTTP 使用**同机 Playwright 容器**，保留 D 浅色泡泡外观，主机信息不发往外部服务。
这是与上游 AstrBot 插件最大的差别——上游默认把整页 HTML 发给远程 T2I 服务出图。

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

# 远程重启与日志（测试服务器 root@192.0.2.226:/opt/qqbot）
systemctl restart qqbot
systemctl is-active qqbot
journalctl -u qqbot -n 80 --no-pager | grep -i epic
```

测试服务器在 2026-10-31 已同步 Epic 相关文件，远程原文件备份为 `*.bak-epic-20261031`；`dashboard.py` / `dashboard.html` / `gate_logic.py` 用定点补丁保留远程已有改动。以后升级时不要整文件覆盖这三个接线文件，先比对再合并。

实跑记录见 `docs/research/epic-free-acceptance.md`：真实接口抓取、760 宽卡片、模拟换期推送、卡片失败回退文本、接口失败跳过/命令回文案、面板增删目标均逐项留痕。

## 9. 上线前检查

- [ ] 5099 / 6081 / 8080 全都只绑内网或 `127.0.0.1`，**不要对公网开**
- [ ] WebUI、VNC、面板、数据库密码全是强的且各不相同
- [ ] 数据卷 `qqbot-qq-config` / `qqbot-qq-data` **绝对不能删**，删了要重新扫码
- [ ] `sysctl kernel.yama.ptrace_scope` ≤ 2（= 3 会直接拒绝注入）
- [ ] 给容器加 `mem_limit` / `cpus`
- [ ] 备份：数据库、`gate.json`、`model_pref.json`、`rag/index`、`data/statuscard*`
- [ ] 状态卡默认已勾选「主机名」「进程信息」脱敏；公开群再加「网络数据」

## 10. 终末地（endfield）

- 远程渲染器是独立预置服务，本次合并不含其服务端；接口见 `docs/endfield.md`。
  已有开发服采用宿主机模式 `HOST=127.0.0.1 PORT=3100`（3000 是 personal-wiki）；
  机器人 `render_url=http://127.0.0.1:3100`，3100 不要对公网开。
- Key：主面板「终末地」（`/endfield` 重定向）写入 `data/endfield_secrets.json`（0600），`ENDFIELD_API_KEY` 兜底；
  绑定 token 在 `data/endfield/bindings.json`（0600，明文，备份注意）。
- 调度器随 bot 启动，60s tick；心跳 `data/endfield/heartbeat.json`（>180s 视为离线）；
  审计 `data/endfield/audit.jsonl`（1 MB 轮转），面板读尾部。
- 测试：`cd nonebot && .venv/Scripts/python.exe -m pytest tests/endfield -q`（默认离线）。
- 公告订阅仅 superusers 在目标群；个人订阅仅私聊；图卡失败自动回退文本。

## 11. 部署本地文件与同步排除

以下文件只存在于部署机，仓库里只保留 `*.example` 模板。**从仓库同步代码时必须排除它们**，否则真实配置会被占位值覆盖：

| 文件 | 仓库对应物 | 说明 |
|---|---|---|
| `nonebot/.env` | `nonebot/.env.example` | OneBot token、`SUPERUSERS` 等 |
| `nonebot/configs/models.yml` | `nonebot/configs/models.yml.example` | 模型地址与密钥 |
| `nonebot/configs/templates.yml` | `nonebot/configs/templates.yml.example` | 人格配置；`userinfos` 含主人 QQ |
| `nonebot/data/` | — | 运行时状态与凭据 |
| `/etc/systemd/system/qqbot-dashboard.service` 及 `.d/` | `nonebot/qqbot-dashboard.service` | 仓库 unit 默认绑 `127.0.0.1`，真实监听地址放 drop-in |
| `snowluma/config/`、`compose.yml` | `docker/snowluma/` 模板 | 协议端配置 |

同步建议：

```bash
rsync -a --exclude '.env' \
  --exclude 'configs/models.yml' --exclude 'configs/templates.yml' \
  --exclude 'data/' --exclude '.venv/' --exclude '__pycache__/' \
  <仓库>/nonebot/ /opt/qqbot/nonebot/
```

若用 `git pull`，先在部署机执行一次：

```bash
git update-index --skip-worktree nonebot/configs/templates.yml
```

面板监听地址用 drop-in 覆盖（不要把真实内网地址写进仓库）：

```ini
# /etc/systemd/system/qqbot-dashboard.service.d/host.conf
[Service]
Environment=DASHBOARD_HOST=<部署机内网地址>
```

改完执行 `systemctl daemon-reload && systemctl restart qqbot-dashboard`，再用 `systemctl show -p ExecStart` 核对实际监听地址。

## 附录：Windows 单机部署（历史）

早期在 `D:\Software\qqbot` 上用 SnowLuma + NoneBot 双进程跑的方案，入口是
`启动机器人.bat` / `停止机器人.bat`（内部调 `tools\启动所有.ps1`）。
关键差异：用 `tools\只注入小号.py` 做账号隔离（探针识别 PID 后只 load 小号），
且所有 `.ps1` 必须存成带 UTF-8 BOM，否则 PowerShell 5.1 按 GBK 解析中文会报「字符串缺少终止符」。

这些脚本仍留在 `tools/` 里，但仓库的默认部署路径是 Linux。
