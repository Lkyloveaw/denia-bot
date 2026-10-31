# 运维手册

面向 Linux 部署。Windows 单机部署的历史记录已归档在最后「附录」里，仅作参考。

## 1. 服务清单

| 服务 | 端口 | 进程/容器 | 配置 |
|---|---|---|---|
| SnowLuma（QQ 协议端） | 5099 WebUI / 6081 noVNC | Docker `qqbot-snowluma` | compose 环境变量 + `/opt/qqbot/snowluma/config` |
| NoneBot2 + MuiceBot | 8080 | `python bot.py` | `nonebot/.env`、`nonebot/configs/models.yml` |
| RAG 检索服务 | 8091 | `python rag/serve.py` | 环境变量 `KB_GATE_MODE` 等；见 `rag/core.py` |
| 管理面板 | 8092 | `uvicorn dashboard:app` | `nonebot/data/dashboard-auth.json` |
| 模型网关 AxonHub（可选） | 8090 | 独立二进制 | `axonhub/config.yml` |

四个业务服务**只监听回环地址**（面板按需绑内网 IP），外面访问一律走 SSH 隧道或反代 + HTTPS。

## 2. 启动顺序

重启机器后：

1. `docker compose up -d`（SnowLuma，需要先有登录态；否则去 6081 扫码）
2. RAG：`.venv/bin/python rag/serve.py`
3. NoneBot：`cd nonebot && ../.venv/bin/python bot.py`
4. 面板：`systemctl start qqbot-dashboard`（见 `nonebot/qqbot-dashboard.service`）

SnowLuma 的反向 WS 必须指向 `ws://127.0.0.1:8080/onebot/v11/ws`，且 `accessToken` 与 `.env` 的
`ONEBOT_ACCESS_TOKEN` 一致；握手还需要 `X-Self-ID` 头（NoneBot 靠它认 Bot，缺了会 403）。

## 3. 账号隔离

SnowLuma 是**进程级注入**，同一 QQ 客户端里登录的所有号都会被 hook。

- 生产 compose 里 `SNOWLUMA_HOOK_AUTOLOAD: "0"`，只恢复之前成功接入过的账号，不再自动钩别的号
- 改 `hookAutoLoad` 只能在配置文件里改（WebUI 没有这个开关），改完重启
- SnowLuma **不支持按 UIN 过滤注入**，粒度只有 PID

## 4. 日志

| 内容 | 位置 |
|---|---|
| NoneBot 主日志（UTF-8 文件 sink） | `nonebot/logs/<日期>.log` |
| RAG | `rag/logs/` |
| 协议端 | `docker compose logs -f snowluma` |
| 面板 | `journalctl -u qqbot-dashboard -f` |

**优先看 loguru 自己的文件 sink**：重定向到控制台/文件的那份在非 UTF-8 环境下会因表情符号编码失败而丢行。

## 5. 修改人格

人格 = `nonebot/templates/<Name>.jinja2` + `nonebot/configs/templates.yml` 里的变量。

切人格只需三处：

- `configs/templates.yml` 的 `ai_nickname` / `master_nickname` / `userinfos`
- `configs/models.yml` 里每条模型的 `template: <Name>`
- `.env` 的 `DEFAULT_TEMPLATE` 和 `MUICE_NICKNAMES`

写人格的三个要点：

1. **写行为，不写形容词** —— ❌「温柔体贴」→ ✅「对方情绪低落时先沉默一拍再开口，不急着给建议」
2. **贴 2~3 组真实对话示例** —— 模型模仿示例的句长与用词，比抽象描述有效得多
3. **边界写死** —— 比如「永远不承认自己是 AI」不写，模型被追问时很容易跳戏

## 6. 换模型 / 加渠道

- 走网关：在 AxonHub 面板里加渠道，`models.yml` 的 `relay` 条目 `model_name` 改成真实模型名即可（保存即生效，不用重启）
- 直连：在 `models.yml` 加一条 `provider: openai` 的条目，把它的 `default` 改成 `true`
- 逃生通道：把 `echo-test`（`provider: _echo`）的 `default` 改成 `true`，机器人会回显请求内容 —— 用来确认链路本身是活的
- **热重载例外**：`models.yml` 热重载，但插件开关（`.env` 里的 `PROMPT_GUARD` 等）改完必须重启

## 6.5 管理面板的 SnowLuma 状态显示

面板上「SnowLuma 注入」显示 **未运行** 时，先分清是真没跑还是探测不到：

| 面板显示 | 含义 | 处理 |
|---|---|---|
| 未运行 | 所有候选地址都连不上 | `docker ps` 看 `qqbot-snowluma` 是否在跑；`systemctl status qqbot-dashboard` 看面板是否正常 |
| 已注入 / 已注入并连接 | 正常 | 无需处理 |
| 状态未知 | WebUI 通了但拿不到注入状态 | 见下方两种原因 |

**原因一：端口绑在内网 IP 而不是回环地址。**
compose 里 `ports: "10.0.0.226:5099:5099"` 只监听该 IP，探 `127.0.0.1:5099` 会直接失败。
探测会依次尝试 `SNOWLUMA_URL`（如设置）→ `127.0.0.1:5099` → `host.docker.internal:5099` →
本机各内网 IP 的 `:5099`，命中后记住该地址。所以要固定用某个地址，就在面板的 service 里设：

```ini
Environment=SNOWLUMA_URL=http://10.0.0.226:5099
```

**原因二：没有 WebUI 密码，读不到注入状态。**
面板读 `tools/snowluma-webui-pass.txt` 登录 SnowLuma WebUI。文件缺失时仍能判定
「在运行」，但看不到注入情况，提示里会直接写明。写入密码即可：

```bash
mkdir -p /opt/qqbot/tools
printf '%s' '你的SnowLuma访问令牌' > /opt/qqbot/tools/snowluma-webui-pass.txt
chmod 600 /opt/qqbot/tools/snowluma-webui-pass.txt
systemctl restart qqbot-dashboard
```

> `snowluma/config/webui.json` 里只有 scrypt 哈希，反推不出明文；令牌丢了重置密码。
> 拿不到也没关系，注入状态可以直接看容器日志：
> `docker logs qqbot-snowluma | grep "login detected"`

## 7. 知识库（RAG）
```
knowledge/*.md  ──build_index──▶  rag/index/{chunks,tokens,lexicon,vectors}  ──serve──▶  :8091
```

- 检索只在判定「相关」时注入 1~2 块，无关一条都不塞
- 索引里记了 `embed_backend`（`cpu`/`gpu`）和 `dim`，**CPU 与 GPU 版索引不能混用**；fp16 只在 CUDA 上更快
- 语料改了要重建（几小时，支持断点续跑）；改检索阈值前后都要跑 `rag/eval_gate.py` 做闸门回归
- 服务挂了或超时（2.5s），插件一律当「没命中」，不影响正常聊天

## 8. 管理面板

`nonebot/dashboard.py` 提供只读状态与少量管理接口：群话题、天气推送、主人设置、解析器配置。
面板要求密码（`data/dashboard-auth.json`），所有写操作需要二次确认。

## 9. 上线前检查

- [ ] 5099 / 6081 / 8080 全都只绑内网或 `127.0.0.1`，**不要对公网开**
- [ ] WebUI、VNC、面板、数据库密码全是强的且各不相同
- [ ] 数据卷 `qqbot-qq-config` / `qqbot-qq-data` **绝对不能删**，删了要重新扫码
- [ ] `sysctl kernel.yama.ptrace_scope` ≤ 2（= 3 会直接拒绝注入）
- [ ] 给容器加 `mem_limit` / `cpus`
- [ ] 备份：数据库、`gate.json`、`model_pref.json`、`rag/index`

## 10. 终末地（endfield）

- 远程渲染器：宿主机模式 `HOST=127.0.0.1 PORT=3100`（开发服务器 3000 是 personal-wiki）；
  机器人 `render_url=http://127.0.0.1:3100`，3100 不要对公网开。
- Key：面板 `/endfield` 写入 `data/endfield_secrets.json`（0600），`ENDFIELD_API_KEY` 兜底；
  绑定 token 在 `data/endfield/bindings.json`（0600，明文，备份注意）。
- 调度器随 bot 启动，60s tick；心跳 `data/endfield/heartbeat.json`（>180s 视为离线）；
  审计 `data/endfield/audit.jsonl`（1 MB 轮转），面板读尾部。
- 测试：`cd nonebot && .venv/Scripts/python.exe -m pytest tests/endfield -q`（默认离线）。
- 公告订阅仅 superusers 在目标群；个人订阅仅私聊；图卡失败自动回退文本。

## 附录：Windows 单机部署（历史）

早期在 `D:\Software\qqbot` 上用 SnowLuma + NoneBot 双进程跑的方案，入口是
`启动机器人.bat` / `停止机器人.bat`（内部调 `tools\启动所有.ps1`）。
关键差异：用 `tools\只注入小号.py` 做账号隔离（探针识别 PID 后只 load 小号），
且所有 `.ps1` 必须存成带 UTF-8 BOM，否则 PowerShell 5.1 按 GBK 解析中文会报「字符串缺少终止符」。

这些脚本仍留在 `tools/` 里，但仓库的默认部署路径是 Linux。
