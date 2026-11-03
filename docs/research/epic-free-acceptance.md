# Epic 喜加一 V1 实跑验收记录

日期：2026-10-31（Asia/Shanghai）
状态：**自动化验收通过**；真实 QQ 群内投递仍待人工目检（见 §5）。
范围：Epic 喜加一 V1（真实接口、卡片、换期推送、失败回退、面板增删）。
关联：地图「Epic 喜加一提醒：研判与移植」、票「研判文档定稿、AGPL 署名与实跑验收」。

## 1. 结论摘要

- 本机 Epic 定向用例 **55 passed / 0 failed**；全量套件 **564 passed / 1 skipped**（78 秒）。
- 真实接口抓取成功：当前免费 1 条《Sorry We’re Closed》，即将免费 2 条。
- 卡片成功生成：760×686、95 062 B，中文/《》/’/¥ 正常、无方框。
- 模拟换期推送三轮 **0 / 2 / 0**：首装只记录、换期推一次、同一期不重推。
- 卡片渲染失败回退文本；接口失败时推送跳过且状态不变，命令回可读错误。
- 面板目标增、删、立即推送均 200；未登录 401、缺 CSRF 403。

## 2. 环境与版本

| 项 | 值 |
|---|---|
| 工作区 | `D:\Software\qqbot` |
| 分支 / HEAD | `codex/game-events` / `0a33293`（Epic 相关文件当时未提交） |
| Python | `nonebot/.venv`，Python 3.12 |
| 测试服务器 | `root@192.0.2.226:/opt/qqbot`，`qqbot` active，`Bot 3892894069 connected` |
| 面板 | `http://192.0.2.226:8092/`，含 Epic 区块 |
| 数据源 | `https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions` |
| 卡片样本 | [`epic-free-card-sample.png`](epic-free-card-sample.png)，SHA-256 `beafb3c62eb9688ec0f0ac507078b11908dc9c86c6b508d15830b9ad2f3d3fc5` |

## 3. 验收矩阵

| # | 验收项 | 操作 | 观察结果 | 判定 |
|---|---|---|---|---|
| 1 | 定向测试 | `python -m pytest tests -q -k epic_free` | `55 passed, 510 deselected` | 通过 |
| 2 | 全量回归 | `python -m pytest tests -q` | `564 passed, 1 skipped`（78 秒） | 通过 |
| 3 | 真实接口抓取 | `service.fetch_view(country='CN')` | 1 当前免费 + 2 即将免费；集合键 `4453dd31…` | 通过 |
| 4 | 喜加一 出图 | `epic_free_image.render(free, upcoming)` | 760×686、95 062 B；中文正常、无方框 | 通过 |
| 5 | 模拟换期推送 | 假 bot + 真实 `_push_round()` 三轮 | 第 1 轮 0 条；第 2 轮 2 条；第 3 轮 0 条 | 通过 |
| 6 | 卡片失败回退 | `render` 抛 `RuntimeError` | 返回文本，首行「【EPIC 喜加一】」、含「北京时间」；warning 一条 | 通过 |
| 7 | 接口失败 | `fetch_view` 抛 `EpicAPIError` | 推送 0 条、`last_key` 不变；命令回「Epic 查询失败，请稍后再试。」 | 通过 |
| 8 | 面板增删目标 | `/api/epic` add / add / delete / test | 全部 200；目标落盘；触发文件生成 | 通过 |

## 4. 逐项记录

### 4.1 测试

```text
# 定向
55 passed, 510 deselected, 1 warning in 15.46s

# 全量（含其它插件）
564 passed, 1 skipped, 30 subtests passed in 78.05s
```

范围：`test_epic_free_logic.py`、`test_epic_free_service.py`、`test_epic_free_image.py`、`test_epic_free_config.py`、`test_epic_free_plugin.py`、`test_epic_free_dashboard.py`。面板路由用例同时覆盖「加目标 → 删目标 → 落盘为空」。

### 4.2 真实接口（2026-10-31 16:42，CN / zh-CN）

```text
free 1 ['《Sorry We’re Closed》']
upcoming 2 ['《Idle Champions of the Forgotten Realms》', '浮游世界']
key 4453dd3109f0a377
  free: 《Sorry We’re Closed》 | ¥78.00 | 2026-11-06 00:00 |
        https://store.epicgames.com/zh-CN/p/sorry-were-closed-724c86
```

判定口径：遍历 `promotions.promotionalOffers[*].promotionalOffers[*]`，取活跃 0% 窗口；不依赖数组顺序；`productSlug` 为 `null` 时用 `offerMappings[0].pageSlug`。

### 4.3 卡片

- 文件：`docs/research/epic-free-card-sample.png`（760×686，95 062 B）。
- 内容：标题、简介、原价 ¥78.00 → 免费、截止北京时间、领取链接；「即将免费」2 条。
- 目检：中文、全角书名号、弯引号、¥ 均正常，无方框；长标题换行略挤，不影响可读性（已记入研判稿「已知限制」）。

### 4.4 模拟换期推送

配置：1 个群目标；`card_enabled=true`；`fetch_view` 依次返回「本期」「下一期」「下一期」。

```text
round 1: sent=0 last_key=4453dd3109f0   # 首装只记录
round 2: sent=2 last_key=3531649f0f68   # 换期：卡片 + 链接
round 3: sent=0 last_key=3531649f0f68   # 同一期不重推
calls:
  group 12345 <MessageSegment image>
  group 12345 【EPIC 领取链接】 ...
```

### 4.5 卡片失败回退

```text
[WARNING] _message: [epic] 卡片渲染失败，回退文本：模拟渲染失败
fallback type str
fallback first line 【EPIC 喜加一】
has 北京时间 True
```

### 4.6 接口失败

```text
[WARNING] _push_round: [epic] 抓取失败，本轮跳过：模拟 200 错误体
push on api error sends 0 state unchanged old-key
[WARNING] handle: [epic] 查询失败：模拟 200 错误体
command reply ('Epic 查询失败，请稍后再试。',)
```

### 4.7 面板增删目标

```text
add 200 [{'kind': 'group', 'id': '12345'}]
add2 200 [{'kind': 'group', 'id': '12345'}, {'kind': 'private', 'id': '67890'}]
remove 200 [{'kind': 'private', 'id': '67890'}]
trigger 200 True
```

面板鉴权沿用仓库规则：未登录 401、缺 CSRF 403；「立即推送」只写触发文件，由机器人进程消费。

## 5. 未覆盖与残余风险

1. **真实 QQ 群投递未自动化**：本轮验证到「卡片/文本已生成、假 bot 收到两条消息」；测试服务器已部署并连接，但仍需人工在测试群发一次 `喜加一` 目检。该项不阻塞本次自动化验收，作为上线后观察项。
2. **远程用例未在本轮重跑**：远程历史记录 47 绿，本轮本机 55 绿；两者差在新增的配置/面板/权限用例。
3. **换期为模拟**：用同一批游戏把 `id` 与窗口后移 7 天模拟下一期，不是真实跨周等待。
4. **面板用 TestClient**：未登录真实 `http://192.0.2.226:8092` 操作；鉴权、CSRF、路由与落盘逻辑相同。
5. **推送至多一次**：发送前先占位，失败不重试；若真实发送失败，需要面板「立即推送」人工补一次。

## 6. 复现

```bash
cd nonebot
python -m pytest tests -q -k epic_free
```

真实接口冒烟：

```bash
cd nonebot
python -c "import asyncio, epic_free_service as s; v=asyncio.run(s.fetch_view()); print(len(v['free']), [g['title'] for g in v['free']]); print(len(v['upcoming']), [g['title'] for g in v['upcoming']])"
```

换期/回退/面板的等价回归在 `tests/test_epic_free_plugin.py`、`tests/test_epic_free_dashboard.py`、`tests/test_epic_free_config.py`；一次性验收脚本不提交，避免把测试夹具混入产品代码。

## 7. 相关文档

- 研判与实施：[`epic-free-games-port.md`](epic-free-games-port.md)
- 部署与验证：[`../OPERATIONS.md`](../OPERATIONS.md) §8.9
- 踩坑：[`../PITFALLS.md`](../PITFALLS.md) §7
- 许可：[`../third-party/epicfree-NOTICE.md`](../third-party/epicfree-NOTICE.md)
