$ErrorActionPreference = 'Stop'
$compose = Join-Path $PSScriptRoot 'searxng.compose.yml'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker not found. Install Docker Desktop or configure another SearXNG instance in config/settings.json.'
}
docker compose -f $compose up -d
Write-Host 'SearXNG is available at http://127.0.0.1:8080' -ForegroundColor Green
