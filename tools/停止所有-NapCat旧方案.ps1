# =====================================================================
#  QQ 机器人 - 一键停止
# =====================================================================

$ErrorActionPreference = 'Continue'

Write-Host "正在停止 QQ 机器人..." -ForegroundColor Cyan

$killed = 0

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'astrbot\.exe' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止 AstrBot (PID $($_.ProcessId))" -ForegroundColor Green
        $killed++
    }

Get-Process node -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
        if ($cl -match 'napcat|index\.js') {
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            Write-Host "  已停止 NapCat (PID $($_.Id))" -ForegroundColor Green
            $killed++
        }
    } catch {}
}

if ($killed -eq 0) { Write-Host "  没有在运行的服务" -ForegroundColor Yellow }

Start-Sleep -Seconds 3
Write-Host ""
Write-Host "完成。已停止 $killed 个进程。" -ForegroundColor Cyan
