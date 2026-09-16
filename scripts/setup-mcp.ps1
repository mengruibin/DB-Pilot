# setup-mcp.ps1 - DB-Pilot MCP local setup (Windows)
# Usage: pwsh -ExecutionPolicy Bypass -File scripts\setup-mcp.ps1
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'

Write-Host '== DB-Pilot MCP setup =='

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host '[ERROR] uv not found. Install: https://docs.astral.sh/uv/' -ForegroundColor Red
    exit 1
}
Write-Host ('[OK] uv: ' + (uv --version))

python --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] python not found on PATH' -ForegroundColor Red
    exit 1
}
$ver = (python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")').Trim()
$parts = $ver.Split('.')
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 12)) {
    Write-Host "[ERROR] python >= 3.12 required, found $ver" -ForegroundColor Red
    exit 1
}
Write-Host ('[OK] python: ' + $ver)

Push-Location $backendDir
try {
    uv sync --extra mcp
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[ERROR] uv sync failed' -ForegroundColor Red
        exit 1
    }
} finally { Pop-Location }
Write-Host '[OK] dependencies synced'

$envFile = Join-Path $backendDir '.env'
$envExample = Join-Path $backendDir '.env.example'
if (-not (Test-Path $envFile)) {
    Copy-Item $envExample $envFile
    Write-Host '[OK] created backend/.env from .env.example'
} else {
    Write-Host '[OK] backend/.env already exists'
}

Push-Location $backendDir
try {
    $count = (uv run python -c "from app.mcp_server.schema import TOOL_META; print(len(TOOL_META))").Trim()
    Write-Host ('[OK] mcp package loads; tools: ' + $count)
} finally { Pop-Location }

Write-Host ''
Write-Host 'Next steps:'
Write-Host '  1) Edit backend/.env, add your DSN:'
Write-Host '     DBPILOT_DSN=mysql://user:pass@127.0.0.1:3306/shop   (single)'
Write-Host '     DBPILOT_CONNECTIONS={...}                          (multi)'
Write-Host '     DBPILOT_HEADLESS=1'
Write-Host '  2) Register in Claude Code/Codex (see docs/mcp-usage.md):'
Write-Host '     project .mcp.json  or  claude mcp add db-pilot ...'
Write-Host '  3) Verify: ask "dbpilot_list_connections to list databases"'
