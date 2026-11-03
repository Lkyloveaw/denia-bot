# 四类灾害消息与卡片原型

> **已选 B Bulletin**：用户选择 **B**；文案 **首报中性官方、后续达妮娅**。
> 生产实现见 `nonebot/disaster_render.py`（B 卡）与 `nonebot/disaster_tone.py`；本文件保留 A/B/C 原型作为设计记录。
> 固定样例、无外部资源、无持久化，不并入生产代码。

## 问题

四类灾害在群聊/面板里应该长什么样？同一份固定数据下，比较三种**结构不同**的卡片：

| 变体 | 结构 | 适合 |
|---|---|---|
| **A Poster** | 全幅海报：大标题 + 大视觉 + 底部事实 | 群内快速扫读、高严重度/EEF 首报 |
| **B Bulletin** | 官方公告：左侧色轨 + 双栏字段 + 大色块/图 + 印章 | 气象/海啸正式通报、需要“官方感” |
| **C Dashboard** | 数据面板：状态条 + 指标瓦片 + 图 + 事件/指引 | 管理面板、历史列表、多字段密集场景 |

## 打开方式

直接双击 `docs/research/prototypes/disaster-cards.html`，或浏览器打开：

```text
disaster-cards.html?type=weather&variant=A
disaster-cards.html?type=typhoon&variant=B
disaster-cards.html?type=tsunami&variant=C
disaster-cards.html?type=earthquake&variant=A
```

- 顶部切换灾害类型；底部浮动栏或键盘 `←` / `→` 切换 A/B/C。
- URL 里的 `type` / `variant` 可分享、刷新稳定。
- 原型栏只在 HTML 里，不会进生产。

## 图片版

四类主图（Poster）：

| 类型 | 图片 | 体积 | 渲染 |
|---|---|---|---|
| 气象预警 | `images/weather-A.png` | 129,259 B | 540 ms |
| 台风 | `images/typhoon-A.png` | 63,988 B | 173 ms |
| 中国海啸 | `images/tsunami-A.png` | 59,560 B | 158 ms |
| 地震 | `images/earthquake-A.png` | 61,747 B | 184 ms |

结构对比（气象 B/C）：

| 变体 | 图片 | 体积 | 渲染 |
|---|---|---|---|
| Bulletin | `images/weather-B.png` | 169,361 B | 256 ms |
| Dashboard | `images/weather-C.png` | 106,697 B | 204 ms |

四类联系表：`images/contact-sheet.png`。

> 图片由生产 `docker/playwright-renderer/server.js` 的 `/render` 生成（900×1200，JS 关闭）；快照 HTML 用 body class 固定类型/变体。交互页本身用少量 JS 切换，固定样例、只读。

## 文字版：中性官方 vs 达妮娅

### 气象预警

**中性官方**

```text
【气象预警】江苏省徐州市铜山区发布强对流黄色预警
等级：黄色
地区：江苏省徐州市铜山区
生效：2026-07-29 12:41（北京时间）
正文：铜山区气象台 2026 年 07 月 29 日 12 时 41 分发布强对流黄色预警信号：预计今天午后到上半夜我区部分镇（街道）将出现雷电，并伴有短时强降水、局地 7–9 级雷暴大风等强对流天气。
来源：中国气象局 / Jian Project
仅供参考，以官方发布为准。
```

**达妮娅**

```text
达妮娅提醒：徐州铜山区挂起了强对流黄色预警。今天午后到上半夜可能有雷电、短时强降水和 7–9 级雷暴大风，出门记得带伞，离临时搭建物和广告牌远一点。信息来自中国气象局，具体以官方为准。
```

### 台风

**中性官方**

```text
【台风】2518 桦加沙（RAGASA）
等级：超强台风
中心：北纬 19.5° / 东经 119.9°
中心气压：920 hPa；最大风速：58 m/s
移动：西北 20 km/h
更新：2025-09-22 23:00（北京时间）
预计：2025-09-25 23:00 热带低压
来源：中国气象局 / EQSC
仅供参考，以官方发布为准。
```

**达妮娅**

```text
达妮娅看了下台风路径：桦加沙还在超强台风级，中心气压 920 hPa，最大风速 58 m/s，正以 20 km/h 往西北走。它后面会减弱，但路径还可能调整；沿海的朋友先别急着安排海上活动。信息来自中国气象局 / EQSC。
```

### 中国海啸

**中性官方**

```text
【海啸预警】海啸黄色警报
等级：黄色（第 2 报）
震中：日本本州东岸；发震：2026-07-29 12:00
参数：M7.8，深度 10 km
影响：浙江 / 上海 / 江苏南部沿海
预计到达：2026-07-29 18:30
来源：自然资源部海啸预警中心 / Jian Project
仅供参考，以官方发布为准。
```

**达妮娅**

```text
达妮娅提醒沿海的朋友：自然资源部海啸预警中心发布了海啸黄色警报，预计 18:30 前后到达浙江、上海、江苏南部沿海。今天先别去海边和港口，留意应急部门的疏散通知。信息以官方发布为准。
```

### 地震

**中性官方**

```text
【地震预警】四川成都 M6.1
来源：中国地震台网 / Wolfx
报次：第 3 报 · 最终报
发震：2026-10-31 12:00:00（北京时间）
深度：12 km；最大烈度：6.0；距本地：约 120 km
仅供参考，以官方发布为准。
```

**达妮娅**

```text
达妮娅感觉到了一下？中国地震台网测定四川成都附近 M6.1，深度 12 km，最大烈度 6.0，距你约 120 km。先就近避险，远离玻璃和吊灯，关注余震和官方通报。信息来自中国地震台网 / Wolfx。
```

## 必填 / 可缺省字段

| 类型 | 必填（否则不出卡/不出推送） | 可缺省（显示为“—/未知”或省略） |
|---|---|---|
| 气象预警 | `title` 或 `headline`；`description` 推荐；`id`（去重；缺失时用标题+时间回退） | 颜色/类型（可从标题提取）、`province/city/district`、经纬度、`effective`、`adcode` |
| 台风 | `id`；至少一个有效 `historyTrack` 节点（含时间） | 名称（回退“编号 号台风”）、`typeNameCN`、经纬度、气压、风速、移动方向/速度、`futureTrack`、`windCircle` |
| 中国海啸 | `title` 或 `level`；`id` 推荐（缺失用编号+标题+震中+时间回退） | `originTime`、`number`、震级/深度/经纬度、`place`、`orgUnit`、`description`、`htmlUrl`、影响区/官方图件 |
| 地震 | `source`、`event_id`、`occurred_at`、`place`、`magnitude` | 深度、最大烈度/震度、距本地、报告号、地图/列表 |

- 卡片在字段缺失时用 `—` 占位或整行省略，不阻塞推送。
- 文字版始终可用；图片版失败时回退文字（与生产 `disaster_render` 的行为一致）。

## 渲染指标与回退

- 环境：本机 Playwright 1.49 + Chromium 131，900×1200 PNG，生产 `server.js`、JS 关闭。
- 热态渲染 158–540 ms；冷启动第一张约 1.1 s。体积 57–169 KB（气象 B 最大）。
- 渲染失败（服务离线/超时/非图片）：生产 `render_event` 抛 `RenderError`，插件捕获后发纯文字；已有 `test_disaster_render` 覆盖。
- 图片过大/发送失败时不重发，避免重复；文字版是兜底。

## 结论（已选）

- **结构：B Bulletin**。
- **文案：首报中性官方、后续达妮娅**；图片与文字同口吻；首报判定见 `docs/research/disaster-v1-ux-spec.md`。
- 生产 B 卡样例：
  - `images/production-B-weather.png`
  - `images/production-B-typhoon.png`
  - `images/production-B-tsunami.png`
  - `images/production-B-earthquake-character.png`
  - 联系表：`images/production-B-contact.png`
- A/C 保留为备选：A 适合群聊海报，C 适合面板/历史；当前不接生产。

## 建议（历史记录）

- **群内推送**：优先 **A Poster**；严重度高/EEW 首报用中性官方文案，避免人格化延迟阅读。
- **气象/海啸正式通报**：**B Bulletin** 更接近官方公告；色块大，但字段密度低于 C。
- **面板/历史列表**：**C Dashboard**；指标瓦片和防御指引适合多字段，但群聊里信息层级不如 A 直接。
- 可选混合：**A 做群聊图 + 中性首报**，**B 做气象/海啸正式图**，**C 做面板**；文字统一中性，达妮娅只用于非紧急的“提醒/解读”。

## 文件

| 文件 | 说明 |
|---|---|
| `disaster-cards.html` | 单文件交互原型（四类 × 三变体，无外部资源） |
| `build_cards.py` | 固定样例 + 生成 HTML（prototype helper） |
| `render_snapshots.py` | 调用 `/render` 生成 PNG/联系表/指标 |
| `render-metrics.json` | 本轮渲染耗时/体积 |
| `images/` | 4 张主图 + 2 张结构对比 + 联系表 |

## 限制

- 固定样例，不依赖实时源；不是生产实现，不写 `nonebot/`。
- 地图/海啸图件是内联 SVG 占位；生产接瓦片/官方图件后需重新评估体积与耗时。
- 达妮娅文案是候选，不是人格定稿；由《V1 用户可见规格》决定。
- 本机渲染用 Windows 字体；Docker 的 `fonts-noto-cjk` 可能有细微字宽差异，部署后需复核。
