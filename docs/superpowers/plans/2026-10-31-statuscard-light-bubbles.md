# Status Card Light Bubbles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将用户选定的 D 浅色泡泡设计接入本地状态卡。

**Architecture:** 保持 Pillow 渲染接口和数据 view，背景使用模块相对路径的随应用素材。以 1170 px 排版，输出时整体缩放；共享基础配色同步为浅色。

**Tech Stack:** Python、Pillow、pytest。

**Spec:** `docs/superpowers/specs/2026-10-31-statuscard-light-bubbles-design.md`

## Global Constraints

- 本地渲染，无运行时联网；图片故障不影响数据出图。
- `render_status_card(view, *, width=1170) -> bytes` 不变，宽度 760–2880 px。
- 保留原有数据与脱敏语义，精简模式隐藏系统信息且最多 5 个进程。
- 当前状态卡源码属于已有未提交内容，只修改本任务相关文件，不提交整个工作区。

## Review Focus

- 模块从不同工作目录加载时仍能找到图片。
- 图片不存在或损坏时可正常出图。
- 较长昵称、协议、系统值不能挤入人物或相邻字段。
- 760 px 与 2880 px 输出比例一致，精简版确实更短。
- 进程与系统数量增加时动态计算面板和页脚高度。

## Task 1: 本地浅色卡片与素材

**Files:** `nonebot/status_image.py`、`nonebot/resources/statuscard/denia-bubbles.jpg`、素材来源说明、`nonebot/tests/test_status_image.py`。

**Interfaces:** 消费现有 view；输出 PNG bytes；背景加载错误在渲染器内部退为纯色底。

- [x] 增加背景加载/故障/工作目录行为测试，运行并确认未实现时失败。
- [x] 拷贝选定素材并记录来源；调整配色、顶部背景、资源面板、并列信息布局和整体缩放。
- [x] 运行 `python -m pytest nonebot/tests/test_status_image.py -q`：14 通过。
- [x] 运行状态卡相关测试：112 通过；导出默认、精简、窄版、长文本告警版与日报样张并目视检查。
- [x] 独立审查与设计一致性检查通过，无待修问题；所有产物保留在工作区供查看。

最终回归运行于正常进程环境（沙箱内异步集成用例曾停滞），112 passed，1 条测试初始化未注册 adapter 的警告。未提交、未远程部署。
