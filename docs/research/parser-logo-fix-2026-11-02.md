# 解析卡片平台徽标位置回归

2026-11-02 核实并修复，已同步至 `/opt/qqbot/nonebot/media_parser/core/render.py`。

## 原因与修复

旧 Pillow 模板在 `_draw_header` 中以 `card_width - padding - logo_width` 计算徽标的横坐标，放在右侧。迁移后的 HTML 把 `.platform` 放在作者信息之前，内部元素默认左对齐，导致所有共用模板的平台徽标移到了左上。

浏览器复现使用真实渲染镜像：800 像素卡片的徽标右边缘为 x=56，而内容区域右边缘为 x=772。10 个平台和 4 个边界样本全部失败。该问题有明确的旧版定位代码与实际坐标证据，因此无需额外日志或多轮假设排查。

修复将头部划为作者区和右侧平台区：作者区允许长昵称换行；平台徽标位于右上角最右端。无作者、无徽标和嵌套转发均按各自头部边界排版。保持同机 Playwright 容器截图，仅调整共享模板的 HTML/CSS。

## 验证

- 真实浏览器布局检查 14/14 通过：B 站、小红书、抖音、快手、Pixiv、TikTok、Twitter、微博、YouTube、JMComic，以及无作者、长昵称、嵌套转发、缺少徽标素材。
- 断言检查徽标靠右、平台区靠上、作者靠左、两个区域不重叠、文字不溢出。
- 解析逻辑、配置、运行链路、小红书素材、共享渲染客户端的定向测试：32 passed。
- 正式 HTTP 渲染接口生成修复前后对照图片，并人工检查 B 站、小红书及转发样张。
- 上线后正式 `Renderer.render_cards` 再次生成 B 站、小红书样张，均为一张 800×262 PNG；机器人 `ready=true`、`connected=true`，渲染器就绪。未发送 QQ 测试消息。

样张：`parser-logo-preview/bilibili.png`、`parser-logo-preview/xhs.png`。

可复用浏览器检查脚本：`tools/diagnostics/check_parser_layout.js`。本次固定样本、生成脚本、失败/通过日志和上线结果位于 `.tmp/parser-logo-20261102/`；它对同一组由真实模板生成的 HTML 执行检查。正式服务器同镜像的隔离容器使用 `--rm --network none` 运行验收，退出后自动清理。

## 上线范围与回退

仅替换解析卡片模板文件，替换前校验 SHA-256，未覆盖其他工作区改动。

- 原文件 SHA-256：`86eb3b95c0b6d38dccf1bc3452e25ac6f026628f6f9f53464867d133abd97e4b`
- 修复文件 SHA-256：`d5904b202e830f2031ba60e857caea9f69157bbc8e966e14597af264d34db160`
- 服务器备份：`/opt/qqbot-backups/parser-logo-20261102`
- 回退：`/opt/qqbot/.venv/bin/python /opt/qqbot-backups/parser-logo-20261102/rollback.py rollback`

## 同日追加：只显示放大的徽标

按用户确认的位置继续调整，移除有徽标时旁边重复的平台名称；徽标按原始比例显示，高度 40 px、最大宽度 160 px，右上角对齐。直接内嵌原始 PNG，保留透明背景。缺少徽标资源时保留平台名称作为兜底。

14 个真实浏览器布局样本全部通过，包含无可见重复名称、放大尺寸、PNG 资源、长昵称及转发卡片检查；相关 Python 测试 23 passed。已上线，机器人重新连接，正式渲染样张保存于 `parser-logo-only-preview/bilibili.png` 和 `parser-logo-only-preview/xhs.png`。

- 当前模板 SHA-256：`c6d40125535180f487f7637d8806003658e2f7cc992f175e17e54c3835b58c17`
- 此次备份：`/opt/qqbot-backups/parser-logo-only-20261102`
- 回退到“徽标已靠右、仍带平台名称”的版本：`/opt/qqbot/.venv/bin/python /opt/qqbot-backups/parser-logo-only-20261102/rollback.py rollback`
- 验证记录：`.tmp/parser-logo-only-20261102/`。
