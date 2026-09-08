[CmdletBinding()]
param(
    [ValidateSet('tiny', 'base', 'small')]
    [string]$WhisperModel = 'base'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$voiceRoot = Join-Path $projectRoot 'data\voice'
$whisperRoot = Join-Path $voiceRoot 'whisper'
$piperRoot = Join-Path $voiceRoot 'piper'
New-Item -ItemType Directory -Force -Path $whisperRoot, $piperRoot | Out-Null

Write-Host 'Downloading whisper.cpp for Windows...' -ForegroundColor Cyan
$release = Invoke-RestMethod -Uri 'https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest'
$asset = $release.assets | Where-Object { $_.name -match 'whisper-bin-x64\.zip$' } | Select-Object -First 1
if (-not $asset) { throw 'The latest whisper.cpp release does not include the expected x64 binary.' }
$whisperZip = Join-Path $env:TEMP 'jarvis-whisper.zip'
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $whisperZip
Expand-Archive -LiteralPath $whisperZip -DestinationPath $whisperRoot -Force
Remove-Item -LiteralPath $whisperZip -Force

$modelPath = Join-Path $whisperRoot "ggml-$WhisperModel.bin"
if (-not (Test-Path -LiteralPath $modelPath)) {
    Invoke-WebRequest -Uri "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$WhisperModel.bin" -OutFile $modelPath
}

Write-Host 'Downloading Piper for Windows...' -ForegroundColor Cyan
$piperRelease = Invoke-RestMethod -Uri 'https://api.github.com/repos/rhasspy/piper/releases/latest'
$piperAsset = $piperRelease.assets | Where-Object { $_.name -eq 'piper_windows_amd64.zip' } | Select-Object -First 1
if (-not $piperAsset) { throw 'The Piper x64 binary was not found.' }
$piperZip = Join-Path $env:TEMP 'jarvis-piper.zip'
Invoke-WebRequest -Uri $piperAsset.browser_download_url -OutFile $piperZip
Expand-Archive -LiteralPath $piperZip -DestinationPath $piperRoot -Force
Remove-Item -LiteralPath $piperZip -Force

$voiceBase = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium'
$voiceModel = Join-Path $piperRoot 'pt_BR-faber-medium.onnx'
$voiceConfig = "$voiceModel.json"
Invoke-WebRequest -Uri "$voiceBase/pt_BR-faber-medium.onnx" -OutFile $voiceModel
Invoke-WebRequest -Uri "$voiceBase/pt_BR-faber-medium.onnx.json" -OutFile $voiceConfig

$settingsPath = Join-Path $projectRoot 'config\settings.json'
$settings = Get-Content -Raw -LiteralPath $settingsPath | ConvertFrom-Json
$whisperExecutable = Get-ChildItem -LiteralPath $whisperRoot -Filter 'whisper-cli.exe' -Recurse | Select-Object -First 1
$piperExecutable = Get-ChildItem -LiteralPath $piperRoot -Filter 'piper.exe' -Recurse | Select-Object -First 1
if (-not $whisperExecutable -or -not $piperExecutable) { throw 'Voice executables were not found after extraction.' }
$settings.voice.stt_executable = $whisperExecutable.FullName
$settings.voice.stt_model = $modelPath
$settings.voice.piper_executable = $piperExecutable.FullName
$settings.voice.piper_model = $voiceModel
$settings | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $settingsPath -Encoding utf8
Write-Host 'Local voice components installed and configured.' -ForegroundColor Green
