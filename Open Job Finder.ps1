param([switch]$NoPause)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Get-StartupHash($path) {
    $stream = [IO.File]::OpenRead($path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}

function Open-RunningJobFinder($runtimeDirectory) {
    foreach ($name in @('current-instance.json', 'server.json')) {
        try {
            $instance = Get-Content -LiteralPath (Join-Path $runtimeDirectory $name) -Raw | ConvertFrom-Json
            if ($instance.url -notmatch '^http://127\.0\.0\.1:[0-9]{1,5}/?$') { continue }
            $health = Invoke-RestMethod -Uri ($instance.url.TrimEnd('/') + '/health/ready') -TimeoutSec 2
            if ($health.ready -eq $true -and $health.app -eq 'job-finder') {
                Start-Process $instance.url
                return $true
            }
        } catch { }
    }
    return $false
}

$setupLock = $null
$transcriptStarted = $false
$startupLog = $null
try {
    $base = $env:LOCALAPPDATA
    if (-not $base) { $base = [Environment]::GetFolderPath('LocalApplicationData') }
    $dataRoot = Join-Path $base 'JobFinder'
    $logDirectory = Join-Path $dataRoot 'logs'
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    $startupLog = Join-Path $logDirectory ("windows-startup-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + "-$PID.log")
    Start-Transcript -Path $startupLog | Out-Null
    $transcriptStarted = $true
    Write-Host "Startup log: $startupLog"

    $processor = $env:PROCESSOR_ARCHITEW6432
    if (-not $processor) { $processor = $env:PROCESSOR_ARCHITECTURE }
    $architecture = switch ($processor) { 'AMD64' { 'x64' } 'ARM64' { 'arm64' } default { '' } }
    if (-not $architecture) {
        throw 'LAUNCH-PLATFORM: This processor is not supported.'
    }
    $launcher = Join-Path $PSScriptRoot "app/launcher/bin/windows-$architecture/launcher.exe"
    if (Test-Path -LiteralPath $launcher -PathType Leaf) {
        $env:GOOSNAV_RELEASE_ROOT = $PSScriptRoot
        & $launcher
        exit $LASTEXITCODE
    }

    $appRoot = Join-Path $PSScriptRoot 'app'
    foreach ($name in @('pyproject.toml', 'uv.lock', '.python-version', 'run.py', 'launcher/windows-bootstrap.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $appRoot $name) -PathType Leaf)) {
            throw "LAUNCH-ROOT: Missing app/$name. Keep the opener beside the complete app folder."
        }
    }
    $runtimeDirectory = Join-Path $dataRoot 'runtime'
    New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
    if (Open-RunningJobFinder $runtimeDirectory) { exit 0 }

    # Serialize first setup; a second opener waits for the same application.
    $deadline = (Get-Date).AddMinutes(5)
    while (-not $setupLock) {
        try {
            $setupLock = [IO.File]::Open((Join-Path $runtimeDirectory 'source-windows.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch [IO.IOException] {
            if (Open-RunningJobFinder $runtimeDirectory) { exit 0 }
            if ((Get-Date) -gt $deadline) { throw 'BOOT-LOCK: Another window is still preparing Job Finder. Check that window, then retry.' }
            Start-Sleep -Seconds 1
        }
    }
    if (Open-RunningJobFinder $runtimeDirectory) { exit 0 }
    Write-Host 'Preparing Job Finder. First launch needs internet and can take a few minutes.'

    $bootstrap = Get-Content -LiteralPath (Join-Path $appRoot 'launcher/windows-bootstrap.json') -Raw | ConvertFrom-Json
    $target = $bootstrap.targets.$architecture
    if ($bootstrap.uv -notmatch '^\d+\.\d+\.\d+$' -or $target.sha256 -notmatch '^[0-9a-f]{64}$') {
        throw 'BOOT-INTEGRITY: Invalid Windows startup configuration. Update the complete repository.'
    }
    $toolDirectory = Join-Path $dataRoot "tools/uv/$($bootstrap.uv)/windows-$architecture"
    New-Item -ItemType Directory -Force -Path $toolDirectory | Out-Null
    $tool = Join-Path $toolDirectory 'uv.exe'
    if (-not (Test-Path -LiteralPath $tool) -or (Get-StartupHash $tool) -ne $target.sha256) {
        $temporary = Join-Path $toolDirectory ([Guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $temporary | Out-Null
        try {
            Write-Host 'Downloading the private startup tool...'
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            $archive = Join-Path $temporary 'uv.zip'
            $download = "https://github.com/astral-sh/uv/releases/download/$($bootstrap.uv)/uv-$($target.triple).zip"
            try { Invoke-WebRequest -UseBasicParsing -Uri $download -OutFile $archive -TimeoutSec 120 }
            catch { throw 'BOOT-NETWORK: Could not download the startup tool. Check your internet or proxy, then reopen Job Finder.' }
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $zip = [IO.Compression.ZipFile]::OpenRead($archive)
            try {
                $entries = @($zip.Entries | Where-Object { $_.Name -eq 'uv.exe' })
                if ($entries.Count -ne 1 -or $entries[0].Length -gt 100MB) { throw 'BOOT-INTEGRITY: Invalid startup tool archive.' }
                $candidate = Join-Path $temporary 'uv.exe'
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0], $candidate)
            } finally { $zip.Dispose() }
            if ((Get-StartupHash $candidate) -ne $target.sha256) {
                throw 'BOOT-INTEGRITY: Startup tool verification failed. No downloaded code was run.'
            }
            Move-Item -LiteralPath $candidate -Destination $tool -Force
        } finally { Remove-Item -LiteralPath $temporary -Recurse -Force }
    }

    $hashes = @('pyproject.toml', 'uv.lock', '.python-version') | ForEach-Object { Get-StartupHash (Join-Path $appRoot $_) }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $fingerprint = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($hashes -join ':'))))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $dataRoot "runtimes/source-windows-$architecture-$fingerprint"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $dataRoot 'python'
    $env:UV_CACHE_DIR = Join-Path $dataRoot 'cache/uv'
    $env:GOOSNAV_APP_DATA = $dataRoot
    $env:GOOSNAV_RUNTIME_STATE = Join-Path $runtimeDirectory 'current-instance.json'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $python = (Get-Content -LiteralPath (Join-Path $appRoot '.python-version') -Raw).Trim()
    Write-Host 'Opening Job Finder. Keep this window open; use Quit Job Finder in the app to stop it.'
    & $tool run --project $appRoot --locked --no-dev --managed-python --no-build --python $python python (Join-Path $appRoot 'run.py') gui
    if ($LASTEXITCODE -ne 0) { throw 'APP-START: Job Finder could not start. Check the setup error above, then reopen it to retry.' }
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($startupLog) { Write-Host "Startup details were saved to: $startupLog" }
    Write-Host 'Double-click Open Job Finder.cmd for the Windows launch path. It does not change your system execution policy.'
    if (-not $NoPause -and [Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        Read-Host 'Press Enter to close'
    }
    exit 1
} finally {
    if ($setupLock) { $setupLock.Dispose() }
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}
