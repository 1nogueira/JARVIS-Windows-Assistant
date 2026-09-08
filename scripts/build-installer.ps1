[CmdletBinding()]
param([switch]$BackendOnly)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$frontend = Join-Path $projectRoot 'frontend'
$binaryDirectory = Join-Path $frontend 'src-tauri\binaries'
$voiceDirectory = Join-Path $projectRoot 'data\voice'
$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $env:CARGO_BUILD_JOBS) { $env:CARGO_BUILD_JOBS = '1' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup.ps1 before packaging.' }
if (-not (Test-Path -LiteralPath $voiceDirectory)) { throw 'Run setup.ps1 -InstallVoice before packaging.' }
if (-not $BackendOnly -and -not $npm) { throw 'npm not found.' }
if (-not $BackendOnly -and -not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw 'Rust/Cargo not found. Run setup.ps1 -InstallSystemDependencies.' }
New-Item -ItemType Directory -Force -Path $binaryDirectory | Out-Null

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name 'jarvis-backend-x86_64-pc-windows-msvc' `
    --distpath $binaryDirectory `
    --workpath (Join-Path $projectRoot 'build\pyinstaller') `
    --specpath (Join-Path $projectRoot 'build') `
    --paths $projectRoot `
    --add-data "$(Join-Path $projectRoot 'config');config" `
    --add-data "$voiceDirectory;data\voice" `
    --hidden-import backend.api.app `
    --hidden-import psutil `
    --hidden-import PIL.ImageGrab `
    --hidden-import playwright.async_api `
    --hidden-import pyperclip `
    --hidden-import pycaw.pycaw `
    --hidden-import sounddevice `
    --collect-all openwakeword `
    (Join-Path $projectRoot 'backend\main.py')
if ($LASTEXITCODE -ne 0) { throw 'Backend packaging failed.' }
if ($BackendOnly) {
    Write-Host 'Backend sidecar built successfully.' -ForegroundColor Green
    exit 0
}

Push-Location $frontend
try {
    & $npm.Source run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    # The frontend was already built above.  Disabling Tauri's duplicate
    # beforeBuildCommand avoids a second npm process and keeps packaging
    # deterministic on Windows.
    & $npm.Source run tauri -- build `
        --config 'src-tauri/tauri.bundle.conf.json' `
        --config 'src-tauri/tauri.prebuilt.conf.json'
    if ($LASTEXITCODE -ne 0) { throw 'Tauri build failed.' }
} finally { Pop-Location }

Write-Host 'Installers created in frontend\src-tauri\target\release\bundle' -ForegroundColor Green
