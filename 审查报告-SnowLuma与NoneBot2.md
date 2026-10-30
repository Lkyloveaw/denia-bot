# 生产环境审查：SnowLuma + NoneBot2 vs NapCat + AstrBot

审查日期：2026-09-25
审查目标：能否把当前的 `NapCat + AstrBot` 换成 `SnowLuma + NoneBot2`，并长期跑在 Linux 生产环境

---

## 零、结论速览

| 问题 | 结论 |
|---|---|
| SnowLuma 能替 NapCat 吗？ | **能**。两者都输出 OneBot v11，接口层完全兼容 |
| NoneBot2 能替 AstrBot 吗？ | **能，但工作量大幅增加**。NoneBot2 是框架不是应用，人格/记忆/大模型都要自己拼 |
| 能跑 Linux 生产吗？ | **能，但 SnowLuma 是安全降级**：需要 `SYS_PTRACE` + `seccomp=unconfined` |
| 我的建议 | 见第六章。**两个决定应该拆开做** |

---

## 一、架构位置

```
       ┌─────────────────────────────┐
       │   协议端（可互换）            │   ← 决定 A
       │   NapCat   ／   SnowLuma     │
       └──────────────┬──────────────┘
                      │  OneBot v11
                      ▼
       ┌─────────────────────────────┐
       │   框架层（可互换）            │   ← 决定 B
       │   AstrBot  ／  NoneBot2      │
       └──────────────┬──────────────┘
                      ▼
                  LLM API
```

**关键点：这两层互相独立。你可以任意组合，4 种都能跑。**

---

## 二、决定 A：协议端 NapCat vs SnowLuma

### 2.1 工作原理（根本差异）

| | NapCat | SnowLuma |
|---|---|---|
| 原理 | NapCat 自身作为宿主进程，把 QQNT 当库加载（`wrapper.node`） | native addon 通过 **ptrace 注入 hook 到正在运行的 QQ 进程** |
| Windows | NapCat.Shell 自带独立 QQ 内核，**不碰你系统的 QQ** | 注入你**正在运行的 QQ.exe**，必须用同一 Windows 用户 |
| Linux | NapCat-Docker（headless） | 官方镜像内置 **Linux QQ + Xvfb + VNC + noVNC + supervisord** |

来源：SnowLuma 官方文档「介绍」原文——
> 一个 native addon 通过 ptrace 级注入把 hook 打进正在运行的 QQ 进程（Linux 进程名 `qq`，Windows `QQ.exe`），解析它的内部数据包，再以 OneBot v11 的形式……暴露出来。

### 2.2 Linux 生产环境对比 ⚠️ 核心

| 维度 | NapCat-Docker | SnowLuma Docker | 评价 |
|---|---|---|---|
| **所需特权** | **无** | **`cap_add: SYS_PTRACE`**<br>**`security_opt: seccomp=unconfined`** | **SnowLuma 明显更危险** |
| 额外常驻服务 | 无 | Xvfb + VNC + noVNC + supervisord | 攻击面 × N |
| 必须暴露的端口 | 6099 (WebUI) | **6081 (noVNC 网页远程桌面)**<br>5099 (WebUI)<br>3000 (HTTP) / 3001 (WS) | 每多一个都是风险 |
| shm_size | 无要求 | **1gb** | 内存开销 |
| ulimits nofile | 无要求 | 65536:1048576 | |
| 内存占用（实测经验） | ~300–500 MB | ~1–2 GB+ | 差 2–4 倍 |
| 登录方式 | headless，终端二维码 / 快速登录 | **必须走 noVNC 网页远程桌面扫码** | SnowLuma 更笨重 |
| 内核约束 | 无 | **`ptrace_scope=3` 的内核直接拒绝** | 加固过的生产内核可能要改配置 |
| QQ 版本 | 自带内核，与系统 QQ 无关 | **hook 按 QQ 版本对齐**，升级后可能要重扫码 | SnowLuma 更脆 |
| 项目成熟度 | 生态最大、长期稳定 | 较新，v1.14.x 一个月多个版本 | NapCat 更稳 |

**为什么 SnowLuma 需要 `SYS_PTRACE` + `seccomp=unconfined`：**

- `SYS_PTRACE` —— 允许进程跟踪并读写任意同用户进程的内存。**这显著扩大了容器逃逸面**。
- `seccomp=unconfined` —— 关掉 Docker 默认的 seccomp 过滤器。默认过滤器拦掉约 44 个危险系统调用，关掉等于把这层防护拆除。

> 这是官方的**必需**配置，不是可选项。SnowLuma 排障文档原文：
> 「注入失败 / QQ 起来了但不上线 —— 启动参数必须有 `SYS_PTRACE` 和 `seccomp=unconfined`。」

**在单租户自有 VPS 上，这个代价通常可接受。在共享主机 / 多租户 / 有合规要求的环境里，这是明确的安全降级。**

### 2.3 SnowLuma 的优点（客观）

- 架构更现代：TypeScript 运行时、类型完备的 `@snowluma/sdk`、`@snowluma/mcp`（能让 MCP AI 客户端直接调 QQ）
- 配置结构更清晰（`networks` 对象分组，字段归一化）
- 多账号：一个 UIN 一份 `onebot_<uin>.json`，自动检测接入
- 有官方 Docker 框架仓库，发版与镜像构建解耦
- 更新非常活跃（最新 v1.14.19，2026-09-21）

---

## 三、决定 B：框架 AstrBot vs NoneBot2

### 3.1 定位差异（最重要的一条）

| | AstrBot | NoneBot2 |
|---|---|---|
| 定位 | **应用**（拿来就用） | **框架**（拿来组装） |
| 类比 | 装好的整机 | 主板 + 零件 |

NoneBot2 本身**不提供任何 LLM / 人格 / 记忆能力**。它只解决「收到 QQ 消息 → 分发给插件 → 调用 OneBot API 回消息」。AI 聊天能力全部来自插件。

### 3.2 能力对比

| 维度 | AstrBot | NoneBot2 |
|---|---|---|
| 版本 | v4.28.1（2026-09-14） | v2.5.0（2026-04-01） |
| OneBot v11 适配 | 内置 | `nonebot-adapter-onebot` v2.4.6 |
| **人格设定** | ✅ 内置，WebUI 配置 | ❌ 需插件（MuiceBot 用 Jinja2 模板） |
| **上下文 / 记忆** | ✅ 内置，自动压缩 | ⚠️ MuiceBot 的长期记忆仍在 TODO |
| WebUI 管理台 | ✅ | ❌ 无 |
| 大模型接入 | ✅ WebUI 点选 | 配置文件 / 插件 |
| 多模态 / 知识库 | ✅ 内置 | 看插件 |
| 插件生态 | 1000+（官方市场，偏 AI 应用型） | **更大更成熟**（偏指令 / 工具型） |
| 生产稳定性 | 成熟 | **非常成熟**，生态最大 |
| 灵活性 / 可控性 | 中 | **高** |

### 3.3 NoneBot2 上做「游戏人格陪聊」的三条路

| 方案 | 说明 | 代价 |
|---|---|---|
| **A. MuiceBot** | 基于 NoneBot2 的完整 LLM 聊天应用。内置人设、多模型加载器（OpenAI/Ollama/Azure/Dashscope/Gemini）、多模态、工具调用、MCP、Jinja2 动态人设提示词、SQLite 存对话 | 最接近 AstrBot 的替代品；但**长期记忆还没做** |
| **B. 现成插件** | `nonebot-plugin-marshoai`、`nonebot-plugin-moellmchats`、`nonebot-plugin-wtfllm` 等 | 质量参差、维护状况需逐个核实 |
| **C. 自写插件** | 用 NoneBot2 写一个专用的人格聊天插件 | **工作量最大，但完全可控**——人格提示词、上下文裁剪、记忆策略全部自己定 |

### 3.4 换 NoneBot2 的真实收益

**得到的：**
- 完全掌控整条链路，没有黑盒
- 生态最大，任何需求基本都有轮子
- 你想加"指令型"功能（签到、查询、管理）时，NoneBot2 比 AstrBot 顺手得多

**失去的：**
- AstrBot 里点几下就有的（人格、记忆、多模态、知识库、WebUI），要逐个重新实现
- WebUI 管理台
- 出问题时要自己 debug Python 栈，而不是看网页报错

---

## 四、Linux 生产环境专项审查

### 4.1 两种目标架构的合规性

| 检查项 | NapCat + AstrBot | SnowLuma + NoneBot2 |
|---|---|---|
| 容器特权 | ✅ 无需 | ❌ **需 SYS_PTRACE** |
| seccomp | ✅ 默认启用 | ❌ **需 unconfined** |
| 暴露的远程桌面 | ✅ 无 | ❌ **noVNC 6081** |
| 最小权限原则 | ✅ 符合 | ❌ 违背 |
| 内存占用 | ✅ 低 | ⚠️ 高 2–4 倍 |
| 升级中断风险 | ✅ 低 | ⚠️ 可能需重扫码 |
| 自定义能力 | ⚠️ 中 | ✅ 高 |
| 社区支持 | ✅ 大 | ⚠️ 较小 |

### 4.2 运维注意（两套都要做）

1. **绝不裸奔**：`6185/6099/5099/6081` 不要直接暴露公网。用 Nginx/Caddy 反代 + HTTPS，
   或端口映射绑定 `127.0.0.1` 只走 SSH 隧道
2. **改默认密码**：所有 WebUI / 远程桌面密码
3. **备份数据卷**：AstrBot 是 `data/`，NapCat 是 `ntqq/`，SnowLuma 是三个 named volume
4. **日志与监控**：配 `restart: unless-stopped` + 日志轮转 + 掉线告警
5. **QQ 账号风控**：无论哪套都是非官方方案，都有封号风险。用小号、别频繁切 IP
6. **资源限制**：给容器加 `mem_limit` / `cpus`，避免 QQ 客户端吃满

### 4.3 SnowLuma 特有的生产陷阱

- 宿主机内核 `ptrace_scope=3` 会直接拒绝注入。很多加固过的生产镜像默认就是 3，
  要改成 ≤ 2（`sysctl kernel.yama.ptrace_scope=2`）——**这本身又是一次安全折损**
- 容器重建可能更换设备指纹 → **要重新扫码**。所以三个数据卷绝不能删
- 国内拉 `docker.1ms.run` 等镜像源需要额外配置

---

## 五、成本汇总

| | 当前（NapCat + AstrBot） | 目标（SnowLuma + NoneBot2） |
|---|---|---|
| 本机部署时间 | 已完成 | 需重来一遍（约 1–2 小时） |
| 人格陪聊可用性 | ✅ 已经能用 | ⚠️ 需组装（MuiceBot 或自写插件） |
| Linux 安全等级 | 高 | 中（关键降级） |
| 资源开销 | 低 | 高 |
| 可控性 | 中 | 高 |
| 长期维护成本 | 低 | 中高 |

---

## 六、建议

### 建议 1：两个决定拆开，不要捆绑

**协议端（决定 A）**：如果你只是想要一个能稳定跑的 QQ 机器人，
**保留 NapCat**。SnowLuma 的收益（SDK/MCP/新架构）对"游戏人格陪聊"这个目标没有实际提成，
但代价（SYS_PTRACE、noVNC、内存翻倍）在生产环境实打实。

**框架（决定 B）**：如果你确实想要完全可控、想自己写插件、或者以后要做指令型功能，
**换 NoneBot2 是合理的**。这是你唯一真正需要的替换。

### 建议 2：并行部署验证，别拆掉能用的

当前 `NapCat + AstrBot` 已经在跑且可用。端口不冲突：

| 服务 | 端口 |
|---|---|
| NapCat（现有） | 6099, 6199 |
| AstrBot（现有） | 6185 |
| SnowLuma | 3000, 3001, 5099, 6081 |
| NoneBot2 | 自定义，例如 8080 |

D 盘还有 296 GB。**先并行装一套试用，跑顺了再决定是否拆掉旧的。**
这是唯一能真正回答"值不值得换"的办法。

### 建议 3：如果最终要上 Linux 生产

| 优先级 | 组合 |
|---|---|
| 🥇 稳定优先 | **NapCat-Docker + AstrBot**（无特权、低资源、生态大） |
| 🥈 可控优先 | **NapCat-Docker + NoneBot2**（兼顾安全与灵活） |
| 🥉 新架构优先 | SnowLuma + NoneBot2（接受安全降级与更高维护成本） |

> 注意 🥈 这一档：**协议端用 NapCat、框架用 NoneBot2**，
> 既满足你换 NoneBot 的诉求，又避开了 SnowLuma 的生产代价。
> 如果不特别在意 SnowLuma 的 SDK/MCP 能力，这可能是最优解。

---

## 七、参考来源

- SnowLuma 官方文档：https://snowluma.github.io/zh/
  - 介绍：`/zh/docs/guide/introduction`
  - Docker 部署：`/zh/docs/guide/deploy/docker`
  - 排障：`/zh/docs/guide/faq`
  - 对接机器人：`/zh/docs/guide/connect-bot`
  - 配置参考：`/zh/docs/guide/configuration`
- SnowLuma 官方 compose：`SnowLuma/SnowLuma.Docker.Framework/docker-compose.yml`
- NapCat 仓库：https://github.com/NapNeko/NapCatQQ
- NapCat-Docker：https://github.com/NapNeko/NapCat-Docker
- NoneBot2：https://github.com/nonebot/nonebot2
- MuiceBot：https://bot.snowy.moe/guide/introduction
