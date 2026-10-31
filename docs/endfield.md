# 终末地插件（endfield）

基于 `Entropy-Increase-Team/astrbot_plugin_endfield` v3.0.3（AGPL）移植的
NoneBot2 / MuiceBot 插件。协议终端：`https://end-api.shallow.ink`。

## 命令

| 命令 | 场景 | 说明 |
|---|---|---|
| `授权登陆` | 私聊 | 首次发 consent；网页授权后再次发送完成绑定 |
| `扫码绑定` | 私聊 | 二期占位 |
| `便签` / `理智` / `签到` | 群 @ / 私聊 | 查询主体=发送者；签到为手动 |
| `绑定列表` / `切换绑定 N` / `删除绑定 N` | 私聊 | 每 QQ 最多 3 个，primary |
| `抽卡记录 [页]` / `抽卡分析` | 群 @ / 私聊 | 20/页；图卡优先，失败回退文本 |
| `干员列表 [页]` / `干员面板 <名字>` | 群 @ / 私聊 | 需要先 `同步面板` |
| `危机合约 [名字]` / `日历` | 群 @ / 私聊 | 日历公开 |
| `成就列表` / `帝江号建设` / `地区建设` / `全服统计` | 群 @ / 私聊 | 全服统计公开 |
| `公告` / `公告最新` | 群 @ / 私聊 | 公开 |
| `同步抽卡` / `同步面板` | 私聊 | 1 次/天/QQ |
| `订阅公告` / `取消订阅公告` | 群 @ | 仅 superusers，在目标群 |
| `订阅理智` / `订阅调度券` / `订阅签到` + 取消 | 私聊 | 需先绑定 |
| `订阅列表` | 私聊 | 只显示自己的状态 |

规则：群聊必须 @ 机器人；@ 只作触发，查询主体永远是发送者；绑定/同步/个人订阅仅私聊；
5 秒防抖；查询 100/天/QQ、绑定 3/天、切换 10/天、删除 20/天、同步 1/天；主人豁免。

## 配置与 Key

- 非敏感配置：`nonebot/data/endfield.json`；面板可写，bot 按 mtime 热加载。
- API Key：`nonebot/data/endfield_secrets.json`（0600）；面板写，`ENDFIELD_API_KEY` 兜底。
- 绑定/订阅/心跳/审计：`nonebot/data/endfield/`（0600；bot 独占写，面板只读）。
- 面板：打开 `/endfield`（沿用主面板登录）；可写 Key/配置，绑定/订阅只读。
- 绑定文件含明文 `framework_token`；不要提交 git，备份注意权限。

## 渲染器

远程渲染器是 `docker/playwright-renderer/`，不是本插件的一部分：

- 机器人发 HTML → `POST {render_url}/render` → PNG；失败回退文本。
- 默认 `render_url=http://127.0.0.1:3000`（Docker compose）。
- 开发服务器 `10.0.0.226` 的 **3000 是 personal-wiki**；宿主机模式 renderer 用
  `HOST=127.0.0.1 PORT=3100`，bot 配 `render_url=http://127.0.0.1:3100`。
- 3100 仅回环，外网/CI 不能直连；live 测试在服务器本机跑，或
  `ssh -L 3100:127.0.0.1:3100`。
- 限制：HTML ≤2 MB、宽 320–1600、高 200–2400、超时 1–60 s、并发 2（满 429）。

## 运行

```bash
cd nonebot
.venv/Scripts/python.exe -m pytest tests/endfield -q
```

- 默认全离线：MockTransport / 假时钟 / 假推送；不需要真实 Key。
- 真实只读冒烟需 `ENDFIELD_API_KEY` 或面板 Key；不要提交测试输出。
- 调度器随 bot 启动，60s tick；心跳 `data/endfield/heartbeat.json` 超过 180s 视为离线。
- 审计 `data/endfield/audit.jsonl`（1 MB 轮转），面板读尾部 50 条。

## 许可

- `endfield/client.py`、`store.py`、`render.py`：上游同源移植，**AGPL-3.0-only**。
- `config.py`、`logic.py`、`cards.py`、`scheduler.py`、`plugins/endfield.py`、
  `endfield_panel.py`：MIT。
- 根 `LICENSE-AGPL-3.0` + `docs/third-party/endfield-NOTICE.md`。
- 官方 NoneBot 插件只作参考，未复制其代码；其许可证冲突待澄清。
