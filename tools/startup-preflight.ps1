# ASCII-only: Windows PowerShell 5.1 may misread UTF-8 without a BOM.
[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$SnowLumaPath = '',
    [string]$IndexUrl = '',
    [switch]$NoPause,
    [switch]$SkipInstall,
    [switch]$CheckOnly,
    [switch]$IndexProbeOnly
)

$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$rootPath = [IO.Path]::GetFullPath($Root)
$botDir = Join-Path $rootPath 'nonebot'
$venvDir = Join-Path $botDir '.venv'
$python = Join-Path $venvDir 'Scripts\python.exe'
$requirements = Join-Path $rootPath 'requirements.lock'
$parserRequirements = Join-Path $botDir 'media_parser\requirements.txt'
$ragIndex = Join-Path $rootPath 'rag\index-siliconflow'
if ($env:KB_CLOUD_INDEX) {
    $ragIndex = $env:KB_CLOUD_INDEX
    if (-not [IO.Path]::IsPathRooted($ragIndex)) {
        $ragIndex = Join-Path $rootPath $ragIndex
    }
}
$ragKeyFile = Join-Path $rootPath 'nonebot\data\embedding_secrets.json'
$ragPython = Join-Path $rootPath 'rag\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ragPython -PathType Leaf)) {
    $ragPython = Join-Path $rootPath 'nonebot\.venv\Scripts\python.exe'
}
$gsuidPython = Join-Path $rootPath 'gsuid\core\.venv\Scripts\python.exe'
$pluginSettings = Join-Path $botDir 'data\plugins.json'
$bundledPythonVersion = '3.12.13'
$bundledPythonUrl = 'https://github.com/astral-sh/python-build-standalone/releases/download/20260510/cpython-3.12.13%2B20260510-x86_64-pc-windows-msvc-install_only_stripped.tar.gz'
$bundledPythonSha256 = '24168aff2e7d93784c6a436124c4ebb79b076a4e289bde4902c08333507b71d0'
$bundledRuntimeDir = Join-Path $rootPath 'runtime'
$bundledPythonDir = Join-Path $bundledRuntimeDir 'python312'
$bundledPythonExe = Join-Path $bundledPythonDir 'python.exe'

function Write-Section($Message) {
    Write-Host "[deps] $Message" -ForegroundColor Cyan
}

function Write-Ok($Message) {
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Warn($Message) {
    Write-Host "  [--] $Message" -ForegroundColor Yellow
}

function Stop-Check($Message) {
    Write-Host "  [!!] $Message" -ForegroundColor Red
    if (-not $NoPause -and -not $env:QQBOT_NO_PAUSE -and [Environment]::UserInteractive) {
        Write-Host ''
        [void](Read-Host 'Press Enter to close')
    }
    exit 1
}

function Protect-PipUrl([string]$Value) {
    if (-not $Value) { return '' }
    try {
        $uri = [Uri]$Value
        if ($uri.UserInfo) {
            $builder = New-Object System.UriBuilder -ArgumentList $uri
            $builder.UserName = ''
            $builder.Password = ''
            return $builder.Uri.AbsoluteUri
        }
    } catch {
    }
    return [regex]::Replace($Value, '//[^/@\s]+@', '//***@')
}

function Get-PipIndexLines {
    $lines = New-Object System.Collections.Generic.List[string]
    if ($IndexUrl) {
        $lines.Add('one-off -IndexUrl: ' + (Protect-PipUrl $IndexUrl))
    }
    if ($env:PIP_INDEX_URL) {
        $lines.Add('PIP_INDEX_URL: ' + (Protect-PipUrl $env:PIP_INDEX_URL))
    }
    if ($env:PIP_EXTRA_INDEX_URL) {
        $lines.Add('PIP_EXTRA_INDEX_URL: ' + (Protect-PipUrl $env:PIP_EXTRA_INDEX_URL))
    }
    if ($env:HTTPS_PROXY) {
        $lines.Add('HTTPS_PROXY: ' + (Protect-PipUrl $env:HTTPS_PROXY))
    }
    if ($env:HTTP_PROXY) {
        $lines.Add('HTTP_PROXY: ' + (Protect-PipUrl $env:HTTP_PROXY))
    }
    if ($env:NO_PROXY) {
        $lines.Add('NO_PROXY: ' + (Protect-PipUrl $env:NO_PROXY))
    }
    if (Test-Path -LiteralPath $python -PathType Leaf) {
        $config = @(& $python -m pip config list 2>&1)
        $configExit = $LASTEXITCODE
        if ($configExit -eq 0) {
            foreach ($line in $config) {
                $text = ([string]$line).Trim()
                if ($text -and $text -match '(?i)(index|proxy)') {
                    $lines.Add('pip config: ' + (Protect-PipUrl $text))
                }
            }
        }
    }
    if ($lines.Count -eq 0) {
        $lines.Add('index: https://pypi.org/simple (pip default)')
    }
    return $lines.ToArray()
}

function Write-PipIndex {
    Write-Section 'pip index in use:'
    foreach ($line in @(Get-PipIndexLines)) {
        Write-Host "  $line"
    }
}

function Invoke-PipInstall {
    Write-Section 'Installing/repairing requirements.lock and media parser requirements; no service starts until this completes ...'
    Write-PipIndex
    $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-r', $requirements)
    if (Test-Path -LiteralPath $parserRequirements -PathType Leaf) {
        $pipArgs += @('-r', $parserRequirements)
    }
    if ($IndexUrl) {
        $pipArgs += @('--index-url', $IndexUrl)
    }
    & $python @pipArgs
    if ($LASTEXITCODE -ne 0) {
        $hint = @(
            "pip install failed (exit $LASTEXITCODE); no service was started.",
            'pip index in use:'
        )
        $hint += @(Get-PipIndexLines | ForEach-Object { "  $_" })
        $hint += @(
            '',
            'If this is a network/index problem, set PIP_INDEX_URL (for example https://pypi.tuna.tsinghua.edu.cn/simple) or fix the proxy, then retry.',
            'One-off: powershell -NoProfile -ExecutionPolicy Bypass -File tools\startup-preflight.ps1 -IndexUrl <url>'
        )
        Stop-Check ($hint -join "`n")
    }
    Set-Content -LiteralPath $marker -Value $requirementsHash -NoNewline -Encoding ascii
}

if ($IndexProbeOnly) {
    Write-PipIndex
    exit 0
}

trap {
    Write-Host "  [!!] Dependency check failed: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $NoPause -and -not $env:QQBOT_NO_PAUSE -and [Environment]::UserInteractive) {
        Write-Host ''
        [void](Read-Host 'Press Enter to close')
    }
    exit 1
}

function Test-Python312([string]$Exe) {
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
        return $false
    }
    try {
        $version = & $Exe -I -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>$null
        return ($LASTEXITCODE -eq 0 -and ($version -join "`n").Trim() -eq '3.12')
    } catch {
        return $false
    }
}

function Test-X64Windows {
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) {
        $arch = $env:PROCESSOR_ARCHITEW6432
    }
    return ([Environment]::Is64BitOperatingSystem -and $arch -eq 'AMD64')
}

function Get-BundledPython {
    if (-not (Test-Path -LiteralPath $bundledPythonExe)) {
        return $null
    }
    if (-not (Test-X64Windows)) {
        Stop-Check 'The pinned Python runtime requires x64 Windows; ARM64 and 32-bit Windows are unsupported.'
    }
    if (-not (Test-Python312 $bundledPythonExe)) {
        Stop-Check "Bundled Python runtime is invalid: $bundledPythonExe`nDelete runtime\python312 and retry."
    }
    return $bundledPythonExe
}

function Install-BundledPython {
    if (-not (Test-X64Windows)) {
        Stop-Check 'The pinned Python runtime requires x64 Windows; ARM64 and 32-bit Windows are unsupported.'
    }
    $tarExe = Join-Path $env:SystemRoot 'System32\tar.exe'
    if (-not (Test-Path -LiteralPath $tarExe -PathType Leaf)) {
        $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
        if (-not $tar) {
            Stop-Check 'tar.exe is required to extract the pinned Python runtime.'
        }
        $tarExe = $tar.Source
    }

    $tempDir = Join-Path $bundledRuntimeDir ('.tmp-python312-' + [guid]::NewGuid())
    $archive = Join-Path $tempDir 'python312.tar.gz'
    $extractDir = Join-Path $tempDir 'extract'
    try {
        try {
            [void][IO.Directory]::CreateDirectory($extractDir)
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $ProgressPreference = 'SilentlyContinue'
            $request = @{}
            if ($env:HTTPS_PROXY) {
                $request.Proxy = $env:HTTPS_PROXY
            } elseif ($env:HTTP_PROXY) {
                $request.Proxy = $env:HTTP_PROXY
            }
            Write-Section "Downloading Python $bundledPythonVersion ..."
            try {
                Invoke-WebRequest -Uri $bundledPythonUrl -UseBasicParsing -OutFile $archive @request -ErrorAction Stop
            } catch {
                throw "Failed to download the pinned Python 3.12 runtime.`nURL: $bundledPythonUrl`n$($_.Exception.Message)`nCheck network or proxy access to GitHub and retry."
            }
            $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
            if ($hash -ne $bundledPythonSha256) {
                throw "Python runtime SHA256 mismatch: expected $bundledPythonSha256, got $hash."
            }
            Write-Ok "Python runtime SHA256: $hash"
            Write-Section 'Extracting the pinned Python runtime ...'
            & $tarExe -xzf $archive -C $extractDir --strip-components=1
            if ($LASTEXITCODE -ne 0) {
                throw "tar.exe could not extract the Python runtime (exit $LASTEXITCODE)."
            }
            if (-not (Test-Python312 (Join-Path $extractDir 'python.exe'))) {
                throw 'Extracted runtime does not provide a working Python 3.12.'
            }
            try {
                [IO.Directory]::Move($extractDir, $bundledPythonDir)
            } catch {
                if (-not (Test-Python312 $bundledPythonExe)) {
                    throw "Bundled Python directory already exists but is not usable: $bundledPythonDir`nDelete it and retry."
                }
                Write-Ok "Reusing Python runtime published by another launch: $bundledPythonExe"
                return $bundledPythonExe
            }
            if (-not (Test-Python312 $bundledPythonExe)) {
                throw "Bundled Python runtime is invalid: $bundledPythonExe`nDelete runtime\python312 and retry."
            }
        } finally {
            if (Test-Path -LiteralPath $tempDir) {
                Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction Stop
            }
        }
    } catch {
        Stop-Check $_.Exception.Message
    }
    Write-Ok "Python runtime: $bundledPythonExe"
    return $bundledPythonExe
}

Write-Host '==========================================' -ForegroundColor Cyan
Write-Host '  Startup dependency preflight' -ForegroundColor Cyan
Write-Host '==========================================' -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath (Join-Path $botDir 'bot.py') -PathType Leaf)) {
    Stop-Check "Not a valid checkout: missing $botDir\bot.py"
}
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    Stop-Check "Missing dependency lock: $requirements"
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    if ($CheckOnly) {
        Stop-Check "NoneBot virtual environment is missing: $python"
    }
    $hostPython = Get-BundledPython
    if (-not $hostPython) {
        if ($SkipInstall) {
            Stop-Check 'Python 3.12 is unavailable, and -SkipInstall prevents the runtime download. Rerun without -SkipInstall to download the pinned runtime.'
        }
        Write-Section 'No bundled Python 3.12 runtime found; downloading the pinned python-build-standalone runtime ...'
        $hostPython = Install-BundledPython
    }
    & $hostPython -m venv $venvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Stop-Check "Could not create the NoneBot venv with $hostPython (exit $LASTEXITCODE)."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $venvDir 'pyvenv.cfg') -PathType Leaf)) {
    Stop-Check "NoneBot venv is incomplete (missing pyvenv.cfg): $venvDir"
}
$venvVersion = (& $python -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>&1) -join "`n"
$venvVersion = $venvVersion.Trim()
if ($LASTEXITCODE -ne 0) {
    Stop-Check "NoneBot Python cannot run (exit $LASTEXITCODE): $python`n$venvVersion"
}
if ($venvVersion -ne '3.12') {
    Stop-Check "NoneBot venv uses Python $venvVersion (requires 3.12): $venvDir`nRecreate it from the repo root: Remove-Item -Recurse -Force nonebot\.venv; py -3.12 -m venv nonebot\.venv"
}
Write-Ok "NoneBot Python: $python"

$snowHelper = Join-Path $PSScriptRoot 'snowluma-path.ps1'
if (-not (Test-Path -LiteralPath $snowHelper -PathType Leaf)) {
    Stop-Check "Missing SnowLuma path resolver: $snowHelper"
}
$snowArgs = @{ Root = $rootPath; SnowLumaPath = $SnowLumaPath }
if ($CheckOnly -or $SkipInstall) { $snowArgs['NoExtract'] = $true }
$snowHome = (& $snowHelper @snowArgs)
if ($LASTEXITCODE -ne 0 -or -not $snowHome) {
    Stop-Check 'SnowLuma runtime is missing. Set SNOWLUMA_HOME (or QQBOT_SNOWLUMA), pass -SnowLumaPath, or place SnowLuma-v2.4.2-win-x64.zip in the repo root/downloads.'
}
$snowHome = [string]($snowHome | Select-Object -Last 1)
$snowHome = $snowHome.Trim()
$snowNode = Join-Path $snowHome 'node.exe'
$snowEntry = Join-Path $snowHome 'index.mjs'
if (-not (Test-Path -LiteralPath $snowNode -PathType Leaf)) {
    Stop-Check "SnowLuma runtime is missing: $snowNode"
}
if (-not (Test-Path -LiteralPath $snowEntry -PathType Leaf)) {
    Stop-Check "SnowLuma entry point is missing: $snowEntry"
}
Write-Ok "SnowLuma runtime: $snowNode"

if ($CheckOnly) {
    Write-Ok 'File checks passed (CheckOnly; pip/import were not run)'
    exit 0
}

& $python -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    if ($SkipInstall) {
        Stop-Check 'pip is unavailable, and -SkipInstall was requested; no service was started.'
    }
    Write-Section 'pip is unavailable; trying ensurepip ...'
    & $python -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "pip is unavailable (exit $LASTEXITCODE); no service was started."
    }
}
Write-Ok 'pip is available'

$requirementsFiles = @($requirements)
if (Test-Path -LiteralPath $parserRequirements -PathType Leaf) {
    $requirementsFiles += $parserRequirements
}
$requirementsHash = ($requirementsFiles | ForEach-Object {
    (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
}) -join ':'
$marker = Join-Path $venvDir '.qqbot-requirements.sha256'
$markerHash = ''
if (Test-Path -LiteralPath $marker -PathType Leaf) {
    $markerText = Get-Content -LiteralPath $marker -Raw
    if ($null -ne $markerText) {
        $markerHash = $markerText.Trim().ToLowerInvariant()
    }
}
$needsInstall = $markerHash -ne $requirementsHash

if ($needsInstall) {
    if ($SkipInstall) {
        Write-Warn 'dependency requirements changed, but -SkipInstall was requested; checking the existing environment'
    } else {
        Invoke-PipInstall
    }
}

$pipCheck = & $python -m pip check 2>&1
if ($LASTEXITCODE -ne 0) {
    if ($SkipInstall) {
        Stop-Check "pip check failed (-SkipInstall prevents repair):`n$pipCheck"
    }
    Write-Warn 'pip check failed; repairing from requirements.lock ...'
    Invoke-PipInstall
    $pipCheck = & $python -m pip check 2>&1
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "Dependency conflicts remain:`n$pipCheck"
    }
}
Write-Ok 'pip check passed'

function Invoke-ImportCheck {
    Push-Location -LiteralPath $botDir
    try {
        & $python -c "import nonebot, importlib.util; assert importlib.util.find_spec('muicebot') is not None" 2>&1
    } finally {
        Pop-Location
    }
}

$importCheck = Invoke-ImportCheck
if ($LASTEXITCODE -ne 0) {
    if ($SkipInstall) {
        Stop-Check "NoneBot/MuiceBot import check failed (-SkipInstall prevents repair):`n$importCheck"
    }
    Write-Warn 'NoneBot/MuiceBot import check failed; repairing from requirements.lock ...'
    Invoke-PipInstall
    $importCheck = Invoke-ImportCheck
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "NoneBot/MuiceBot still cannot be imported:`n$importCheck"
    }
}
Write-Ok 'nonebot imports; muicebot is installed'

if (-not $SkipInstall) {
    Write-Section 'Ensuring Playwright Chromium for the dashboard Xiaohongshu login ...'
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "Playwright Chromium install failed (exit $LASTEXITCODE); no service was started."
    }
    Write-Ok 'Playwright Chromium is installed'
}

$ragVectors = Join-Path $ragIndex 'vectors.npy'
$ragKeyConfigured = $false
if (Test-Path -LiteralPath $ragKeyFile -PathType Leaf) {
    try {
        $ragSecrets = Get-Content -LiteralPath $ragKeyFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $ragKeyConfigured = [bool]("$($ragSecrets.api_key)".Trim())
    } catch {
        $ragKeyConfigured = $false
    }
}
if ((Test-Path -LiteralPath $ragVectors -PathType Leaf) -and $ragKeyConfigured) {
    if (Test-Path -LiteralPath $ragPython -PathType Leaf) {
        Write-Ok "Optional RAG service: cloud index and SiliconFlow key are ready ($ragIndex)"
    } else {
        Write-Warn "RAG prerequisites exist but no Python environment was found: $ragPython (RAG will be skipped)"
    }
} elseif (-not (Test-Path -LiteralPath $ragVectors -PathType Leaf)) {
    Write-Warn "RAG not enabled (needs SiliconFlow key/index): index missing $ragVectors"
} else {
    Write-Warn "RAG not enabled (needs SiliconFlow key/index): api_key missing in $ragKeyFile"
}

$gsuidEnabled = $true
if (Test-Path -LiteralPath $pluginSettings -PathType Leaf) {
    try {
        $pluginData = Get-Content -LiteralPath $pluginSettings -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($pluginData.disabled -contains 'gsuid') {
            $gsuidEnabled = $false
        }
    } catch {
        Write-Warn "Could not read plugin switches: $pluginSettings (assuming gsuid is enabled)"
    }
}
if (-not $gsuidEnabled) {
    Write-Warn 'Optional gsuid_core is disabled by the plugin manager'
} elseif (Test-Path -LiteralPath $gsuidPython -PathType Leaf) {
    Write-Ok 'Optional gsuid_core: venv is ready'
} else {
    Write-Warn "Optional gsuid_core is not installed: $gsuidPython (gsuid will be skipped)"
}

foreach ($configFile in @((Join-Path $botDir '.env'), (Join-Path $botDir 'configs\models.yml'))) {
    if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
        Write-Warn "Missing configuration: $configFile (the bot may fail to start)"
    }
}

Write-Host ''
Write-Host 'Dependency preflight passed; starting services.' -ForegroundColor Green
exit 0
