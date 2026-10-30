# Stop only the NoneBot process belonging to this installation.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$botDir = Join-Path $root 'nonebot'
$venvPython = [IO.Path]::GetFullPath((Join-Path $botDir '.venv\Scripts\python.exe'))
$botFile = [IO.Path]::GetFullPath((Join-Path $botDir 'bot.py'))
$processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
$byId = @{}
foreach ($process in $processes) { $byId[[int]$process.ProcessId] = $process }
$owned = @()
foreach ($process in $processes) {
    if ($process.Name -ine 'python.exe' -or -not $process.CommandLine) { continue }
    $command = [string]$process.CommandLine
    $isBot = $command -match '(?i)(?:^|[\s"])bot\.py(?:[\s"]|$)' -or $command.IndexOf($botFile, [StringComparison]::OrdinalIgnoreCase) -ge 0
    if (-not $isBot) { continue }
    $parent = $byId[[int]$process.ParentProcessId]
    $ownExe = [string]$process.ExecutablePath
    $parentExe = if ($parent) { [string]$parent.ExecutablePath } else { '' }
    if ($ownExe -ieq $venvPython -or $parentExe -ieq $venvPython) { $owned += [int]$process.ProcessId }
}
$listeners = @(Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    if ([int]$listener.OwningProcess -notin $owned) { throw 'Port 8080 belongs to an unrecognized process.' }
}
foreach ($processId in $owned) {
    if (Get-Process -Id $processId -ErrorAction SilentlyContinue) { Stop-Process -Id $processId -Force -ErrorAction Stop }
}
for ($i = 0; $i -lt 100; $i++) {
    if (-not @(Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue).Count) { exit 0 }
    Start-Sleep -Milliseconds 200
}
throw 'The NoneBot port did not close.'
