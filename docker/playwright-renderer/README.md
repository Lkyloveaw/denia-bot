# 同机图片渲染容器

天气、群话题、帮助菜单、Epic、状态卡、报表、解析预览、灾害和终末地统一由此服务排版和截图。Pillow 只做已有素材的压缩、裁剪、转码与最终缩放。各业务的来源和许可证仍按文件头及 docs/third-party 保留；公共传输层和服务为原创 MIT 实现。

## 启动与配置

```bash
cd /opt/qqbot/docker/playwright-renderer
docker compose build
docker compose up -d
curl --fail http://127.0.0.1:3100/health
```

构建上下文是仓库根目录。Playwright 镜像与 npm 包均固定 1.60.0，npm ci 使用锁文件。镜像内含 HYSongYunLangHeiW、Noto CJK 与 Noto Color Emoji；字体和背景资源来源不变。容器名 qqbot-playwright，只绑定 127.0.0.1:3100，内存 768 MiB、共享内存 256 MiB、CPU 1、最大并发 2。

统一地址默认 http://127.0.0.1:3100，面板「图片渲染服务」可保存到 nonebot/data/renderer.json，机器人与面板下次调用立即重读。仅允许本机回环 HTTP 地址。旧灾害 remote_url、Endfield render_url 字段保留读取兼容，但不再控制传输地址。进程环境 QQBOT_RENDER_URL 优先于共享文件；设置时面板地址只读。该变量指进程环境，单独写 NoneBot .env 不会配置面板进程。可选鉴权使用容器 RENDER_TOKEN 与调用进程 QQBOT_RENDER_TOKEN；值不要写入代码或面板响应。

候选验证使用独立项目、容器名与端口：

```bash
RENDER_PORT=3101 RENDER_CONTAINER_NAME=qqbot-playwright-candidate docker compose -p qqbot-render-candidate up -d
QQBOT_RENDER_URL=http://127.0.0.1:3101 python -m pytest nonebot/tests -q --import-mode=importlib
```

## 接口与失败行为

- GET /health：浏览器已真实截图后返回 ready:true 和 HTTP 200；未就绪返回 503。包括 deployment、active、成功/失败计数与来源计数。
- POST /render：自包含 HTML → 单张 PNG。宽 320–1600、高 200–2400；内容溢出返回 422，禁止静默裁切。
- POST /render-pages：模板含 data-card 根，按直接子内容块分页；响应 pages 为 PNG base64 数组。最多 12 页、总计 20M 像素，单块过高返回 422。
- POST /render-card：完整截取唯一的 data-card 根，返回一张 PNG。帮助菜单使用此接口，自适应多列宽度、按内容取高度，不套用普通卡片的 1600 像素宽和 2400 像素分页限制；仍受 20M 总像素预算保护，溢出或超预算返回 422。
- 完整 JSON 请求最多 2 MiB；PNG 响应校验签名、尺寸和类型。来源通过 X-Render-Source 记录，不记录 HTML、原始数据或令牌。
- 客户端共用 2 个执行位置、最多 10 个接纳位置，等待最多 5 秒；429/超时/断连返回错误，由业务回退文字或继续发送原媒体。不自动改用 Pillow 绘制卡片。
- 仅允许 data/about:blank 资源，禁页面 JS 和 service worker；外部素材由机器人下载、预处理后内嵌。浏览器退出会清除就绪状态并自动重建。

## 运维与回退

```bash
docker logs --tail 40 qqbot-playwright
docker inspect qqbot-playwright --format '{{.State.Health.Status}}'
docker compose restart
```

旧 qqbot-playwright-renderer.service 仅作为回退保留，正式容器接管 3100 后必须停用，避免端口争用。回退时先停容器、恢复本轮备份的业务文件，再启动旧 systemd 服务；旧宿主服务没有 /render-pages，不能只切回服务而保留新调用代码。

纯单元测试不需要服务，实际图片测试需设置 QQBOT_RENDER_URL。部署验收还应包含全部业务样张、分页、429 占位释放、浏览器退出恢复及重启后真实就绪；不得对正式业务发送测试消息。
