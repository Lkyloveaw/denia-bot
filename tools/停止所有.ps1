# =====================================================================
#  QQ 机器人一键停止（SnowLuma + NoneBot2/MuiceBot）
# =====================================================================

$ErrorActionPreference = 'Continue'

Write-Host "正在停止 QQ 机器人..." -ForegroundColor Cyan
$killed = 0

# NoneBot2
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'bot\.py' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止 NoneBot2 (PID $($_.ProcessId))" -ForegroundColor Green
        $killed++
    }

# RAG 检索服务
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'serve\.py' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止 RAG 检索服务 (PID $($_.ProcessId))" -ForegroundColor Green
        $killed++
    }

# SnowLuma
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -match 'snowluma' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止 SnowLuma (PID $($_.ProcessId))" -ForegroundColor Green
        $killed++
    }

if ($killed -eq 0) { Write-Host "  没有在运行的服务" -ForegroundColor Yellow }

Start-Sleep -Seconds 3
Write-Host ""
Write-Host "完成。已停止 $killed 个进程。" -ForegroundColor Cyan
Write-Host "注意：SnowLuma 停止后不会再 hook QQ；QQ 客户端本身不受影响。" -ForegroundColor DarkGray
