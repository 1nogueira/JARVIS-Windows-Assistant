[CmdletBinding()]
param(
    [switch]$InstallSystemDependencies,
    [switch]$InstallVoice,
    [switch]$InstallBrowser = $true
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot

function Find-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { return $launcher.Source }
    return $null
}

if ($InstallSystemDependencies) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { throw 'winget not found. Install Python 3.11+, Node.js LTS, Rust, Ollama, FFmpeg, and Docker Desktop manually.' }
    $packages = @(
        'Python.Python.3.12',
        'OpenJS.NodeJS.LTS',
        'Rustlang.Rustup',
        'Ollama.Ollama',
        'Gyan.FFmpeg',
        'Docker.DockerDesktop'
    )
    foreach ($package in $packages) {
        Write-Host "Checking $package..." -ForegroundColor Cyan
        winget install --id $package --exact --accept-package-agreements --accept-source-agreements --silent
    }
    winget install --id Microsoft.VisualStudio.2022.BuildTools --exact --accept-package-agreements --accept-source-agreements --override '--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'
    Write-Host 'System dependencies installed. Open a new terminal, then run setup.ps1 again.' -ForegroundColor Yellow
    exit 0
}

$pythonCommand = Find-Python
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$npmCommand = Get-Command npm -ErrorAction SilentlyContinue
$cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue

if (-not $pythonCommand -or -not $nodeCommand -or -not $npmCommand) {
    Write-Host 'Required dependencies are missing.' -ForegroundColor Red
    Write-Host 'Run: .\setup.ps1 -InstallSystemDependencies' -ForegroundColor Yellow
    Write-Host 'Or install Python 3.11+, Node.js LTS, and Rust manually.'
    exit 1
}

$settingsPath = Join-Path $projectRoot 'config\settings.json'
$settingsExamplePath = Join-Path $projectRoot 'config\settings.example.json'
if (-not (Test-Path -LiteralPath $settingsPath)) {
    Copy-Item -LiteralPath $settingsExamplePath -Destination $settingsPath
}

$venvPath = Join-Path $projectRoot '.venv'
if (-not (Test-Path -LiteralPath $venvPath)) {
    if ((Split-Path -Leaf $pythonCommand) -eq 'py.exe') { & $pythonCommand -3 -m venv $venvPath }
    else { & $pythonCommand -m venv $venvPath }
}
$venvPython = Join-Path $venvPath 'Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
$wakewordDownload = 'from openwakeword.utils import download_models; download_models()'
& $venvPython -c $wakewordDownload
if ($LASTEXITCODE -ne 0) { Write-Host 'openWakeWord models could not be downloaded; retry when an internet connection is available.' -ForegroundColor Yellow }

Push-Location (Join-Path $projectRoot 'frontend')
try {
    & $npmCommand.Source install
    if ($LASTEXITCODE -ne 0) { throw 'npm install failed.' }
    if ($InstallBrowser) { & $venvPython -m playwright install chromium }
    if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium installation failed.' }
} finally { Pop-Location }

New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'data'), (Join-Path $projectRoot 'logs') | Out-Null
if ($InstallVoice) { & (Join-Path $projectRoot 'scripts\install-voice.ps1') }

Write-Host ''
Write-Host 'JARVIS is ready.' -ForegroundColor Green
Write-Host "Rust/Tauri: $([bool]$cargoCommand)  Ollama: $([bool]$ollamaCommand)"
if (-not $cargoCommand) { Write-Host 'Install Rust to run the Tauri window; web mode is available without Rust.' -ForegroundColor Yellow }
if (-not $ollamaCommand) { Write-Host 'Install Ollama and download a model: ollama pull qwen3:4b' -ForegroundColor Yellow }
Write-Host 'Start with: .\start.ps1'
