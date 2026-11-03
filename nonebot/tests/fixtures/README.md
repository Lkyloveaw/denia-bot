# Epic 免费游戏 fixture

- 来源：`https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions?locale=zh-CN&country={CN,US}&allowCountries={CN,US}`
- 抓取时间：2026-10-31 12:03（UTC+8）
- 用途：`tests/test_epic_free_logic.py` / `tests/test_epic_free_service.py` 的固定输入，测试不联网。
- `epic_free_cn_reversed.json` 把每条 `promotions` 组内的 offer 顺序整体反转，用来复现「只取首条会漏报」的问题；判定结果应与 `epic_free_cn.json` 完全一致。
- 测试固定 `now = 2026-10-31T03:14:00Z`（`datetime(2026, 10, 31, 3, 14, tzinfo=timezone.utc)`）；此刻 CN/US 都只有 `《Sorry We’re Closed》` 一条当前免费，另有 2 条即将免费。
