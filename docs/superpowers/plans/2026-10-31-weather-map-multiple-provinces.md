# 气象地图与多省过滤实施记录

> **For agentic workers:** 使用 superpowers:executing-plans 在当前会话实施；最后做独立检查。

**Goal:** 将用户已确认的雷电/大风地图卡片用于实际渲染，让管理面板的本地过滤可填写多个省份。

**Architecture:** 保留现有 province 字符串配置，规范化分隔符并按任意关键词匹配，城市仍为附加约束。地图独立模块按预警行政区代码获取 DataV 边界，缓存后转换为内联 SVG；没有边界时显示缺省提示。气象采用已确认的浅色卡片布局，其他灾害模板保持现有接口。

**Tech Stack:** Python/httpx、HTML/CSS/SVG、pytest、现有 Playwright HTTP 服务。

**Spec:** 本对话已确认的两张 `preview-*-map.png`；用户“可以 就这样”及“本地过滤添加多个省份”。以下为实施时的小型设计记录。

## 设计与约束

- 采用多行输入框，支持中英文逗号、顿号、分号及换行；去空、去重；旧的单省配置原样可用。
- 省份任一匹配，城市仍需匹配；省份为空表示不限省份；继续兼容预警地区字段缺失时的既有处理。
- 沿用 province 字段而不引入平行数组字段，避免旧配置、旧接口与新面板出现两个配置源。
- 地图只高亮预警命名的行政区域，显示“预警行政区域示意”，不生成真实气象影响多边形。
- 行政区编码来自明确 adcode 或规范预警 ID；只从固定公开 DataV 地址请求数值编码边界。
- 按边界数据的 parent 属性选上级地图，不用行政码截断猜测直辖市/省直辖县归属。
- 使用本地磁盘缓存；首次网络失败不阻断预警渲染；地图标签与正文均转义。
- 实际预警保留原文与时间，不把历史案例提示带进正式模板。长正文在图片可用空间内显示并提示详见随附全文。
- 开发服务器已有其他工作树修改，只同步本次文件；dashboard.html 从服务器当前文件打局部补丁。保留配置及通知状态。

## Review Focus

- 多省关键词不能被当成一个字符串；过滤阈值及城市限制仍有效。
- 旧单省配置、空值、中文分隔符、重复省份、非法类型均有明确行为。
- 无效行政码、边界缺失、网络超时、损坏缓存不伪造行政范围或中断推送。
- 直辖市和省直辖县遵循实际父级；Hainan 局部地图明确“海南岛”。
- 长名称、长正文及异常字段不造成 HTML 注入或图片越界。

## Task 1：多省配置及管理面板

Files: `nonebot/disaster_config.py`, `nonebot/disaster_china_logic.py`, `nonebot/dashboard.html`, 对应 tests。

- [x] 添加多省 OR、城市 AND、旧值兼容及配置保存测试，运行并确认新增断言失败。
- [x] 规范化 province 字符串（最多 600 字符、每项最多 40 字符），匹配时拆分关键词；面板改多行输入及用法提示。
- [x] 运行配置/逻辑/面板测试，预期全部通过；真实浏览器验证读取和提交值。

## Task 2：正式地图卡片

Files: `nonebot/disaster_weather_map.py`（新）、`nonebot/disaster_weather_card.py`（新，独立模板职责）、`nonebot/disaster_render.py`, `nonebot/tests/test_disaster_weather_map.py`（新）、渲染测试。

- [x] 添加地图选区、缓存、缺失回退、渲染 HTML 内容及长正文行为测试，确认失败。
- [x] 实现 `map_for_alert(alert, *, cache_dir=None, client=None)` 和地图对象的 SVG 输出；实现内联图标与确认的气象卡片布局。
- [x] 运行相关测试，并用现有渲染服务生成原有两个历史案例和长文本边界案例，检查图片。

## Task 3：部署及验证

- [x] 运行 `python -m pytest tests -q` 并记录实际结果；独立检查本次改动。
- [x] 校验服务器原文件 SHA，备份、局部安装并重启 qqbot/qqbot-dashboard。
- [x] 验证服务状态、鉴权接口、面板多省表单及实际 PNG；不发送额外 QQ 消息。
- [x] 记录部署结果、备份位置、测试数量与剩余限制。

## 执行证据

当前工作区包含先前未提交功能，使用本次文件快照及服务器 SHA 校验界定改动，不覆盖其他改动。

- Task 1：complete，新增多省规范化失败测试后实现；配置、逻辑、面板 35 passed。
- Task 2：complete，地图与卡片 58 项相关测试通过；正式服务生成两张历史样例及合成长正文排版样例，实际 Chromium 检查通过。
- Final review：独立 reviewer 发现两项 Important；补充失败回归后修复随附文字截断和异常嵌套地图属性。最终全量 564 passed、1 skipped、30 subtests passed。
- Final minor（deferred）：极慢网络下过期缓存刷新可能耗尽 5 秒总预算而暂时无地图；已有无地图回退，详情记入部署文档。
- Task 3：complete，服务器隔离 50 passed、24 subtests passed；6 个文件 SHA 通过；三个服务 active；机器人 ready/connected；真实面板输入及移动端布局通过。
- 部署备份 `/opt/qqbot/.deploy-backup-weather-map-20261031-164111`。全部证据见 `docs/research/weather-map-multiple-provinces-deployment.md`。
