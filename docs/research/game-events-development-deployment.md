# 游戏活动插件：开发服务器部署验收

日期：2026-10-31，时区 Asia/Shanghai。

## 部署结果

- 开发服务器：`192.0.2.226`；项目目录：`/opt/qqbot`。
- 管理面板：`http://192.0.2.226:8092`，侧栏「游戏活动」。
- 安装了 7 个游戏活动模块；在服务器当前版本的 `dashboard.py`、`dashboard.html`、`gate_logic.py` 合入入口、接口和帮助分组，共 10 个文件。
- 服务器工作树含独立改动，没有用本地整个工作树覆盖服务器。部署前发现并保留了同期灾害功能更新；安装前再次校验文件 SHA-256。
- `qqbot`、`qqbot-dashboard`、`qqbot-playwright-renderer` 均为 active；机器人健康接口返回 `ready=true, connected=true`。
- 最终文件清单的 10 个 SHA-256 校验通过；SQLite `PRAGMA quick_check` 返回 `ok`。

## 验收证据

| 项目 | 结果 |
|---|---|
| 服务器隔离测试 | 最终版本 161 passed，1 warning；6.61 秒 |
| 本地完整游戏测试 | 161 passed，1 warning；35.89 秒 |
| 最后配置接线修复 | 本地消息与插件测试 61 passed；服务器完整 161 passed |
| 面板鉴权 | 无登录 401；错误 CSRF 403；有效会话读取成功 |
| 其他管理接口 | 灾害、状态、超级管理员、解析器、综合状态均 200 |
| 真实浏览器 | 游戏活动独立入口、白名单、黑名单可见；星铁预览 13 项；无页面 JS 异常 |
| 窄屏 | 390 × 844 下无横向溢出 |
| 后台刷新 | 通过面板提交 data 任务，机器人执行成功；10 游戏源无错误；sent=0 |
| 私聊测试 | 用户明确指定 `10000001`；依次按文字、图片、合并转发模式测试，3 条投递记录均 success |
| 清理 | 临时订阅 0，紧急目标 0；合并转发恢复关闭；紧急提醒保持关闭 |

测试警告为 NoneBot 测试引导未注册 adapter 的既有警告。QQ `success` 表示平台调用成功返回；投递日志未单独区分格式降级，也不代表收件人已阅读。封面在发送前另行验证能生成图片段。

十个游戏入口的有效活动数（本次快照）：

| 游戏 ID | 有效活动 |
|---|---:|
| sr | 13 |
| ys | 7 |
| zzz | 9 |
| ww | 10 |
| nte | 10 |
| ba-cn | 4 |
| ba-global | 1 |
| ba-jp | 2 |
| endfield | 18 |
| arknights | 8 |

有效数包含当前尚未结束及未来活动，不含历史结束项。终末地解析总数 145，其中 127 项已过期；查询与提醒不会混入这些历史项。

## 封面网络兼容修复

开发服务器的系统 DNS 对 `resource.starrailassistant.top`、`data.akedata.wiki` 返回 `198.18.x.x` Fake-IP。原下载器正确拒绝非公网地址，导致封面降级文字。

新增可选 `GAME_EVENT_COVER_DNS=google`，仅封面解析使用固定 Google DoH 服务查询真实 IPv4。默认仍为 `system`；配置可来自 NoneBot `.env` 或进程环境。两种模式都保留公网地址检查、固定 IP 连接、原域名 SNI/证书校验、跳转限制和下载大小限制；没有更改服务器系统 DNS 或放行内网地址。新增 8 项回归覆盖公网解析、失败响应、私网/Fake-IP/混合地址以及过大响应；启动测试覆盖 `.env` 配置传递。

开发服务器通过以下 systemd drop-in 启用：

```ini
# /etc/systemd/system/qqbot.service.d/game-event-covers.conf
[Service]
Environment=GAME_EVENT_COVER_DNS=google
```

最终实测星铁和终末地封面均能构建 1 个图片段。PRTS 活动数据正常，但其封面地址在 `prts.wiki` 与 `sisyphus.prts.wiki` 间循环重定向，触发跳转限制；当前回退为文字和原站封面链接。这是尚未消除的原站封面兼容限制。

## 记录与回退

- 原文件备份：`/opt/qqbot/.deploy-backup-game-events-20261031-152033`。
- 封面修复前版本：上述目录内 `cover-dns/`。
- 暂存与完整验收记录：`/opt/qqbot/.deploy-game-events-20261031/`，包括 `manifest-final.json`、`live-smoke.json`、`browser-smoke.json`、`dm-smoke.json`、`final-check.json` 和截图。
- 本地副本：`.tmp/game-deploy-20261031/`。部署脚本、隔离测试副本和凭证没有加入产品文件；脚本使用现有面板会话，未输出会话 token 或更改管理员密码。

回退前先确认服务器文件与最终 manifest 一致，避免覆盖后续部署。关闭游戏活动全局推送，停止 `qqbot` 与 `qqbot-dashboard`，恢复备份中的三个集成文件，将 `nonebot/plugins/game_events.py` 移出插件目录，再启动两项服务。保留 `data/game_events.sqlite3` 及缓存供恢复使用；不要清空通知历史。若同时撤回专用 DNS 模式，移除上述专用 drop-in 并执行 `systemctl daemon-reload`。若部署后又有其他功能修改这三个集成文件，应只撤销游戏活动集成点，不直接覆盖整个文件。

验收后尚未添加常驻订阅；面板白名单、黑名单均为空，按既定规则表示不限会话，黑名单优先。正式使用前由管理员添加需要的会话限制和订阅。
