$ErrorActionPreference = 'Stop'
try {
    $architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
    if ($architecture -notin @('x64', 'arm64')) {
        throw 'LAUNCH-PLATFORM: This processor is not supported.'
    }
    $launcher = Join-Path $PSScriptRoot "app/launcher/bin/windows-$architecture/launcher.exe"
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        throw 'LAUNCH-ROOT: Extract the complete Job Finder script ZIP, including its app folder.'
    }
    $env:GOOSNAV_RELEASE_ROOT = $PSScriptRoot
    & $launcher
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
