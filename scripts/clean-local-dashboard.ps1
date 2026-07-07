#!/usr/bin/env pwsh
#
# Reset the local dashboard dev loop to a fresh slate (Windows / PowerShell).
#
# Windows counterpart to scripts/clean-local-dashboard.sh. Removes:
#   - the dev compose stack and its named volumes (arcadedb data/log)
#   - .dev-state\ (tokens, credentials, registry, bifrost config)
#   - any loreholm-arcadedb-* containers and volumes the dashboard/wizard may
#     have spun up during iteration
#
# Re-running scripts\dev-local-dashboard.ps1 after this gives you a pristine
# environment with the `dev` database pre-registered again.
#
# Usage:
#   scripts\clean-local-dashboard.ps1          # prompts for confirmation
#   scripts\clean-local-dashboard.ps1 -Yes     # skip the prompt

[CmdletBinding()]
param(
    [Alias('y')]
    [switch]$Yes,
    [Alias('h')]
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

$RepoRoot    = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DevState    = Join-Path $RepoRoot '.dev-state'
$ComposeFile = Join-Path $RepoRoot 'deploy\docker-compose.dev.yml'

function Blue($msg)  { Write-Host "[clean-dev] $msg" -ForegroundColor Blue }
function Green($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "[!] $msg" -ForegroundColor Yellow }

if ($Help) {
    Write-Host @'
Reset the local dashboard dev loop to a fresh slate.

Removes:
  - the dev compose stack and its named volumes (arcadedb data/log)
  - .dev-state\ (tokens, credentials, registry, bifrost config)
  - any loreholm-arcadedb-* containers/volumes (wizard-created test DBs)

Usage:
  scripts\clean-local-dashboard.ps1          # prompts for confirmation
  scripts\clean-local-dashboard.ps1 -Yes     # skip the prompt
'@
    exit 0
}

# ---------- confirm ----------
if (-not $Yes) {
    Write-Host ''
    Warn 'This will delete:'
    Warn "  - dev containers + volumes from $ComposeFile"
    Warn '  - any loreholm-arcadedb-* containers/volumes (wizard-created test DBs)'
    Warn "  - $DevState (tokens, credentials, registry, bifrost config)"
    Write-Host ''
    $answer = Read-Host 'Proceed? [y/N]'
    if ($answer -notmatch '^(y|yes)$') {
        Write-Host 'Aborted.'
        exit 0
    }
}

# ---------- dev compose stack ----------
$dockerOk = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker compose version *> $null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
}

if ($dockerOk) {
    if (Test-Path $ComposeFile) {
        Blue 'Tearing down dev compose stack...'
        & docker compose -f $ComposeFile down --volumes --remove-orphans
        Green 'Dev compose stack removed'
    }

    # ---------- extra wizard-created arcadedb containers ----------
    # Anything matching loreholm-arcadedb-* that wasn't owned by compose above —
    # these come from dashboard create-database flows during iteration.
    $extraContainers = & docker ps -a --filter 'name=^loreholm-arcadedb-' --format '{{.Names}}'
    $extraContainers = @($extraContainers | Where-Object { $_ })
    if ($extraContainers.Count -gt 0) {
        Blue 'Removing leftover arcadedb containers:'
        foreach ($name in $extraContainers) {
            Write-Host "  - $name"
            & docker rm -f $name *> $null
        }
    }

    # Matching named volumes (dashboard names them <container>-data / -log).
    $extraVolumes = & docker volume ls --filter 'name=^loreholm-arcadedb-' --format '{{.Name}}'
    $extraVolumes = @($extraVolumes | Where-Object { $_ })
    if ($extraVolumes.Count -gt 0) {
        Blue 'Removing leftover arcadedb volumes:'
        foreach ($vol in $extraVolumes) {
            Write-Host "  - $vol"
            & docker volume rm $vol *> $null
        }
    }
} else {
    Warn 'docker / docker compose not available — skipping container cleanup.'
}

# ---------- .dev-state\ ----------
if (Test-Path $DevState) {
    Blue "Removing $DevState ..."
    Remove-Item -Recurse -Force $DevState
    Green '.dev-state\ removed'
}

Write-Host ''
Green 'Clean slate. Run scripts\dev-local-dashboard.ps1 to rebuild.'
