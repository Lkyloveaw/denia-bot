# =====================================================================
#  AxonHub 启停（AI 网关）
#  用法：
#    powershell -File "D:\Software\qqbot\tools\AxonHub.ps1" start
#    powershell -File "D:\Software\qqbot\tools\AxonHub.ps1" stop
#    powershell -File "D:\Software\qqbot\tools\AxonHub.ps1" status
# =====================================================================

param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Continue'

$ROOT = 'D:\Software\qqbot'
$BASE = "$ROOT\axonhub"
$BIN  = "$BASE\axonhub.exe"
$LOG  = "$ROOT\logs"
$PORT = 8090

New-Item -ItemType Directory -Force -Path $LOG | Out-Null
$env:PYTHONIOENCODING = 'utf-8'

function Get-AxonProcs {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq 'axonhub.exe' }
}

function Show-Status {
    $procs = Get-AxonProcs
    $listening = [bool](Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue)
    if ($procs) {
        foreach ($p in $procs) { Write-Host "  运行中  PID $($p.ProcessId)" -ForegroundColor Green }
    } else {
        Write-Host "  未运行" -ForegroundColor Yellow
    }
    if ($listening) { Write-Host "  监听 127.0.0.1:$PORT  OK" -ForegroundColor Green }
    else            { Write-Host "  端口 $PORT 未监听" -ForegroundColor Yellow }
}

switch ($Action) {

    'start' {
        if (Get-AxonProcs) {
            Write-Host "AxonHub 已在运行：" -ForegroundColor Yellow
            Show-Status
            return
        }
        if (-not (Test-Path $BIN)) { Write-Host "找不到 $BIN" -ForegroundColor Red; return }
        # 必须用 $BASE 作工作目录：config.yml / axonhub.db / logs 都是相对路径
        Start-Process -FilePath $BIN -WorkingDirectory $BASE `
            -RedirectStandardOutput "$LOG\axonhub.out.log" `
            -RedirectStandardError  "$LOG\axonhub.err.log" `
            -WindowStyle Hidden
        Write-Host "已启动，等待就绪..." -NoNewline
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Seconds 1
            Write-Host "." -NoNewline
            if (Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue) { break }
        }
        Write-Host ""
        Show-Status
        Write-Host "  WebUI : http://127.0.0.1:$PORT" -ForegroundColor Cyan
        Write-Host "  API   : http://127.0.0.1:$PORT/v1  (OpenAI 兼容)" -ForegroundColor Cyan
    }

    'stop' {
        $procs = Get-AxonProcs
        if (-not $procs) { Write-Host "没有在运行" -ForegroundColor Yellow; return }
        foreach ($p in $procs) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "  已停止 PID $($p.ProcessId)" -ForegroundColor Green
        }
        Start-Sleep -Seconds 2
        Show-Status
    }

    'restart' {
        & $PSCommandPath stop
        Start-Sleep -Seconds 3
        & $PSCommandPath start
    }

    'status' { Show-Status }
}
