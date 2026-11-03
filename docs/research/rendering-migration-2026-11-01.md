# 同机 Playwright 容器迁移验收

实施日期：2026-11-01（Asia/Shanghai）。正式切换约 18:48，18:53 完成上线健康核验。

## 结果

九类业务卡片已统一使用服务器 `192.0.2.226` 上的 `qqbot-playwright` 容器，入口为 `127.0.0.1:3100`。卡片排版和截图不再由宿主机 Pillow 或宿主机 Playwright 服务执行。状态卡保留已选 D 浅色泡泡、达妮娅背景及原有信息分区；长菜单、解析卡和报表按内容块分页。

Pillow 保留用于素材压缩、EXIF 方向修正、图片转码、输出缩放以及既有 GIF/PDF 组装；二维码仍用 qrcode。这些不是卡片排版后端。

| 用途 | 模板 / 调用接口 | 正式容器来源标记 |
| --- | --- | --- |
| 天气 | weather_image.render_pages | weather |
| 群话题 | weather_image.render_pages | group_topics |
| 帮助菜单 | help_image.render_help_images | help |
| Epic 喜加一 | epic_free_image.render_pages | epic |
| 服务器状态卡 | status_image.render_status_card_pages | statuscard |
| 日报 / 周报 | status_image.render_report_pages | status_report |
| 链接解析预览 | Renderer.render_cards | parser |
| 灾害预警 | disaster_render → render_client | disaster |
| 终末地 | endfield.render → render_client | endfield |

正式健康快照中九个来源均有成功计数，累计 17 次成功请求、0 次失败、active=0。这是核验时刻的计数，包含固定样张和运行中的正常请求，不是长期可用率统计。机器人 connected=true；机器人和面板服务均 active，容器 running/healthy。

旧 `qqbot-playwright-renderer.service` 已停止并禁用。临时 3101 容器、3102 宿主进程及本地验收 SSH 隧道均已清理，渲染端口仅剩 Docker 绑定的 `127.0.0.1:3100`。

## 实现与审查

- `render_client.py` 统一请求大小、PNG 校验、连接池、同步/异步接纳限制和错误；`render_settings.py` 统一地址。面板「图片渲染服务」显示实际就绪状态，设置保存到 `nonebot/data/renderer.json`，下次调用读取。默认地址不需要额外配置文件。
- 公共传输、终末地客户端和灾害健康检查显式忽略宿主代理环境，确保回环请求不受 HTTP_PROXY 等变量影响；收尾复测 39 项通过。
- 旧灾害 `remote_url` 和 Endfield `render_url` 字段保留兼容读取，但不再控制有效地址。进程环境 `QQBOT_RENDER_URL` 优先于共享文件；此时面板地址只读。仅接受本机回环 HTTP 地址。
- 服务在读取请求体之前占位，最大并发 2；完整 JSON 不超过 2 MiB。单图溢出、单块过高和超出分页预算明确报错，业务回退文字或保留原媒体。
- `/render-pages` 最多 12 页、20M 总像素；支持 320–1600 px 布局宽度和最高 2400 px 单页。状态卡仍按 1170 px 排版，输出宽度 760–2880 的配置由后处理缩放保持。
- 浏览器真正完成测试截图才 ready；退出后清除状态并重建。页面禁用脚本、service worker 和外部资源。日志只含来源与错误分类，不打印 HTML 或原始业务数据。
- 保留帮助菜单 / 解析器的共享缓存路径映射和原媒体发送顺序。多页预览作为独立图片消息；渲染失败保留标题、链接和媒体。
- Playwright 镜像与 npm 包固定 1.60.0；镜像摘要固定为 `sha256:9bd26ad900bb5e0f4dee75839e957a89ae89c2b7ab1e76050e559790e946b948`，与官方清单 SHA-256 一致。下载使用镜像代理加速后核对摘要，未更换版本。
- 原 HYS 字体、Noto CJK、Noto Color Emoji 已安装并通过 fc-match 验证。CPU 1、内存 768 MiB、共享内存 256 MiB、pids 256；上线验证后观察到约 215.5 MiB 内存，不能视为峰值承诺。

本轮审查了公共服务、传输、配置、分页、模板与插件接线差异，并修正了并发占位、字体名称、D 背景定位、网络图例、告警颜色、透明素材白底、旧单页测试桩以及共享地址一致性。沿用来源文件和第三方声明，没有把衍生模板的许可证改为公共模块许可证。

同时整合了此前 main 已审查的 Endfield client/store/scheduler/plugin 修复及其回归测试。原工作区其他未提交内容保留，未执行批量提交或重置。

## 验证证据

本地证据目录：`.tmp/render-migration-20261101/`。

| 检查 | 结果 / 证据 |
| --- | --- |
| 九类真实出图 | `previews/container/manifest.json` 与 `previews/deployed/manifest.json`；均使用真实 Chromium |
| 灾害变体与实际帮助菜单 | 地震、气象、台风、海啸的 neutral / character 共 8 张均成功；真实命令清单成功落盘，见 `final-variants.json` |
| D 默认 / 精简 / 760 px 窄版 | 均出图；人物位置、信息块、网络图例已人工检查 |
| 多页菜单 / 长报表 / 解析长文 | 均分页，单页不超过 2400 px |
| 服务单元测试 | Node 5 项通过 |
| 渲染及接线复测 | 72 项通过，见 `tests-fixed.log` |
| 仓库回归 | 排除下述既有失败文件后，754 passed、2 skipped、43 subtests passed，见 `tests-final.log` |
| 跳过原因 | Windows 不适用的 systemd / POSIX 权限检查 |
| 双并发、第三请求拒绝 | 429；断连后 active 回到 0 |
| 超时清理 | 复杂文档触发 504，随后正常请求成功 |
| 六个调用排队 | 六个均成功，服务并发仍为 2 |
| 内容超界 | 单图 / 超高块返回 422，超宽参数返回 400 |
| 外部图片 | 被禁止，不会成功生成含外链资源的卡片 |
| 浏览器退出恢复 | 候选浏览器终止后观察到 503；约 1.01 秒恢复 ready |
| 容器重启 | 恢复 ready；故障注入仅对候选容器执行 |
| 面板脚本 | node --check 通过；上线 `/api/renderer` 未登录返回 401 |
| 文件一致性 | 53 个部署文件本地与服务器均匹配验收摘要 |

`test_parser_forward_retry.py` 在第一次全量运行中有 4 项失败（包含群聊/私聊两个子测试），原工作区单独运行也复现同样 4 项失败：测试期待上传明确拒绝后的有限重试，而现有 transport 不执行该重试。这属于本轮开始前已有的解析器工作，transport 本轮未修改、未覆盖部署；最终回归明确排除了这一文件，不能称全仓所有测试无条件通过。

验收通过本机接口生成图片，没有通过工具发送 QQ 测试消息。真实发送路径由协议适配与接线测试验证，未把 HTTP 成功等同于逐个命令的人工聊天验收。

## 部署与回退

53 个文件的审核清单为 `.tmp/render-migration-20261101/deployment-plan.json`。部署前按摘要检查本地和服务器漂移；两份服务器文档仅缺少本地已有说明，经差异审查后更新。源文件、字体原图、运行数据和其他会话改动均按清单处理，未覆盖 `.env`、Cookie、配置数据库或缓存。

- 正式代码：`/opt/qqbot`
- 正式容器：`qqbot-playwright`，镜像 `qqbot-playwright:20261101`
- 服务器备份：`/opt/qqbot-backups/render-container-20261101`
- 本地原文件备份：`.tmp/render-migration-20261101/local-before/`
- 服务器备份清单：`manifest.json`；切换结果：`deployment-result.json`

如需回退，运行服务器备份目录中的 `rollback.py rollback`。它先停机器人/面板和正式容器，恢复本轮原文件，再启动原宿主渲染服务及机器人/面板。必须同时恢复调用代码和渲染服务；旧宿主版本不支持新的 `/render-pages`。

## 实测图

以下为固定测试数据，不代表服务器当时负载。原 D 对照保存在 `.tmp/render-migration-20261101/previews/host-fixed/status-D-before-1.png`。

![D 方案容器渲染样张](rendering-migration-preview/status-D.png)
