# =====================================================================
#  QQ 机器人 - 一键启动
#  启动 AstrBot（聊天框架）+ NapCat（QQ 协议端）
# =====================================================================

$ErrorActionPreference = 'Continue'

$ROOT      = 'D:\Software\qqbot'
$ASTRBOT   = "$env:USERPROFILE\.local\bin\astrbot.exe"
$NAPCAT    = "$ROOT\napcat\node.exe"
$LOG       = "$ROOT\logs"
$BOT_UIN   = '3892894069'      # 机器人 QQ 号

New-Item -ItemType Directory -Force -Path $LOG | Out-Null

function Is-Running($pattern) {
    $hit = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern }
    return [bool]$hit
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  QQ 机器人启动" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 1. AstrBot ----------
Write-Host "[1/2] AstrBot (聊天框架)..." -NoNewline
if (Is-Running 'astrbot\.exe"?\s+run') {
    Write-Host " 已在运行，跳过" -ForegroundColor Yellow
} else {
    Start-Process -FilePath $ASTRBOT -ArgumentList 'run' `
        -WorkingDirectory $ROOT `
        -RedirectStandardOutput "$LOG\astrbot.out.log" `
        -RedirectStandardError  "$LOG\astrbot.err.log" `
        -WindowStyle Hidden
    Write-Host " 已启动" -ForegroundColor Green
}

# ---------- 2. NapCat ----------
Write-Host "[2/2] NapCat (QQ 协议端)..." -NoNewline
if (Is-Running 'napcat\\node\.exe') {
    Write-Host " 已在运行，跳过" -ForegroundColor Yellow
} else {
    Start-Process -FilePath $NAPCAT -ArgumentList 'index.js', '-q', $BOT_UIN `
        -WorkingDirectory "$ROOT\napcat" `
        -RedirectStandardOutput "$LOG\napcat.out.log" `
        -RedirectStandardError  "$LOG\napcat.err.log" `
        -WindowStyle Hidden
    Write-Host " 已启动" -ForegroundColor Green
}

# ---------- 等待就绪 ----------
Write-Host ""
Write-Host "等待服务就绪..." -NoNewline
$ok1 = $false; $ok2 = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    Write-Host "." -NoNewline
    if (-not $ok1) {
        $ok1 = [bool](Get-NetTCPConnection -LocalPort 6185 -State Listen -ErrorAction SilentlyContinue)
    }
    if (-not $ok2) {
        $ok2 = [bool](Get-NetTCPConnection -LocalPort 6099 -State Listen -ErrorAction SilentlyContinue)
    }
    if ($ok1 -and $ok2) { break }
}
Write-Host ""
Write-Host ""

if ($ok1) { Write-Host "  [OK] AstrBot WebUI : http://127.0.0.1:6185" -ForegroundColor Green }
else      { Write-Host "  [!!] AstrBot 未就绪，看日志: $LOG\astrbot.out.log" -ForegroundColor Red }

if ($ok2) { Write-Host "  [OK] NapCat WebUI  : http://127.0.0.1:6099/webui" -ForegroundColor Green }
else      { Write-Host "  [!!] NapCat 未就绪，看日志: $LOG\napcat.out.log" -ForegroundColor Red }

Write-Host ""
Write-Host "OneBot 反向 WS  : ws://127.0.0.1:6199/ws" -ForegroundColor DarkGray
Write-Host "停止全部服务请运行: 停止机器人.bat" -ForegroundColor DarkGray
Write-Host ""
