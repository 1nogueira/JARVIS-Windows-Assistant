[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script as an administrator.'
}

Write-Host 'Enabling Virtual Machine Platform...' -ForegroundColor Cyan
Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart | Out-Null

Write-Host 'Enabling Windows Subsystem for Linux...' -ForegroundColor Cyan
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart | Out-Null

& bcdedit.exe /set hypervisorlaunchtype auto | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not configure hypervisor startup.' }

Write-Host 'Docker features enabled. Restart Windows before starting Docker Desktop.' -ForegroundColor Green
