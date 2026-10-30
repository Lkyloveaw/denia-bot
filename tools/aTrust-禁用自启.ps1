# =====================================================================
#  aTrust 禁用开机自启（需要管理员）
#  作用：让 aTrust 不再开机常驻，只有你手动打开时才启动
#  还原：运行 aTrust-恢复自启.ps1
# =====================================================================

$ErrorActionPreference = 'Continue'

function Test-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "[!] 需要管理员权限，正在请求提权..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $PSCommandPath
    )
    exit
}

Write-Host "=== aTrust 禁用自启 ===" -ForegroundColor Cyan
Write-Host ""

# --- 1. 服务：自动 -> 手动 ---
Write-Host "[1/3] 服务 aTrustService : AUTO_START -> DEMAND_START"
sc.exe config aTrustService start= demand
if ($LASTEXITCODE -eq 0) { Write-Host "      OK" -ForegroundColor Green }
else { Write-Host "      失败 (exit $LASTEXITCODE)" -ForegroundColor Red }

# --- 2. 内核驱动：自动 -> 手动 ---
Write-Host "[2/3] 驱动 aTrustNetflt_0 : AUTO -> DEMAND"
sc.exe config aTrustNetflt_0 start= demand
if ($LASTEXITCODE -eq 0) { Write-Host "      OK" -ForegroundColor Green }
else { Write-Host "      失败 (exit $LASTEXITCODE)" -ForegroundColor Red }

# --- 3. 残留启动项 ---
Write-Host "[3/3] 清理 HKCU 启动项 aTrustTray"
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$existing = (Get-ItemProperty -Path $runKey -Name 'aTrustTray' -ErrorAction SilentlyContinue).aTrustTray
if ($null -ne $existing) {
    # 备份原值，方便还原
    $backupDir = Join-Path $PSScriptRoot 'atrust-backup'
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    Set-Content -Path (Join-Path $backupDir 'aTrustTray.txt') -Value $existing -Encoding UTF8
    Remove-ItemProperty -Path $runKey -Name 'aTrustTray' -ErrorAction SilentlyContinue
    Write-Host "      已删除（原值已备份到 atrust-backup\aTrustTray.txt）" -ForegroundColor Green
} else {
    Write-Host "      不存在，跳过" -ForegroundColor DarkGray
}

# --- 4. 立即停止当前实例 ---
Write-Host "[4/4] 立即停止 aTrust 当前实例"
Stop-Service -Name aTrustService -Force -ErrorAction SilentlyContinue
try {
    Get-Process aTrustAgent, aTrustTray, aTrust -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "      已停止并结束托盘进程" -ForegroundColor Green
} catch {
    Write-Host "      进程结束失败，可手动在托盘退出" -ForegroundColor DarkGray
}
Start-Sleep -Seconds 2
$vnic = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceDescription -match 'aTrust' }
if ($vnic -and $vnic.Status -eq 'Up') {
    Disable-NetAdapter -Name $vnic.Name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "      虚拟网卡已禁用（下次手动打开 aTrust 会自动恢复）" -ForegroundColor Green
} else {
    Write-Host "      虚拟网卡已断开" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== 完成 ===" -ForegroundColor Cyan
Write-Host "aTrust 现在不会开机自启。需要用时从开始菜单手动打开即可。" -ForegroundColor White
Write-Host ""
Write-Host "当前状态：" -ForegroundColor White
sc.exe qc aTrustService    | Select-String 'START_TYPE|SERVICE_NAME'
sc.exe qc aTrustNetflt_0   | Select-String 'START_TYPE|SERVICE_NAME'
Write-Host ""
Read-Host "按回车关闭"
