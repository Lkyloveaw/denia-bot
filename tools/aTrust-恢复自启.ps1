# =====================================================================
#  aTrust 恢复开机自启（需要管理员）
#  用途：学校的合规要求 / 需要 aTrust 常驻时，一键还原
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

Write-Host "=== aTrust 恢复自启 ===" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] 服务 aTrustService : -> AUTO_START"
sc.exe config aTrustService start= auto
if ($LASTEXITCODE -eq 0) { Write-Host "      OK" -ForegroundColor Green }
else { Write-Host "      失败 (exit $LASTEXITCODE)" -ForegroundColor Red }

Write-Host "[2/3] 驱动 aTrustNetflt_0 : -> AUTO"
sc.exe config aTrustNetflt_0 start= auto
if ($LASTEXITCODE -eq 0) { Write-Host "      OK" -ForegroundColor Green }
else { Write-Host "      失败 (exit $LASTEXITCODE)" -ForegroundColor Red }

Write-Host "[3/3] 还原 HKCU 启动项 aTrustTray"
$backup = Join-Path $PSScriptRoot 'atrust-backup\aTrustTray.txt'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if (Test-Path $backup) {
    $val = Get-Content $backup -Raw
    Set-ItemProperty -Path $runKey -Name 'aTrustTray' -Value $val.Trim()
    Write-Host "      已还原" -ForegroundColor Green
} else {
    Write-Host "      无备份，跳过（不影响功能）" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "=== 完成，aTrust 恢复为开机自启 ===" -ForegroundColor Cyan
sc.exe qc aTrustService  | Select-String 'START_TYPE|SERVICE_NAME'
sc.exe qc aTrustNetflt_0 | Select-String 'START_TYPE|SERVICE_NAME'
Write-Host ""
Read-Host "按回车关闭"
