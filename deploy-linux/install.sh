#!/usr/bin/env bash
# Install or refresh the Linux deployment.
#
# Default architecture (matches the development server):
#   - SnowLuma: Docker Compose (protocol endpoint only)
#   - NoneBot, dashboard, optional gsuid_core: native Python + systemd
#
# This script creates the venv, installs requirements.lock and the media parser
# requirements, creates missing configuration from the tracked examples, runs
# migrations, initializes the dashboard password, prepares the SnowLuma compose
# files, and installs the systemd units. start.sh starts the services.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
DRY_RUN=0
CHECK_ONLY=0
NO_SYSTEMD=0
NO_DOCKER=0
NO_DEPS=0
NO_INIT=0
NO_PULL=0
WITH_GSIUD=0
WITH_RAG=0
YES=0
PYTHON="${PYTHON:-python3.12}"
VNC_PASSWD="${VNC_PASSWD:-}"
BIND_ADDR="${BIND_ADDR:-127.0.0.1}"
PY=""
PIP=""

usage() {
    cat <<'EOF'
Usage: sudo bash deploy-linux/install.sh [options]

Options:
  --root PATH          Repository root (default: parent of deploy-linux)
  --python PATH        Python 3.12 executable (default: $PYTHON or python3.12)
  --vnc-password PASS  SnowLuma noVNC password (default: generate once)
  --bind-addr ADDR     SnowLuma bind address (default: 127.0.0.1)
  --with-gsuid         Install gsuid_core and its systemd unit
  --with-rag           Create rag/.venv and install RAG runtime deps
  --no-systemd         Do not install systemd units
  --no-docker          Do not prepare or pull the SnowLuma container
  --no-deps            Do not create the venv or install requirements
  --no-init            Do not run migrations or dashboard --init
  --no-pull            Do not docker compose pull
  --check              Check prerequisites and exit
  --dry-run            Print actions without changing the system
  -y, --yes            Non-interactive (accepted for scripts; no prompts)
  -h, --help           Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --root) ROOT="$2"; shift 2 ;;
        --python) PYTHON="$2"; shift 2 ;;
        --vnc-password) VNC_PASSWD="$2"; shift 2 ;;
        --bind-addr) BIND_ADDR="$2"; shift 2 ;;
        --with-gsuid) WITH_GSIUD=1; shift ;;
        --with-rag) WITH_RAG=1; shift ;;
        --no-systemd) NO_SYSTEMD=1; shift ;;
        --no-docker) NO_DOCKER=1; shift ;;
        --no-deps) NO_DEPS=1; shift ;;
        --no-init) NO_INIT=1; shift ;;
        --no-pull) NO_PULL=1; shift ;;
        --check) CHECK_ONLY=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -y|--yes) YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

ROOT="$(CDPATH= cd -- "$ROOT" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

need_file "$ROOT/nonebot/bot.py"
need_file "$ROOT/requirements.lock"
cd "$ROOT"
umask 077

check_prereqs() {
    local missing=0
    if command -v "$PYTHON" >/dev/null 2>&1; then
        PY="$(command -v "$PYTHON")"
        log "Python: $("$PY" --version 2>&1)"
    elif command -v uv >/dev/null 2>&1; then
        log "Python: will be provisioned by uv (3.12)"
    else
        warn "Python 3.12 not found; set PYTHON=/path/to/python3.12 or install uv"
        missing=1
    fi
    for cmd in git; do
        if command -v "$cmd" >/dev/null 2>&1; then
            log "$cmd: $(command -v "$cmd")"
        else
            warn "missing required command: $cmd"
            missing=1
        fi
    done
    if [ "$NO_DOCKER" -eq 0 ]; then
        if command -v docker >/dev/null 2>&1; then
            log "docker: $(docker --version 2>&1)"
            if docker compose version >/dev/null 2>&1; then
                log "docker compose: $(docker compose version 2>&1 | head -1)"
            else
                warn "docker compose v2 is required"
                missing=1
            fi
            if ! docker info >/dev/null 2>&1; then
                warn "Docker daemon is not reachable; start Docker before start.sh"
                missing=1
            fi
            if command -v sysctl >/dev/null 2>&1; then
                local ptrace
                ptrace="$(sysctl -n kernel.yama.ptrace_scope 2>/dev/null || true)"
                if [ -n "$ptrace" ] && [ "$ptrace" -gt 2 ]; then
                    warn "kernel.yama.ptrace_scope=$ptrace; SnowLuma injection needs <= 2"
                fi
            fi
        else
            warn "missing required command: docker"
            missing=1
        fi
    fi
    if [ "$NO_SYSTEMD" -eq 0 ]; then
        if command -v systemctl >/dev/null 2>&1; then
            log "systemctl: $(command -v systemctl)"
        else
            warn "missing required command: systemctl (use --no-systemd to skip)"
            missing=1
        fi
        if [ "$(id -u)" -ne 0 ] && ! command -v sudo >/dev/null 2>&1; then
            warn "root or sudo is required to install systemd units"
            missing=1
        fi
    fi
    if [ "$WITH_GSIUD" -eq 1 ] && ! command -v uv >/dev/null 2>&1; then
        warn "--with-gsuid requires uv: https://docs.astral.sh/uv/getting-started/installation/"
        missing=1
    fi
    if [ "$WITH_RAG" -eq 1 ] && [ -z "$PY" ] && ! command -v uv >/dev/null 2>&1; then
        warn "--with-rag requires Python 3.12 or uv"
        missing=1
    fi
    if [ "$missing" -ne 0 ]; then
        die "prerequisite check failed"
    fi
    log "prerequisite check passed"
}

check_prereqs
if [ "$CHECK_ONLY" -eq 1 ]; then
    exit 0
fi

ensure_dirs() {
    log "ensuring runtime directories"
    run mkdir -p "$ROOT/local" "$ROOT/nonebot/data" "$ROOT/snowluma"
    run chmod 700 "$ROOT/local" "$ROOT/nonebot/data"
    # SnowLuma's container may run as a non-root UID; keep the data dir writable.
    run chmod 775 "$ROOT/snowluma"
}

ensure_venv() {
    if [ -x "$ROOT/.venv/bin/python" ]; then
        local version
        version="$("$ROOT/.venv/bin/python" -c 'import sys; print(str(sys.version_info[0]) + "." + str(sys.version_info[1]))')"
        if [ "$version" != "3.12" ]; then
            die "$ROOT/.venv uses Python $version; remove it and re-run (requires 3.12)"
        fi
        log "reusing venv: $ROOT/.venv"
    else
        if [ "$NO_DEPS" -eq 1 ]; then
            die "--no-deps was requested but $ROOT/.venv does not exist"
        fi
        if [ -n "$PY" ]; then
            log "creating venv with $PY"
            run "$PY" -m venv "$ROOT/.venv"
        else
            log "creating venv with uv"
            run uv venv --python 3.12 "$ROOT/.venv"
        fi
    fi
    PIP="$ROOT/.venv/bin/python"
    if [ "$NO_DEPS" -eq 1 ]; then
        log "skipping dependency installation (--no-deps)"
        return 0
    fi
    local hash marker requirement_file
    local -a requirement_files=("$ROOT/requirements.lock")
    local -a pip_args=()
    if [ -f "$ROOT/nonebot/media_parser/requirements.txt" ]; then
        requirement_files+=("$ROOT/nonebot/media_parser/requirements.txt")
    fi
    for requirement_file in "${requirement_files[@]}"; do
        pip_args+=(-r "$requirement_file")
    done
    hash="$(sha256sum "${requirement_files[@]}" | awk '{print $1}' | tr '\n' ':')"
    hash="${hash%:}"
    marker="$ROOT/.venv/.qqbot-requirements.sha256"
    if [ -f "$marker" ] && [ "$(cat "$marker")" = "$hash" ]; then
        log "Python dependencies are up to date"
    else
        if command -v uv >/dev/null 2>&1; then
            run uv pip install --python "$PIP" "${pip_args[@]}"
        else
            run "$PIP" -m pip install --disable-pip-version-check --no-input "${pip_args[@]}"
        fi
        if [ "$DRY_RUN" -eq 1 ]; then
            log "would write dependency marker: $marker"
        else
            printf '%s\n' "$hash" > "$marker"
        fi
    fi
    if command -v uv >/dev/null 2>&1; then
        run uv pip check --python "$PIP"
    else
        run "$PIP" -m pip check
    fi
    run "$PIP" -c "import nonebot, importlib.util; assert importlib.util.find_spec('muicebot') is not None"
    log "ensuring Playwright Chromium for the dashboard's Xiaohongshu login"
    if [ "$(id -u)" -eq 0 ]; then
        run "$PIP" -m playwright install --with-deps chromium
    else
        run "$PIP" -m playwright install chromium
    fi
}

ensure_configs() {
    log "ensuring configuration files (existing files are never overwritten)"
    copy_if_missing "$ROOT/nonebot/.env.example" "$ROOT/nonebot/.env"
    copy_if_missing "$ROOT/nonebot/configs/models.yml.example" "$ROOT/nonebot/configs/models.yml"
    copy_if_missing "$ROOT/nonebot/configs/templates.yml.example" "$ROOT/nonebot/configs/templates.yml"
    run chmod 600 "$ROOT/nonebot/.env" "$ROOT/nonebot/configs/models.yml" "$ROOT/nonebot/configs/templates.yml"

    if [ "$DRY_RUN" -eq 1 ]; then
        log "would generate ONEBOT_ACCESS_TOKEN if nonebot/.env still has the placeholder"
    elif grep -q '^ONEBOT_ACCESS_TOKEN=请替换成随机长串$' "$ROOT/nonebot/.env"; then
        replace_line "$ROOT/nonebot/.env" 'ONEBOT_ACCESS_TOKEN' "ONEBOT_ACCESS_TOKEN=$(random_hex 32)"
        log "generated ONEBOT_ACCESS_TOKEN in nonebot/.env"
    fi

    if [ "$NO_DOCKER" -eq 1 ]; then
        log "skipping SnowLuma env (--no-docker)"
        return 0
    fi
    local env_local="$ROOT/docker/snowluma/.env.local"
    local created=0
    if [ -e "$env_local" ]; then
        log "keep existing $env_local"
    else
        copy_if_missing "$ROOT/docker/snowluma/.env.example" "$env_local"
        created=1
    fi
    run chmod 600 "$env_local"
    if [ "$DRY_RUN" -eq 1 ]; then
        log "would fill VNC_PASSWD and BIND_ADDR in docker/snowluma/.env.local"
    else
        if [ -n "$VNC_PASSWD" ]; then
            replace_line "$env_local" 'VNC_PASSWD' "VNC_PASSWD=$VNC_PASSWD"
        elif grep -q '^VNC_PASSWD=请改成你自己的强密码$' "$env_local"; then
            replace_line "$env_local" 'VNC_PASSWD' "VNC_PASSWD=$(random_hex 16)"
            log "generated VNC_PASSWD in docker/snowluma/.env.local"
        fi
        if [ "$created" -eq 1 ]; then
            replace_line "$env_local" 'BIND_ADDR' "BIND_ADDR=$BIND_ADDR"
        fi
    fi
    if [ "$DRY_RUN" -eq 0 ]; then
        log "SnowLuma config: $env_local (VNC password is stored there, mode 600)"
    fi
}

ensure_init() {
    if [ "$NO_INIT" -eq 1 ]; then
        log "skipping migrations/dashboard init (--no-init)"
        return 0
    fi
    [ -n "$PIP" ] || die "Python venv is not available; cannot run migrations"
    log "running database migrations"
    ( cd "$ROOT/nonebot" && run "$PIP" migrate_db.py upgrade )
    if [ -f "$ROOT/nonebot/data/dashboard-auth.json" ]; then
        log "dashboard authentication already exists; keeping it"
    else
        log "initializing dashboard password"
        ( cd "$ROOT/nonebot" && run "$PIP" dashboard.py --init >/dev/null )
        log "one-time dashboard password: $ROOT/nonebot/data/dashboard-initial-password"
    fi
}

ensure_snowluma() {
    if [ "$NO_DOCKER" -eq 1 ]; then
        log "skipping SnowLuma compose (--no-docker)"
        return 0
    fi
    log "preparing SnowLuma compose"
    copy_if_missing "$ROOT/docker/snowluma/docker-compose.yml" "$ROOT/compose.yml"
    if [ "$DRY_RUN" -eq 0 ]; then
        compose_run config --quiet
    else
        log "would validate docker compose config"
    fi
    if [ "$NO_PULL" -eq 0 ]; then
        log "pulling SnowLuma image (a cached image is enough)"
        if ! compose_run pull snowluma; then
            local snow_image
            snow_image="$(compose_run config --images 2>/dev/null | head -1 || true)"
            if [ -n "$snow_image" ] && docker image inspect "$snow_image" >/dev/null 2>&1; then
                warn "SnowLuma image pull failed; using cached image: $snow_image"
            else
                die "SnowLuma image pull failed and no cached image is available; set SNOWLUMA_IMAGE in docker/snowluma/.env.local or run docker login"
            fi
        fi
    else
        log "skipping docker compose pull (--no-pull)"
    fi
}

ensure_gsuid() {
    if [ "$WITH_GSIUD" -eq 0 ]; then
        log "gsuid install not requested (--with-gsuid)"
        return 0
    fi
    log "installing gsuid_core (pinned upstream)"
    ( cd "$ROOT" && run bash gsuid/install.sh )
}

ensure_rag() {
    if [ "$WITH_RAG" -eq 0 ]; then
        return 0
    fi
    log "creating optional RAG environment"
    if [ ! -x "$ROOT/rag/.venv/bin/python" ]; then
        if [ -n "$PY" ]; then
            run "$PY" -m venv "$ROOT/rag/.venv"
        else
            run uv venv --python 3.12 "$ROOT/rag/.venv"
        fi
    fi
    if command -v uv >/dev/null 2>&1; then
        run uv pip install --python "$ROOT/rag/.venv/bin/python" jieba numpy fastapi uvicorn
    else
        run "$ROOT/rag/.venv/bin/python" -m pip install --disable-pip-version-check --no-input jieba numpy fastapi uvicorn
    fi
    log "RAG deps installed (SiliconFlow cloud-only path)."
    log "1) save the SiliconFlow API key in nonebot/data/embedding_secrets.json"
    log "2) build:  $ROOT/rag/.venv/bin/python $ROOT/rag/rebuild_index.py --knowledge $ROOT/knowledge --output $ROOT/rag/index-siliconflow"
    log "3) serve:  $ROOT/rag/.venv/bin/python $ROOT/rag/serve_cloud.py --port 8091"
    log "   (--activate swaps the index; Linux uses the qqbot-rag-cloud systemd unit when installed)"
}

ensure_systemd() {
    if [ "$NO_SYSTEMD" -eq 1 ]; then
        log "skipping systemd units (--no-systemd)"
        return 0
    fi
    local unit_dir="/etc/systemd/system"
    log "installing systemd units into $unit_dir"
    render_unit "$SCRIPT_DIR/systemd/qqbot.service" "$unit_dir/qqbot.service"
    render_unit "$ROOT/nonebot/qqbot-dashboard.service" "$unit_dir/qqbot-dashboard.service"
    if [ "$WITH_GSIUD" -eq 1 ] || [ -x "$ROOT/gsuid/core/.venv/bin/python" ]; then
        render_unit "$ROOT/gsuid/qqbot-gsuid-core.service" "$unit_dir/qqbot-gsuid-core.service"
        local dropin_dir="$unit_dir/qqbot.service.d"
        run_root mkdir -p "$dropin_dir"
        local dropin
        dropin="$(mktemp)"
        printf '[Unit]\nAfter=qqbot-gsuid-core.service\nWants=qqbot-gsuid-core.service\n' > "$dropin"
        run_root install -m 0644 "$dropin" "$dropin_dir/gsuid.conf"
        rm -f "$dropin"
        systemctl_run enable qqbot-gsuid-core
    else
        log "gsuid_core is not installed; skipping its unit (use --with-gsuid)"
    fi
    systemctl_run daemon-reload
    systemctl_run enable qqbot qqbot-dashboard
    log "systemd units installed; run deploy-linux/start.sh to start"
}

ensure_dirs
ensure_venv
ensure_configs
ensure_gsuid
ensure_rag
ensure_init
ensure_snowluma
ensure_systemd

log "install complete"
log "edit nonebot/.env and nonebot/configs/models.yml if you have not done so"
log "start: sudo bash $ROOT/deploy-linux/start.sh"
