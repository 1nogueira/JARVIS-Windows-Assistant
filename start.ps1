[CmdletBinding()]
param([switch]$Web)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Python environment not found. Run .\setup.ps1 first.' }
$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm) { throw 'npm not found. Run .\setup.ps1 -InstallSystemDependencies.' }
$expectedVersion = (Get-Content -LiteralPath (Join-Path $projectRoot 'frontend\src-tauri\tauri.conf.json') -Raw | ConvertFrom-Json).version

function Test-JarvisOwnedProcess {
    param(
        [Parameter(Mandatory)][int]$CandidateProcessId,
        [Parameter(Mandatory)][int]$RootProcessId
    )
    $currentProcessId = $CandidateProcessId
    for ($depth = 0; $depth -lt 8; $depth++) {
        if ($currentProcessId -eq $RootProcessId) { return $true }
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $currentProcessId" -ErrorAction SilentlyContinue
        if (-not $processInfo -or [int]$processInfo.ParentProcessId -le 0) { return $false }
        $currentProcessId = [int]$processInfo.ParentProcessId
    }
    return $false
}

$sessionBytes = New-Object byte[] 48
$random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $random.GetBytes($sessionBytes) } finally { $random.Dispose() }
$sessionToken = [Convert]::ToBase64String($sessionBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$previousSessionToken = $env:JARVIS_SESSION_TOKEN
$env:JARVIS_SESSION_TOKEN = $sessionToken
$backend = Start-Process -FilePath $python -ArgumentList '-m', 'backend.main' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
$ownedBackendProcessId = $null
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $headers = @{ Authorization = "Bearer $sessionToken" }
            $identity = Invoke-RestMethod -Uri 'http://127.0.0.1:8742/api/identity' -Headers $headers -TimeoutSec 1
            $candidateProcessId = [int]$identity.pid
            if ($identity.name -ne 'JARVIS' -or $identity.version -ne $expectedVersion) {
                throw 'Unexpected backend identity or version.'
            }
            if ([string]::IsNullOrWhiteSpace([string]$identity.instance_id)) {
                throw 'Backend identity nonce is missing.'
            }
            if (-not (Test-JarvisOwnedProcess -CandidateProcessId $candidateProcessId -RootProcessId $backend.Id)) {
                throw 'Authenticated PID does not belong to the process started by this script.'
            }
            $ownedBackendProcessId = $candidateProcessId
            $ready = $true
            break
        } catch { Start-Sleep -Milliseconds 300 }
    }
    if (-not $ready) { throw 'Backend did not respond. Check the logs and run .\.venv\Scripts\python.exe -m backend.main for diagnostics.' }
    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        if ($Web) {
            Write-Host "Web mode: open http://127.0.0.1:1420/#jarvis_token=$sessionToken" -ForegroundColor Cyan
            & $npm.Source run dev
        }
        else { & $npm.Source run tauri dev }
    } finally { Pop-Location }
} finally {
    if ($ownedBackendProcessId -and (Get-Process -Id $ownedBackendProcessId -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $ownedBackendProcessId
    }
    if (-not $backend.HasExited) { Stop-Process -Id $backend.Id }
    if ($null -eq $previousSessionToken) { Remove-Item Env:JARVIS_SESSION_TOKEN -ErrorAction SilentlyContinue }
    else { $env:JARVIS_SESSION_TOKEN = $previousSessionToken }
}
