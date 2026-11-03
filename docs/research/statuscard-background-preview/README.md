# 达妮娅服务器状态卡：背景选型预览

2026-10-31。用户已选择 **D 浅色泡泡**，已接入 `nonebot/status_image.py` 本地 Pillow 渲染器，并于当日 22:51（北京时间）部署服务器。整合审查、验证与回滚记录见 [近期代码整合验收](../recent-code-integration-2026-10-31.md)。

实际渲染样张位于 `selected/`：`statuscard-D.png`（完整）、`compact-D.png`（精简）、`narrow-D.png`（760 px）、`stress-D.png`（长文本及告警）、`report-D.png`（共享浅色基础配色的日报）。所有样张均使用模拟数据。

重现实际样张：`.\nonebot\.venv\Scripts\python.exe docs/research/statuscard-background-preview/render_selected.py`。

实现验证：最终本地全仓 697 项通过、2 项平台相关跳过；服务器 696 项通过、3 项平台相关跳过，均含 34 个通过的子用例。整合审查补齐了网站延迟区块、网络脱敏和未知延迟显示；独立复核通过。服务器实际 PNG 为 1170 × 2035，布局已目视检查。

选型前实现：`nonebot/status_image.py`，Pillow 本地直绘，默认宽 1170 px，仅有深紫渐变、粉色装饰、星点和泡泡，没有角色背景资源加载入口。

最初按资源环、磁盘、网络、进程、系统、QQ 统计的信息结构制作 HTML/CSS 预览，再由本机 Chromium 导出 PNG。人物使用检索得到的原图，没有 AI 重绘。顶层 A/B/C/D 图片保留为选型稿；实际代码效果以 `selected/` 中的 Pillow 样张为准。

| 方案 | 视觉处理 | 适合的取向 | 文件 |
|---|---|---|---|
| A 星夜全幅 | 周年庆竖图，深紫渐变遮罩，半透明数据面板 | 人物大、氛围浓，整张卡都有角色存在感 | A.png |
| B 剧场横幅 | 舞台星空原图用于顶部，下面恢复深色面板 | 推荐；场景丰富，数据区清晰 | B.png |
| C 立绘陪伴 | 透明角色立绘放右上，淡紫轨道装饰 | 人物轮廓清楚，背景干净，容易延续现有配色 | C.png |
| D 浅色泡泡 | 粉蓝泡泡横图，浅紫底与白色数据面板 | 明亮、柔和，适合偏爱浅色的人 | D.png |

`comparison.png` 为四版对照，`sources.png` 为原始素材对照。四张独立状态卡均为 1170 × 1810 px。

## 素材来源

原始下载文件保存在 `assets/`，保留文件内容，页面仅通过 CSS 做显示裁切和遮罩。角色与美术权属不因本预览而改变。

### A：周年庆吹泡泡

- 来源页：<https://www.zerochan.net/4692267>
- 下载：<https://static.zerochan.net/Denia.%28Wuthering.Waves%29.1024.4692267.webp>
- 本地：`assets/anniversary.webp`，1024 × 1820。
- 搜索结果将其标为 KuroGames 周年庆作品；本次使用转载站版本，尚未核对首发账号。

### B：舞台星空

- 已核对的鸣潮官方 B 站动态：<https://www.bilibili.com/opus/1183931799897636868>
- 发布日期：2026-03-26。
- 从官方动态取得的图片：<https://i0.hdslb.com/bfs/new_dyn/4077c3fe3de40de08a3cab3f6d408de01955897084.jpg>
- 本地：`assets/stage.jpg`，2560 × 1440。

### C：角色透明立绘

- 来源页：<https://wiki.biligame.com/wutheringwaves/共鸣者/达妮娅>
- 下载：<https://patchwiki.biligame.com/images/wutheringwaves/a/af/6acl4kwx70rsngp29y1ehmnqx4iippw.png>
- 本地：`assets/standing.png`，2048 × 2032，含 alpha 通道。

### D：粉蓝泡泡横图

- 来源页：<https://www.pixiewall.com/wallpaper/denia-wuthering-waves-bubbles-4k-33810>
- 下载：<https://www.pixiewall.com/content/wallpapers/medium/66/pixiewall-denia-wuthering-waves-bubbles-4k-7gud2a.jpg>
- 本地：`assets/bubbles.jpg`，实际下载尺寸 2560 × 1440。未将网页标题中的 4K 当作文件实际分辨率。
- 本次来源为壁纸聚合站，尚未核对首发出处。

## 重现与检查

在仓库根目录执行：

```powershell
.\nonebot\.venv\Scripts\python.exe docs/research/statuscard-background-preview/render_cdp.py
```

该脚本生成 HTML，启动独立的无头 Chromium 配置目录，用 CDP 指定实际视口尺寸，等待素材及字体加载后截图。只访问本地预览文件。

`render-metrics.json` 记录四张单图、对照图、素材图的检查：素材成功加载、实际视口尺寸正确、内容在页脚上方、页脚位于卡片内。PNG 已逐版/对照目视检查，首轮命令行截图的底部裁切已修正。
