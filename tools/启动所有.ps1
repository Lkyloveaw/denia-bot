# =====================================================================
#  QQ 机器人一键启动（SnowLuma + NoneBot2/MuiceBot）
#  启动顺序：先 NoneBot（WebSocket 服务端），再 SnowLuma（连出去）
# =====================================================================

$ErrorActionPreference = 'Continue'

$ROOT       = 'D:\Software\qqbot'
$SNOWLUMA   = "$ROOT\snowluma\node.exe"
$NONE_LUA   = "$ROOT\nonebot\.venv\Scripts\python.exe"
$LOG        = "$ROOT\logs"

# RAG 服务用哪个 venv：索引是 GPU(fp16) 建的，查询向量就必须也走 CUDA，
# 否则就是拿 fp32 模型去比 fp16 向量。有 .venv-gpu 就用它，没有退回 .venv。
$RAG_PY     = "$ROOT\rag\.venv\Scripts\python.exe"
if (Test-Path "$ROOT\rag\.venv-gpu\Scripts\python.exe") {
    $RAG_PY = "$ROOT\rag\.venv-gpu\Scripts\python.exe"
}

New-Item -ItemType Directory -Force -Path $LOG | Out-Null

# 否则 Python 在 Windows 上重定向输出时会用 GBK，日志里的中文会变成乱码
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

function Test-Listening($port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  QQ 机器人启动 (SnowLuma + NoneBot2)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 1. RAG 检索服务 ----------
Write-Host "[1/4] RAG 检索服务 (8091) ..." -NoNewline
if (Test-Listening 8091) {
    Write-Host " 已在运行" -ForegroundColor Yellow
} elseif (-not (Test-Path "$ROOT\rag\index\vectors.npy")) {
    Write-Host " [!!] 索引不存在，先运行 rag\build_index.py" -ForegroundColor Yellow
} else {
    Start-Process -FilePath $RAG_PY -ArgumentList 'serve.py' `
        -WorkingDirectory "$ROOT\rag" `
        -RedirectStandardOutput "$LOG\rag.out.log" `
        -RedirectStandardError  "$LOG\rag.err.log" `
        -WindowStyle Hidden
    Write-Host " 已启动" -ForegroundColor Green
}

# ---------- 2. NoneBot2 + MuiceBot ----------
Write-Host "[2/4] NoneBot2 + MuiceBot ..." -NoNewline
if (Test-Listening 8080) {
    Write-Host " 已在运行" -ForegroundColor Yellow
} else {
    Start-Process -FilePath $NONE_LUA -ArgumentList 'bot.py' `
        -WorkingDirectory "$ROOT\nonebot" `
        -RedirectStandardOutput "$LOG\nonebot.out.log" `
        -RedirectStandardError  "$LOG\nonebot.err.log" `
        -WindowStyle Hidden
    Write-Host " 已启动" -ForegroundColor Green}

# ---------- 3. SnowLuma ----------
Write-Host "[3/4] SnowLuma (QQ 协议端) ..." -NoNewline
if (Test-Listening 5099) {
    Write-Host " 已在运行" -ForegroundColor Yellow
} else {
    Start-Process -FilePath $SNOWLUMA -ArgumentList 'index.mjs' `
        -WorkingDirectory "$ROOT\snowluma" `
        -RedirectStandardOutput "$LOG\snowluma.out.log" `
        -RedirectStandardError  "$LOG\snowluma.err.log" `
        -WindowStyle Hidden
    Write-Host " 已启动" -ForegroundColor Green
}

# ---------- 4. 等待各端就绪 ----------
Write-Host "[4/4] 等待服务就绪 ..." -NoNewline
$ok1 = $false; $ok2 = $false; $ok3 = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    Write-Host "." -NoNewline
    if (-not $ok1) { $ok1 = Test-Listening 8080 }
    if (-not $ok2) { $ok2 = Test-Listening 5099 }
    if (-not $ok3) { $ok3 = Test-Listening 8091 }
    if ($ok1 -and $ok2 -and $ok3) { break }
}
Write-Host ""
Write-Host ""

if ($ok1) { Write-Host "  [OK] NoneBot2          : http://127.0.0.1:8080" -ForegroundColor Green }
else      { Write-Host "  [!!] NoneBot2 未就绪，看日志: $LOG\nonebot.out.log" -ForegroundColor Red }

if ($ok2) { Write-Host "  [OK] SnowLuma WebUI    : http://127.0.0.1:5099" -ForegroundColor Green }
else      { Write-Host "  [!!] SnowLuma 未就绪，看日志: $LOG\snowluma.out.log" -ForegroundColor Red }

if ($ok3) { Write-Host "  [OK] RAG 检索服务      : http://127.0.0.1:8091" -ForegroundColor Green }
else      { Write-Host "  [!!] RAG 未就绪，看日志: $LOG\rag.out.log" -ForegroundColor Red }

# ---------- 4. 收敛注入账号：只保留小号 ----------
Write-Host ""
Write-Host "收敛 QQ 注入账号（只保留 3892894069）..." -ForegroundColor Cyan
if ($ok2) {
    Start-Sleep -Seconds 8   # 等 SnowLuma 完成自动注入
    & "$ROOT\nonebot\.venv\Scripts\python.exe" "$ROOT\tools\只注入小号.py"
} else {
    Write-Host "  SnowLuma 未就绪，跳过" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "---------------- 连接信息 ----------------" -ForegroundColor DarkGray
Write-Host "  OneBot 反向 WS : ws://127.0.0.1:8080/onebot/v11/ws" -ForegroundColor DarkGray
Write-Host "  机器人 QQ      : 3892894069" -ForegroundColor DarkGray
Write-Host "  停止服务请运行 : 停止机器人.bat" -ForegroundColor DarkGray
Write-Host ""
