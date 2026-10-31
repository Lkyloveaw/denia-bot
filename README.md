# denia-bot

一个跑在 Linux 上的 **QQ 人格陪聊机器人**：QQ 协议端经 OneBot v11 接 NoneBot2 + [MuiceBot](https://github.com/Moemu/MuiceBot)，
用 Jinja2 人格模板扮演《鸣潮》角色「达妮娅」，并在背后挂一层本地 RAG 知识库做设定检索。

> 人格设定、语料、密钥、账号等**私有数据不进仓库**。仓库里只有代码、测试、部署脚本和文档。

---

## 架构

```
QQ 小号（协议端账号）
    │  ① SnowLuma（Docker，ptrace 注入运行中的 QQ）→ OneBot v11
    ▼
SnowLuma  ──② 反向 WS ──▶  NoneBot2 :8080
                              └─ MuiceBot  LLM 聊天 + Jinja2 人格
                                   │  ③ OpenAI 兼容 HTTP
                                   ▼
                              模型网关（AxonHub :8090）→ 上游模型
                                   ▲
                                   │ ④ 命中才注入
                              RAG 检索服务 :8091（bge-m3 + BM25，自带语料）
```

一轮消息的实际顺序：

1. 协议端把消息经反向 WS 推给 NoneBot
2. MuiceBot 判定该不该回（@ 或昵称前缀），**`gate` 插件再拦一层**：群里没 @ 就直接结束，**一次模型都不调**
3. `kb` 插件把这句话（先剥掉唤醒词）拿去检索
4. 只有判定「相关」时才把 1~2 块设定拼进 system prompt，无关一条都不塞（避免资料稀释人格）
5. 请求发给模型网关 → 模型生成 → 原路发回 QQ
6. 回复真的发出去了，`gate` 的 `on_finish_chat` 钩子才计数 —— 数的是「回了几次」而非「收到几条」

---

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
另有 `bot_monitor.py` 做只读状态检查与串行重启。终末地面板在 `/endfield`。

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

```bash
git clone git@github.com:<you>/denia-bot.git && cd denia-bot

python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock

cd nonebot
cp .env.example .env                                   # 填 ONEBOT_ACCESS_TOKEN / SUPERUSERS
cp configs/models.yml.example configs/models.yml       # 填 api_key
python migrate_db.py upgrade
python bot.py
```

协议端按 `docker/snowluma/README.md` 起容器，扫码登录后把反向 WS 指向
`ws://127.0.0.1:8080/onebot/v11/ws` 并填同一个 `ONEBOT_ACCESS_TOKEN`。

知识库（可选）：把语料放进 `knowledge/*.md`，用 `rag/build_index.py` 建索引，再 `rag/serve.py` 起服务。

> 详细运维手册见 `docs/OPERATIONS.md`，踩坑记录见 `docs/PITFALLS.md`。

---

## 测试

```bash
cd nonebot && python -m pytest tests -q
```

纯逻辑用例不依赖运行中的机器人，接线用例通过打桩验证 hook/rule 真的挂上了。

---

## 安全须知

- **不要提交**：`.env`、`configs/models.yml`、任何 `*token*.txt`、`*pass*.txt`、数据库、日志、协议端配置
- 所有服务只监听回环；远程访问走 SSH 隧道或反代 + HTTPS
- 非官方协议端有封号风险：用小号，别频繁换 IP

---

## 许可

代码以 MIT 许可发布（见 `LICENSE`）。
自有代码之外的第三方部分保留各自许可：MuiceBot、SnowLuma、NapCat、AxonHub、media_parser 等。
人格模板与语料为本项目私有数据，不含在仓库许可授权范围内。
