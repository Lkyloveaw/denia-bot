# 未合并分支审查与整合 · 2026-10-31

用户确认目标为现有 `main`（仓库没有 `master`）。固定基点为
`0a33293c0c9700f03a994be6cc4294e4305a8bd6`，审查覆盖下列四个分支的完整差异与整合后的修复。

| 分支 | 审查时 tip | 提交数 | 整合记录 |
|---|---|---:|---|
| `research/endfield-upstream` | `4200cc72accd29bd8dfbecff2eec1a23d86ddf1d` | 1 | `2cfbb13` |
| `research/endfield-integration` | `21ece622b14d2a050b93fc14e38d5e6dc136a653` | 1 | `420288a` |
| `research/endfield-nonebot` | `6e7e8bbf21529898f882b638bfcafab4ee75d088` | 1 | `cdb60b1` |
| `feat/endfield-impl` | `25c2ab08740ba636764beec77b63d6b1a061d621` | 8 | `58ca232` |

四个分支各自以 merge commit 保留完整祖先历史。审查时远端只有 `origin/main`；
`codex/game-events` 与基点一致，没有独立提交。没有删除分支或既有工作树。

## 审查依据与范围

- 每个分支使用 `git diff 0a33293...<tip>`，整合候选再对固定基点核对。
- GitHub Issues #1、#2、#3、#20、#34、#35、#38 的正文和讨论；
  `endfield-port.md`、`endfield-api.md`、`endfield-takeover.md`。
- 根 AGENTS.md 及 `docs/agents/` 的会话规则，README、PITFALLS 与接入面文档的工程约定。
- Standards 与 Spec 由两个独立 reviewer 审查，修复后分别复核。
- 三个研究分支是固定版本的历史调研；没有将文中提到的未提交工作区功能误当成此次已分发代码。

## Standards

原分支存在三个需要处理的问题：群聊依赖 `is_tome()` 而不是原始消息段
（`docs/PITFALLS.md` 的 @ 判断约定）；独立终末地页面写操作未携带 CSRF
（`endfield-integration.md` 的统一 `api()` 约定）；README/NOTICE 对 MIT、AGPL
和实际分发目录的说明不准确。候选已改为复用 `gate_logic.mentions_self`、
遵循 `QUOTE_AT_COUNTS`、接入主面板统一鉴权，并修正许可与外部渲染器说明。

剩余一项非阻塞判断：**possible Duplicated Code**。
`nonebot/endfield/config.py:190`、`store.py:533`、`scheduler.py:585` 和
`nonebot/endfield_panel.py:292` 分别实现相似的 0600 原子 JSON 写入，有维护一致性的成本。
本次没有仅为消除重复而改动不同模块的写盘边界。

Standards：最终 **0 个阻塞项，1 个非阻塞判断项**；剩余项等级 P3。

## Spec

原实现落后于同日已部署修复，候选补入授权角色解析、创建网关绑定、授权超时/撤回、
共享存储、数字角色与区服定位、配置开关、面板 Key 优先/热读取、调度间隔和图卡分页等修复。

本次新增修复：

1. 逐账号保存签到进度；在 POST 前记录结果未确认状态，成功、超时或中断后不自动重放。
   明确 429 的账号延后 60 秒且只重试一次，账号间隔 1.5 秒。
   依据 `endfield-port.md` 第 9.3 节和 #16 的逐账号、限流及防重要求。
2. 普通参数错误的 HTTP 400 归协议错误，不再使有效绑定失效。
   依据 `endfield-api.md` 的错误语义及 `endfield-port.md` 第 6 节。
3. 理智/券提醒保留四小时冷却，失效凭据只提示一次并支持投递重试；
   签到失败通知连续投递失败达到阈值后停用，重新订阅恢复新的失败预算。
   依据 `endfield-port.md` 第 9.2–9.3 节与 #16。
4. 同角色重新授权保存新的网关绑定 ID，尝试清理旧绑定，删除操作命中新 ID；
   清理失败提示人工处理，重新授权成功后不再投递过期的失效通知。
   依据方案的绑定更新、失效保留与用户自助删除要求。
5. 文档明确渲染器为独立前置服务，补充 `/render` 与 `/health` 契约，
   将历史方案与当前交付能力分开，说明内存配额的重启限制。

Spec：定向复核后 **0 个剩余合并阻塞项**；三个研究分支未发现阻塞性需求偏差。

## 验证

- 最终隔离候选全量离线测试：**213 passed, 2 skipped, 3 subtests passed**。
- 其中最后一轮授权/订阅恢复定向回归：**65 passed, 1 skipped**。
- 保留现有 NoneBot「未安装 adapter」测试环境警告。
- 主面板实际路由验证：未登录 401、缺 CSRF 403、错误 Origin 403、合法配置写入 200、
  `/endfield` 重定向及主页面入口通过。验证使用临时认证文件和写盘 mock。
- 提取主页面 JavaScript 后执行 `node --check`，语法通过。
- 合并差异通过 `git diff --check`；所有测试使用合成账号，未进行真实授权、签到或 QQ 推送。
- 运行证据在本地 `.tmp/branch-review-20261031/`，不提交测试日志和私有配置。

## 工作区与运行边界

整合在独立 detached 工作树中完成。当前 `codex/game-events` 的未提交修改保留；
除明确核对并移入候选的终末地修复和测试引导调整，其余状态卡、灾害、Epic、游戏活动、
解析器等未提交功能没有纳入本次分支合并。并行解析器任务产生的修改也未覆盖。

本次完成 Git 分支整合；服务器仍运行此前部署版本，本次审查新增修复尚未另行部署。
真实账号授权、签到和同步未做人工验收；扫码绑定与上游 16 套视觉模板仍未实现。
图卡需要独立渲染服务，不可用时回退文本。每日命令和推送配额重启后清零；
签到进度、提醒冷却和限流退避已持久化。详见 `../endfield.md`。
