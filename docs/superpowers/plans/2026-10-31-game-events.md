# 游戏活动日历 Implementation Plan

> **For agentic workers:** Use the test-driven-development workflow; task steps use checkbox tracking. Implementation is authorized by the user's design approval. Independent source and message adapters may be delegated using dispatching-parallel-agents once their interfaces are fixed.

**Goal:** 在 NoneBot2 接入十个游戏的活动日历，带面板管理和与解析器一致的群/私聊黑白名单约束。

**Architecture:** 纯字典活动协议、纯规则、SQLite store、异步源/发送适配和串行服务；面板写配置与任务，机器人负责网络和发送。

**Tech Stack:** Python >=3.10, SQLite, httpx, Pillow, NoneBot2/OneBot v11, FastAPI, existing vanilla HTML/JS.

**Spec:** `docs/superpowers/specs/2026-10-31-game-event-due-design.md`（含已批准的会话限制补充）

## Global Constraints

- 原创实现，不拷贝上游代码；不发送真实测试消息，不部署/重启生产。
- 保留已有未提交变更；工作分支 `codex/game-events`；不自动提交混合工作树。
- 数据放 nonebot/data/game_events.sqlite3 与 nonebot/data/game_events/；测试一律临时路径。
- 30分钟轮询；7/3/1天；紧急默认关闭、120分钟、每日每人3条、UTC+8 23–8静默。
- 黑名单优先，白名单为空不限制，群/私聊独立总开关；读命令和每次发送均检查。
- 使用现有 `.venv/Scripts/python.exe -m pytest`；测试不联网；有真实网络验证时只读。

## Review Focus

1. 名单在生成计划后收紧：发送前重新检查，紧急origin群同步暂停。
2. 订阅概览部分成功后重启：只重试未成功段，最终完整快照建基线。
3. 面板与bot同时变更：revision冲突与逐行状态更新，不能恢复已退订代次。
4. 源304/坏JSON/部分分页：不清空成功快照，失败数据不能当正常无活动。
5. 超时回执未知：不立即回退重发，失败不记成功，目标日限合计计数。

## Shared Interfaces

活动为JSON兼容dict：`uid, game_id, name, start`（epoch秒或null）, `end`（epoch秒）, `description, cover, url, tags`（字符串列表）, `extra_times`（`[{label,at}]`）。时间内部UTC秒，显示UTC+8。

`game_event_logic`: `GAMES` 游戏字典；`parse_time(value)->float|None`；`event_record(game_id,name,start,end,*,identity='',description='',cover='',url='',tags=(),extra_times=())->dict|None`；`resolve_games(tokens)->list[str]`；`allowed(settings,kind,target_id)->bool`；`parse_command(text)->tuple[action,str]|None`；`plans(events,states,policy,now)->list[dict]`，计划为 `event,reasons`（new/d7/d3/d1）,`tier`（天数或null）；`format_event(event,now,description=False)->str`。

`game_event_sources.SourceClient(cache_dir,client=None,clock=time.time)`：`await fetch(game_id,force=False,include_gacha=True)->dict`，返回 `game_id,source,activities,version,checked_at,fetched_at,stale,error,complete`；失败有缓存可返回stale，否则抛 `SourceError`；`await close()`。

`game_event_message.MessageBuilder(cache_dir,client=None,clock=time.time)`：`await build(events,*,title,mode='image',settings=None)->dict`，返回 `parts`（OneBot标准字典消息段）, `fallback`（纯文本含链接）, `nodes`（每活动一个parts列表）；调用端负责分批最多8项，builder共享5MiB预算。`await close()`；`safe_url(url)->bool`只做纯字符串语法，下载端校验DNS/重定向。

store/service/plugin/dashboard 的精确方法由根代理在实现任务3之前写入本计划补充，不强迫适配器了解数据库结构。

## Task 1: 纯规则与访问策略

Files: game_event_logic.py, tests/test_game_event_logic.py。

- [x] 先写行为测试：黑白名单优先、群/私独立禁用、别名、边界时间、剩5小时只1天档、过滤和误捕日常聊天。
- [x] 运行测试确认新增模块缺失，再实现共享接口；阈值只接受1–365整数，最多10档。
- [x] 相关测试绿色，记录结果；向独立适配器传递已固定活动协议。

## Task 2: 三源与安全消息（独立文件，可并行）

Files: game_event_sources.py + tests/test_game_event_sources.py；game_event_message.py + tests/test_game_event_message.py。

- [x] 各自先写HTTP mock/内存图片fixture行为测试并观察失败。
- [x] 实现三源协议、304实体缓存、AKE多区间/完整版本、PRTS限速完整分页；源错误不清空缓存。
- [x] 实现文本、字节图片、安全流式下载/像素上限、共享预算、节点；不执行OneBot发送。
- [x] 各自定向测试通过，自审并返回报告，由根代理整合核验。

## Task 3: SQLite与服务

Files: game_event_store.py, game_event_service.py, tests/test_game_event_store.py, tests/test_game_event_service.py。

- [x] 测试事务revision冲突、会话按game代次、初始化重试、失败不提交、名单热变更拦截、紧急日限/确认。
- [x] store负责唯一键/事务/设置/任务/源快照/状态，服务注入 source、sender、clock；网络调用在事务外。
- [x] 同一服务互斥轮询与初始化；每轮每源失败隔离，普通与紧急发送前复查访问策略。
- [x] 通过相关测试，记录可恢复初始化和崩溃边界。

## Task 4: 命令与后台生命周期

Files: plugins/game_events.py, tests/test_game_event_plugin.py, gate_logic.py, tests/test_help_coverage.py。

- [x] 测试读取vs修改权限、禁用群静默且不调LLM、英文短别名边界、bot路由、动态超管、帮助覆盖。
- [x] 接入PluginMetadata、on_message、on_startup/on_shutdown、实例锁、普通/图片/转发回退。
- [x] 有限超时，超时不当成明确拒绝；不主动选择任意在线账号。
- [x] 通过接线和help测试。

## Task 5: 面板管理项

Files: dashboard.py, dashboard.html, game_event_panel.py（允许提取API组装）, tests/test_game_event_panel.py。

- [x] 先用TestClient测试未登录/CSRF、输入、revision冲突、黑白名单、订阅、预览与任务排队。
- [x] 新增游戏活动导航与区域：群/私开关和黑白名单、全局、会话、源状态、预览、刷新与紧急目标。
- [x] 复用认证与样式；外部文本用textContent；不在面板发送或拉取远程。
- [x] 测试绿色，检查JS语法与页面内容，不修改其他面板区域。

## Task 6: 集成、审阅和交付

- [x] README/OPERATIONS补充命令、名单示例、数据/备份、上线与现有源验证限制。
- [x] 运行所有新测试、受影响测试、全套测试；所有基线失败单列。
- [x] 独立审阅访问策略、并发、来源完整性和投递；修复重要问题并回归。
- [x] 记录实施完成程度/实测限制；不部署、不发真实消息、不自动提交其他改动。

## Progress / Rulings

- 2026-10-31: 用户已批准实施。群与私聊均采用解析器同款黑白名单，另加各自总开关。
- Ruling: 直接保留当前工作目录并创建功能分支，因为当前面板及插件依赖大量未提交更改，干净worktree不包含这些；不覆盖/暂存其他工作。
- Preflight: Task1活动dict提供Task2/3；Task3 store提供Task4/5；Task2不修改Task1文件；Task4/5分别拥有插件与面板，无并行同文件写入。接口在任务交接时记录。
- 基线回归：373 passed, 1 skipped, 18 subtests passed。Windows 沙箱 asyncio Proactor socketpair 会挂起；离线 pytest 使用 require_escalated 与任务专属 `.tmp/` basetemp 后正常运行。
- Task1/3/4：规则7、服务11、插件11，合计29项定向通过；帮助覆盖另已通过。Store20与Message37项通过后进入独立review修复轮。
- Task2：源真实协议校验及31项离线用例通过，正在补5分钟查询缓存/负缓存；10入口开发机HTTP只读成功，详见研判报告第9节；没有QQ发送。
- Task5：管理面板独立表单及API由专属代理实施中；不在面板进程请求上游或发送消息。
- Review 1：`.tmp/game-event-adapter-review.md` 记录身份迁移继承状态、长URL降级、空快照诊断三项发现，已交单个修复代理；额外补封面宽度与来源展示。下一轮覆盖核心与面板边界。
- Ruling: 订阅目标身份创建后不可变，改目标需删除再新建，避免继承旧收件人的通知历史；代价是面板迁移目标多一步。
- Ruling: 图片参数范围限定总计1–5MiB、宽64–720px，与已批准上限一致；两条超长URL无法同装时保留首选完整详情链接，完整原记录仍可面板预览。
- 初始化期间任何相关设置变更会重置快照并更换generation，已ready的游戏保留通知历史。发送前复查全局revision、当前订阅revision/generation、访问策略、源开关、快照新鲜度及活动结束时间；紧急再复查静默/额度/本人确认。
- Task2交接：Source36；Store/Message修复后67项通过，补封面宽度、来源文本、完整链接预算。核心/源整合132项通过后，新增2项缺开始时间延期去重回归通过。
- Task5交接：新面板18项+旧dashboard8项通过；浏览器用只读临时假数据核验独立入口、黑白名单表单、订阅编辑身份禁用及缓存预览；不使用真实配置/目标。
- Review 2：缺开始时间活动延期改变UID，已移除deadline身份成分；有/无sourceID两个回归先红后绿。
- Review 3：不同mode/bot/games的任务不再默认为重复任务，而返回409，面板展示返回任务实际范围。新增TestClient回归先202红后409绿，相关48项通过；最终窄范围复审无剩余可执行发现，报告`.tmp/game-event-final-review.md`。
- 全套第一轮531通过/1跳过/1失败：旧Epic测试假设FastAPI路由全有.path，新增include_router惰性路由触发失败；改为TestClient核验实际接口鉴权响应，相关Epic测试通过。不是生产路由缺失。
- Ruling: 保留所有代码在当前功能分支工作树，不自动提交/合并混合的现有用户改动；无生产部署/重启或真实QQ测试消息。
- **最终验证（2026-10-31）：533 passed, 1 skipped, 1 warning, 18 subtests passed，90.98秒。** 命令为 `nonebot/.venv/Scripts/python.exe -m pytest tests -q --disable-warnings --tb=short --basetemp=D:/Software/qqbot/.tmp/game-final-suite`（工作目录nonebot，离线，Windows socketpair需升级执行）。警告为既有NoneBot测试引导的No adapters found，基线亦有。
- Python语法检查14个新增模块/测试通过；全量dashboard脚本`node --check`通过；本任务相关已跟踪文件`git diff --check`通过。全仓whitespace检查报告原有`plugins/memory.py` CRLF尾随告警，未改动该文件。
- 完整实现与审阅结束；临时只读预览页面/本地服务已关闭。部署网络、实际QQ私聊/群聊图片与合并转发投递仍属于上线联调，未执行。不宣称真实投递验收通过。
