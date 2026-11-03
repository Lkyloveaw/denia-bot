# 研判：远程 Playwright 渲染（灾害预警卡片）

研判日期：2026-10-31
上游参考：`Pancakes-Labs/astrbot_plugin_disaster_warning` v1.6.5（commit `20ba182`）
目标：本仓库 NoneBot2 + MuiceBot（OneBot v11）

## 结论

V1 采用**自建远程 HTTP 渲染服务**，不接上游的 WebSocket 远程模式，也不使用第三方 T2I：

- 机器人只发 HTML、收 PNG；渲染服务在独立 Docker 容器里
- 容器基于官方 `mcr.microsoft.com/playwright:v1.49.0-jammy`，额外装 `fonts-noto-cjk`
- 卡片 HTML 由本仓库生成（内联 CSS、无外部图片/字体链接），不复制上游 AGPL 模板
- 渲染失败/未配置时自动回退纯文字；不影响地震推送主链路
- 管理面板加「检查渲染服务」按钮，调 `/api/disaster/render-status` 看健康状态

## 协议

### `GET /health`

```json
{"ok":true,"service":"disaster-playwright","ready":true,"renders":12,"failures":0,"active":0,"last_ms":180,"uptime_s":3600}
```

- `ok`：服务进程在线
- `ready`：Playwright/Chromium 可用；本地开发没装 Playwright 时为 `false`
- `renders`/`failures`/`last_ms`：面板状态用

### `POST /render`

请求：

```json
{"html":"<!doctype html>...","width":900,"height":1200,"timeout_ms":20000}
```

- `html`：必填，≤2 MB
- `width` 320–1600，`height` 200–2400，`timeout_ms` 1000–60000
- 可选 `X-Render-Token` / `Authorization: Bearer <token>`（`RENDER_TOKEN` 环境变量）

响应：`200 image/png`；错误为 JSON：

- `400` 参数无效 / 非 JSON
- `401` token 不匹配
- `413` body 过大
- `429` 并发已满（默认 2）
- `503` Playwright 不可用
- `500` 渲染失败

## 容器

- 镜像：`mcr.microsoft.com/playwright:v1.49.0-jammy`
- 额外字体：`fonts-noto-cjk`（中文不出现方框）
- `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`，`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` 复用镜像浏览器
- 运行用户：`pwuser`；Chromium 参数 `--no-sandbox --disable-dev-shm-usage`
- compose 只绑 `127.0.0.1:3000`，不暴露公网
- `shm_size: 1gb`，`MAX_CONCURRENT=2`，`no-new-privileges`

## 安全

- `javaScriptEnabled:false`
- 只允许 `about:blank` 与 `data:` 请求，其他外部请求全部 abort（防 SSRF/外部依赖）
- body 与 HTML 大小限制；并发限制；渲染超时
- 当前机器人客户端不发送 token，V1 依赖回环绑定；不要把 3000 端口暴露公网
- 渲染服务代码是本仓库原创（MIT），未复制上游 AGPL 模板；上游许可边界仍由地图的 AGPL 票处理

## 机器人侧

| 文件 | 职责 |
|---|---|
| `nonebot/disaster_render.py` | 卡片 HTML、`render_png`、`health`、`render_event` |
| `nonebot/plugins/disaster_warning.py` | 渲染成功则发「图片 + 文字」，失败回退文字 |
| `nonebot/dashboard.py` | `GET /api/disaster/render-status` |
| `nonebot/dashboard.html` | 渲染地址/超时/容器名 + 「检查渲染服务」 |

卡片内容：标题/报次、震级、震中、发震时间、深度、最大烈度/震度、距本地、事件编号、来源、免责声明。V1 只有地震卡，不含地图瓦片/海啸/台风图。

## 验证

- 渲染服务：`node --check` 通过；本机无 Playwright 时启动服务，`/health` 返回 `ready:false`，`/render` 返回 `503`，不崩溃
- 机器人客户端：`httpx.MockTransport` 覆盖成功、HTTP 错误、非图片、无效地址、`health`、未配置回退
- 接线：注入假 renderer，确认推送带 `image` 且文字仍完整
- 全量测试：**314 passed / 1 skipped**
- 本机 Playwright 1.49 + Chromium 131 端到端：启动 `server.js`，机器人侧 `render_png` 收到 **107,489 字节 PNG（900×1200）**，中文/布局正常；样例见 `docs/research/disaster-card-sample.png`
- Docker 镜像未在本机构建（本机没有 Docker）；部署后按 README 的 `/health` 与面板「检查渲染服务」复核

## 部署

```bash
cd docker/playwright-renderer
docker compose up -d --build
curl http://127.0.0.1:3000/health
```

面板「灾害预警 → 远程 Playwright」填 `http://127.0.0.1:3000`，超时 20 秒，容器名 `qqbot-disaster-playwright`；点「检查渲染服务」。

## 开发服务器实测（2026-10-31）

- 主机：`192.0.2.226`（hostname `Development`），root；系统 Node v18.19.1。
- 已有 **Node Playwright 1.60.0**（在 `/root/.npm/_npx/e41f.../node_modules/playwright` 缓存；不是全局包，普通目录 `require.resolve` 找不到）；`playwright-core 1.60.0`；Chromium `chromium-1223`；**没有 Python Playwright**。
- 中文字体：WenQuanYi Zen Hei / Unifont；没有 Noto CJK。
- Docker 29.9.2 可用，容器有 SnowLuma/MCS；**未部署本项目 Docker**（用户选择宿主机复用）。
- 端口：3000 被 personal-wiki 占用；3100 空闲；SnowLuma 的 3000–3001 只在容器内网。
- 部署：把 `docker/playwright-renderer` 复制到 `/opt/qqbot/docker/playwright-renderer`，把缓存里的 `playwright`/`playwright-core` 复制到 `vendor/`；`systemd` 单元 `qqbot-playwright-renderer.service` 以 `HOST=127.0.0.1 PORT=3100` 常驻。`/health` 返回 `ready:true`；当前生产 B 气象卡 900×1200 实测 **106,895 字节**，中文正常。
- 服务器 `/opt/qqbot` 原为旧提交 `52d12e0`；2026-10-31 15:17 已复制 `disaster_*` 模块/插件、给 dashboard/gate 打最小补丁并重启。`disaster_warning` 加载成功，面板 `/api/disaster` 401、页面含灾害区块，配置 render URL 为 `http://127.0.0.1:3100`；服务器端 `render_event` 返回 79,290 B PNG。Wolfx 四个端点从服务器均 OK。注意这些文件在服务器上仍未提交/未跟踪，正式升级应先进仓库。

## 未做/后续

- 地图瓦片、台风路径图、海啸官方图件
- 上游 WebSocket 远程模式与多浏览器池
- render token 的机器人侧配置（当前留空，仅回环）
- 容器状态用 Docker inspect 还是健康接口：V1 用健康接口，容器名暂只作展示/后续扩展
