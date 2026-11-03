# 灾害预警 V1 验收、灰度与回滚 runbook

日期：2026-10-31（Asia/Shanghai）
状态：**验收未开始**；规格单 #39 已建并挂到 #21；#32 已通过对抗式审查并关闭；后续按本 runbook 执行。
范围：Jian 气象/海啸 + Wolfx 地震。EQSC/台风已关闭，不在 V1 live 范围。

## 1. 结论摘要

- 混合验收：天气用真实推送；海啸/地震用 owner-only 合成样例覆盖 P0；真实海啸/地震作为上线后 72h 观察项，不阻塞 V1。
- 当前群是正式运维群；无独立测试群。先做 owner-only 预演，然后“群 + 主人”同时上线，接受首轮误推风险。
- 观察窗口：预演后群+主人 24h；之后 72h 生产观察；用户本人签字。
- 回滚：手动 L1/L2/L3；保留事件库；验收前做脱敏备份。
- 合成样例通过一次性 bot 内验收端点发送，不新增生产命令；验收后删除端点。
- 不自动回滚；不重试/补发；不覆盖 EQSC/台风。

## 2. 当前事实与基线

- 天气：事件库 69 条 `filtered`、2 条 `pushed`；4 条推送记录（群 + 主人）均为 `ok=1`。
- 地震/海啸：事件库暂无记录；Wolfx 四端点连通正常。
- 渲染：宿主机 Playwright 1.60 + Chromium 1223，`127.0.0.1:3100`，`/health ready`；无 Docker 容器状态。
- 面板：`http://192.0.2.226:8092`，无登录 `/api/disaster` 401；CSRF/会话规则沿用现有面板。
- 服务器：`/opt/qqbot`，`qqbot`/`qqbot-dashboard`/`qqbot-playwright-renderer` active；灾害文件未跟踪/未提交。
- EQSC：`enabled=false`、token 已清空；`source_health` 仍有旧错误，验收前清理。

## 3. 验收前置

### 3.1 备份（脱敏）

在服务器创建：

`/opt/qqbot/.deploy-disaster-acceptance-<YYYYMMDD-HHMMSS>/`

包含：

- `manifest.sha256`：`nonebot/disaster_*`、`nonebot/plugins/disaster_warning.py`、`nonebot/dashboard.py`、`nonebot/dashboard.html`、`nonebot/gate_logic.py` 等实际接线文件的 SHA-256。
- `config.redacted.json`：`nonebot/data/disaster_warning.json` 删除 `sources.jian.token`、`sources.eqsc.token` 等敏感字段后保存；不复制 `.env`/token。
- `disaster_warning.sqlite3.bak`：使用 SQLite online backup 或停 bot 后复制；WAL 活跃时不能只复制主文件。
- `systemd/`：`qqbot.service`、`qqbot-dashboard.service`、`qqbot-playwright-renderer.service` 的 unit。
- `git-status.txt`：`git status --short` 与当前 HEAD。
- 保留到签字后 7 天；不要放入 git。

### 3.2 清理与健康检查

- 清理 EQSC 旧 `source_health` 行（只清观测记录，不改 EQSC 配置；EQSC 保持 disabled）。
- `systemctl is-active qqbot qqbot-dashboard qqbot-playwright-renderer`。
- `curl http://127.0.0.1:3100/health` → `ready:true`。
- 面板无登录 401、错误 CSRF 403、有效会话读取 200。
- `灾害源状态`：Jian/Wolfx 最近成功；无 token 泄露。

## 4. P0 验收矩阵

| 项 | 操作 | 通过条件 | 证据 |
|---|---|---|---|
| 真实天气 | 使用已有 2 条事件/4 条推送 | 群+主人收到图文；无重复 | 推送记录截图/日志 |
| 海啸卡片 | owner-only 合成 | 900×1200、中文正常、首报中性/后续达妮娅 | PNG + 私聊截图 |
| 地震卡片 | owner-only 合成 | EEW/情报文案、tone、缺字段回退 | PNG + 私聊截图 |
| 渲染失败回退 | 停渲染器后触发 | 文字照发；恢复后卡片正常 | 日志 + 私聊截图 |
| 过滤/去重/重启 | 自动化 + 受控配置 | 过滤记录；无重复；重启不补历史 | pytest/事件库 |
| 源离线 | 单源错误/断网模拟 | 任务不退出；状态可见 | 日志/状态 |
| 命令/帮助 | 8 命令/两组帮助 | 空态、状态、测试命令正常 | 命令输出 |
| 面板 | 鉴权/CSRF/读写 | 401/403/200；token 不回显 | HTTP 证据 |
| 密钥 | 检查事件库/日志/截图 | 无 token/.env/Authorization 明文 | 扫描结果 |

自动化命令按仓库惯例执行；当前定向测试和全量结果以执行时输出为准，不把历史通过数当本次证据。

## 5. 一次性验收端点

目的：不新增生产命令，把合成样例真实发到主人私聊。

- 临时模块只在验收期间加载；路由仅接受 `127.0.0.1`/`::1`，要求独立随机 `X-Acceptance-Token`（不是 bot token），无需 QQ 命令。
- 请求体仅允许 `kind`（weather/tsunami/earthquake）、`tone`（neutral/character）、`stopped`；服务端调用现有渲染/格式化逻辑，只向主人私聊发送，不写事件库、不推群、不修改 seen。
- 端点只用于 P0 合成与渲染回退，不模拟真实源数据；验收后删除模块并重启 `qqbot`，确认路由 404。
- 不在生产保留开关或命令；不接受公网访问。

预演顺序：

1. 设 `push.enabled=false`（或清空群目标），确保群不接收预演消息。
2. 加载临时端点，按矩阵发送 owner-only 样例。
3. 你确认收到并核对；记录截图/日志。
4. 删除端点并重启 bot，确认 404。
5. 恢复群+主人目标，开启推送，记录 cutover 时间。

## 6. 灰度与观察

- cutover：群 + 主人同时启用；记录时间。
- 24h：无崩溃、无重复推送、无意外群消息、源错误可见、渲染失败有文字回退。
- 72h：无 P0；检查事件库/推送记录/源健康/日志。
- 若出现 P0：按 L1 立即关推送，保留现场，再决定 L2/L3。
- 真实海啸/地震若在观察期出现，补记真实样例；没有出现则记录为“观察缺口”，不伪造通过。

## 7. 回滚

### L1 配置级（首选）

- 面板关 `push.enabled`；仍异常则关 `enabled`。
- 保留事件库/状态；记录时间与原因。

### L2 源/渲染级

- 停用对应源或渲染器；保留数据库；确认文字回退仍可用。

### L3 文件级

- 仅在代码级故障时恢复 `manifest` 对应文件/配置；数据库保留，不清空。
- 恢复后重启 `qqbot`/`qqbot-dashboard`；核对路由/面板/源状态。
- 若部署后又修改过集成文件，只恢复 disaster 集成点，不整文件覆盖他人改动。

## 8. 排除项

- EQSC/台风 live；Docker 容器状态（宿主模式只有 HTTP 健康）；失败补发/重试；多会话订阅；`/灾害预警模拟`；生产测试命令。
- 自动回滚。
- 清空事件库/通知历史。

## 9. 签字

- 签字人：用户本人。
- 签字条件：P0 全绿 + 24h/72h 无 P0 + 证据留存。
- 签字记录写回 #32；在此之前 #32 保持 open。

## 10. 相关笔记

- `docs/research/disaster-v1-ux-spec.md`：用户可见规格与口吻。
- `docs/research/disaster-nonebot-mapping.md`：接线契约、测试矩阵、已知风险。
- `docs/research/disaster-event-storage.md`：事件身份/去重/24h-7d/保留。
- `docs/OPERATIONS.md` 8.8：宿主机渲染器。
- `docs/research/game-events-development-deployment.md`：仓内 live 验收/备份/回滚先例。
