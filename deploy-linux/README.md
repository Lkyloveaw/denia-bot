# Linux 部署（SnowLuma Docker + systemd）

面向 Linux 服务器的默认入口。和开发服务器同构：

- **SnowLuma（QQ 协议端）**：Docker Compose，默认只绑 `127.0.0.1`。
- **NoneBot / 管理面板 / gsuid_core（可选）**：仓库根 `.venv` + systemd。
- **依赖创建**：`install.sh` 创建 `.venv`、安装 `requirements.lock` 和 `nonebot/media_parser/requirements.txt`、复制缺失配置、生成 OneBot token / VNC 密码、跑迁移、初始化面板密码。
- **服务创建**：安装 `qqbot.service`、`qqbot-dashboard.service`，检测到 gsuid 时安装 `qqbot-gsuid-core.service` 和启动顺序 drop-in。

> 机器人本体不放进 Docker；这是开发服务器当前的生产形态。若要把 NoneBot 也容器化，需要单独的 Dockerfile、数据卷和 RAG/Playwright 编排，属于另一项改动。

## 前置条件

- x86_64 Linux，systemd，root 或 sudo。
- Python 3.12（`python3.12`；也可以先安装 `uv`，脚本会用 uv 创建环境）。
- `git`、`curl`。
- Docker Engine + Compose v2（默认模式需要）。
- 能访问 PyPI、Docker Hub/GitHub；首次安装会下载依赖和 SnowLuma 镜像。公开镜像不可用时，在 `docker/snowluma/.env.local` 设置 `SNOWLUMA_IMAGE`（私有仓库先 `docker login`）；`install.sh` 在 pull 失败但本地已有镜像时会继续。只有本地镜像时，可在已有镜像的机器 `docker save` 后到目标机 `docker load`，再执行 `install.sh --no-pull`。
- 宿主机 `kernel.yama.ptrace_scope` ≤ 2（SnowLuma 注入需要）；脚本只警告，不自动改 sysctl。

## 首次安装

```bash
cd /opt/qqbot
sudo bash deploy-linux/install.sh
```

常用选项：

```text
--root PATH           非 /opt/qqbot 的检出目录
--python PATH         指定 Python 3.12
--bind-addr ADDR      SnowLuma 绑定地址（默认 127.0.0.1；内网访问才改）
--vnc-password PASS   指定 noVNC 密码（默认生成一次）
--with-gsuid          安装 gsuid_core 及其 systemd 单元
--with-rag            创建 rag/.venv 并安装 RAG 运行依赖
--no-systemd          只准备文件，不写 /etc/systemd/system
--no-docker           不准备/拉取 SnowLuma 容器
--no-deps             不创建 venv / 不装 Python 依赖
--no-init             不跑迁移、不初始化面板密码
--no-pull             不执行 docker compose pull
--check               只检查前置条件
--dry-run             只打印将要执行的操作
```

首次安装会创建：

| 路径 | 说明 |
|---|---|
| `.venv/` | 根虚拟环境，安装 `requirements.lock` 与 `media_parser` 解析依赖 |
| `nonebot/.env` | 从 `.env.example` 复制；保留已有文件 |
| `nonebot/configs/models.yml` | 从 `.example` 复制；保留已有文件 |
| `nonebot/configs/templates.yml` | 从 `.example` 复制；保留已有文件 |
| `docker/snowluma/.env.local` | SnowLuma VNC 密码 / 绑定地址，0600，已忽略 |
| `compose.yml` | 从 `docker/snowluma/docker-compose.yml` 复制，已忽略 |
| `snowluma/` | SnowLuma 配置与数据，已忽略 |
| `nonebot/data/dashboard-initial-password` | 首次面板初始化的一次性密码，0600 |

依赖摘要同时覆盖 `requirements.lock` 和 `nonebot/media_parser/requirements.txt`；任一文件变化都会触发重装。干净环境也会装上 `yt-dlp` 等链接解析依赖。

安装不会覆盖已有的 `.env`、`models.yml`、`templates.yml`、`.env.local`、`compose.yml` 或数据目录。若 `.env` 里仍是占位 token，会生成一个随机 `ONEBOT_ACCESS_TOKEN`；SnowLuma WebUI 要填同一个值。

## 启动 / 停止 / 状态

```bash
sudo bash deploy-linux/start.sh
sudo bash deploy-linux/stop.sh
bash deploy-linux/status.sh
```

`start.sh` 默认先跑一次 `install.sh --no-pull`（检查依赖、配置、迁移和 systemd 单元），然后按顺序：

1. `docker compose ... up -d` 启动 SnowLuma。
2. 如果 `qqbot-gsuid-core.service` 已安装，先启动 gsuid_core。
3. 启动 `qqbot` 和 `qqbot-dashboard`。
4. 等 `http://127.0.0.1:8080/dashboard-health` 就绪（面板绑定内网地址时，面板健康检查会提示 DOWN，改用 `systemctl status`）。

`stop.sh` 只 `systemctl stop` + `docker compose stop`，不删除容器、卷或数据。

## SnowLuma 首次接入

1. 浏览器打开 `http://<服务器IP>:6081`，用 `docker/snowluma/.env.local` 里的 `VNC_PASSWD` 登录 noVNC，在容器里的 QQ 扫码登录小号。
2. 打开 `http://<服务器IP>:5099` 的 WebUI，登录凭据在 `docker compose ... logs` 里。
3. 在 WebUI 配置反向 WebSocket：
   - 地址：`ws://host.docker.internal:8080/onebot/v11/ws`
   - Token：`nonebot/.env` 的 `ONEBOT_ACCESS_TOKEN`
4. 看到 `Bot <uin> connected` 后，在面板完成 Bot 基本设置和插件引导。

## 管理面板

首次安装已执行 `dashboard.py --init`。读取一次性密码：

```bash
sudo cat /opt/qqbot/nonebot/data/dashboard-initial-password
```

面板默认只绑 `127.0.0.1:8092`；需要内网访问时用 systemd drop-in：

```ini
# /etc/systemd/system/qqbot-dashboard.service.d/host.conf
[Service]
Environment=DASHBOARD_HOST=<部署机内网地址>
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart qqbot-dashboard
```

## RAG（可选，SiliconFlow 云端）

唯一支持的检索/嵌入路径是 SiliconFlow `BAAI/bge-m3`（1024 维），不再安装或运行本地
ONNX 模型。`install.sh --with-rag` 只创建 `rag/.venv` 并安装 `jieba numpy fastapi uvicorn`：

```bash
# 1) 在管理面板保存 SiliconFlow API Key（写入 nonebot/data/embedding_secrets.json）
# 2) 构建云端索引
rag/.venv/bin/python rag/rebuild_index.py --knowledge knowledge --output rag/index-siliconflow
# 3) 启动服务（生产机可用 systemd 单元 qqbot-rag-cloud，设置 KB_CLOUD_INDEX）
rag/.venv/bin/python rag/serve_cloud.py --port 8091
```

`/health` 返回 `embed_backend=siliconflow`、`model=BAAI/bge-m3` 和实际索引目录 `index`。
`rebuild_index.py --activate` 在 Linux 且安装了 `qqbot-rag-cloud` unit 时走 systemd；
Windows 本机则停止 8091、原子替换目录并重新拉起 `serve_cloud.py`。旧的本地 ONNX
入口只在 `rag/legacy-local/` 保留作历史参考，不受支持。

## systemd 单元

`install.sh` 会把仓库模板里的 `/opt/qqbot` 替换成实际 `--root` 后安装：

- `deploy-linux/systemd/qqbot.service` → `/etc/systemd/system/qqbot.service`
- `nonebot/qqbot-dashboard.service` → `/etc/systemd/system/qqbot-dashboard.service`
- `gsuid/qqbot-gsuid-core.service` → `/etc/systemd/system/qqbot-gsuid-core.service`（检测到 `gsuid/core/.venv` 或 `--with-gsuid`）
- `/etc/systemd/system/qqbot.service.d/gsuid.conf`：让 qqbot 在 gsuid 之后启动。

所有服务默认以 root 运行；仓库根权限应保持 `700`，配置/数据文件 `600`。

## 测试

```bash
# 只检查前置条件（不写文件）
bash deploy-linux/install.sh --check --no-docker --no-systemd

# 打印完整安装流程，不写文件
bash deploy-linux/install.sh --dry-run --no-docker --no-systemd --no-init

# compose 模板语法（需要 Docker Compose）
VNC_PASSWD=test BIND_ADDR=127.0.0.1 \
  docker compose -f docker/snowluma/docker-compose.yml \
  --project-directory . config --quiet

# 仓库测试（Linux）
python -m pytest nonebot/tests/test_linux_deploy.py -q
```

## 安全边界

- 5099（SnowLuma WebUI）和 6081（noVNC）默认只绑 `127.0.0.1`；需要内网访问时改 `BIND_ADDR` 并用防火墙限制来源，绝不对公网开放。
- SnowLuma 容器需要 `SYS_PTRACE` + `seccomp=unconfined`，只在自己独占的机器上运行。
- 账号注入是 PID 级，不能按 UIN 过滤；`SNOWLUMA_HOOK_AUTOLOAD=0` 只恢复已接入账号。
- 备份 `nonebot/data/`、`nonebot/templates/current.jinja2`、`snowluma/`、`local/`、`rag/index-siliconflow*/`；不要 `git clean -fdx`。
- 本脚本不替代防火墙、SSH 隧道、反向代理和 QQ 小号风控。
