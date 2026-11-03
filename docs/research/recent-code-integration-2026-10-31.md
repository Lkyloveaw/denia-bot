# 近期代码整合、双轴审查与服务器同步

部署时间：2026-10-31 22:51:26–22:51:32（Asia/Shanghai）。服务器：`192.0.2.226`，目录：`/opt/qqbot`。

## 发布结果

- D「浅色泡泡」状态卡已上线，角色 JPG 随应用安装，无运行时下载。
- 同步 84 个文件：11 个运行代码/资源/示例配置、60 个测试文件、13 个文档与依赖说明；没有安装新依赖。
- 仅重启 `qqbot`；机器人、面板、Playwright 渲染器均 active。机器人 `ready=true, connected=true`。
- 84 个安装文件 SHA-256 一致；现有 `.env`、模型/人格配置、功能配置、超级管理员及面板凭据的哈希保持不变。运行数据库和消息历史未替换。
- 三份数据库 `statuscard.sqlite3`、`game_events.sqlite3`、`disaster_warning.sqlite3` 的只读 `PRAGMA quick_check` 均为 `ok`。
- 服务器使用实际资源与已有采样渲染 D 卡，PNG 为 1170 × 2035；网站行、中文、人物、告警颜色、进程/系统并排和页脚已检查。
- 没有发送 QQ 测试消息，也没有触发推送、签到或账号绑定。

## 整合口径

固定审查基点为 `main@0a33293c0c9700f03a994be6cc4294e4305a8bd6`，当时 `HEAD` 与 `main` 相同，因此 `git diff main...HEAD` 和提交列表为空。近期功能在未提交工作区和未跟踪文件中，审查使用这些文件构造的固定候选快照，而非只看 Git 提交。

服务器 Git HEAD 为 `52d12e0`，但工作目录已有后续改动。逐文件对比确认：游戏活动、终末地、灾害气象地图、图片理解、超级管理员和解析器核心代码已整合。`dashboard.py`、`dashboard.html`、`gate_logic.py` 以服务器当前版本合入本地，保留其功能入口、面板排序和接线；原本地副本另行备份。没有直接用本地目录覆盖服务器，也没有进行 Git 提交、推送或切换线上分支。

审查重点为实际部署差异、状态卡和各功能共用入口；相对 main 的 189 个变更文件未全部逐行重审。

## Standards

独立规范轴未发现可证实的规范违反或阻塞性问题。检查了状态卡渲染/接线、Epic 上游署名要求和三个共用入口；要求背景图及资源说明随程序部署，已纳入 manifest。

计数：0 项未解决发现。

## Spec

独立需求轴发现两项既有问题，本次均先写失败回归再修复：

1. 网络脱敏未隐藏开机累计上下行流量。依据 `docs/OPERATIONS.md` 的公开群网络脱敏要求，补齐 `up_total` 和 `down_total`，保留原始采样。
2. 既有规格要求的网站延迟已经采集并进入 view，却没有画到状态卡。依据 `docs/research/statuscard-port.md` 的状态卡能力清单，增加按需网站区块，显示名称、状态及延迟并限制长文本宽度。

复核又发现并修复两项边界：网络脱敏时同步隐藏整个网站区块，防止名称/状态暴露内部服务；缺少延迟值时保留 `None` 并显示 `--`，实际零延迟仍显示 `0ms`。采样存储兼容 `None`。独立复核确认这些修复有效，未发现本范围剩余问题。

计数：初审 2 项、复核边界 2 项，全部修复；未解决 0 项。

## 验证证据

| 检查 | 最终结果 |
|---|---|
| Windows 本地隔离快照全量测试 | 697 passed、2 skipped、34 subtests passed；141.24 秒 |
| Linux 服务器隔离快照全量测试 | 696 passed、3 skipped、34 subtests passed；33.03 秒 |
| JavaScript 语法 | `node --check` 通过 |
| 无登录管理接口 | `/api/status/overview` 返回 401 |
| 有效会话只读接口 | 状态、游戏活动、终末地、灾害、超级管理员、解析器、综合状态均 200；Epic 配置由综合状态接口读取 |
| 渲染服务 | 3100 `/health` 返回 ready |
| 状态卡 | 已安装的 Pillow 模块出图成功、背景存在、网络脱敏断言通过 |
| 重启后 116 秒检查 | ready/connected 均 true，状态面板 online=true，采样年龄约 53 秒 |
| 本次启动日志 | 插件加载失败、ModuleNotFoundError、Traceback 标记均为 0 |

两端的一个 warning 均为测试引导没有注册 NoneBot adapter 的既有警告。Windows 跳过 POSIX 文件权限及 systemd 用例；Linux 跳过三项 Windows 用例。测试均使用模拟外部调用或隔离目录，不以真实 QQ 投递作为验收。

准备期间修正了测试执行环境：补入 RAG 源码，服务器测试沿用现有 `parser_deps`；模拟 DOM 补齐 `querySelector`；Windows 停进程测试仅在模拟子进程使用执行策略，不改变系统策略。

首次安装后，验收脚本错误地 GET 了仅支持 POST 的 `/api/epic`，因此自动回滚。修正为从 `/api/state` 读取 Epic 配置后，先完成无重启预检，再重新安装成功。首次回滚备份保留为 `/opt/qqbot/.deploy-backup-integration-20261031-224908`。

## 工件与回滚

- 成功发布前备份：`/opt/qqbot/.deploy-backup-integration-20261031-225126`。
- 服务器候选、manifest、测试与验收结果：`/opt/qqbot/.deploy-integration-20261031/final/`。
- 本地工件：`.tmp/integration-20261031/`，含候选源码、部署前服务器源码、哈希清单、pytest XML、审查差异、部署脚本、结果和实际图片。
- 本地三个共用入口的原版本：`.tmp/integration-20261031/local-before/nonebot/`。

回滚前先按成功发布的 manifest 校验当前文件，确认没有后续部署。停止 `qqbot`，仅恢复该备份中的旧文件；对 `before_sha256=null` 的本次新增文件，只移除 manifest 明确列出的那些路径，再启动 `qqbot`。保留所有配置、数据、缓存、数据库及通知历史。如果已有后续修改，应逐项撤销本次差异，不直接覆盖。

## 并行修改边界

候选快照固定并测试后，本地另出现三个解析器并行修改：`nonebot/media_parser/core/sender.py`、`nonebot/media_parser/transport.py`、`nonebot/media_parser/README.md`，以及新增的 `nonebot/tests/test_parser_sender.py`，涉及发送超时及避免重复补发。本次保留这些工作区修改，未覆盖，也未把尚在变化的代码混入已验证发布。它们不包含在上述测试/部署结论中，已有文件差异记录在 `.tmp/integration-20261031/concurrent-local-changes.patch`。
