#!/usr/bin/env pwsh
#
# Dev loop for the local dashboard (Windows / PowerShell).
#
# Windows counterpart to scripts/dev-local-dashboard.sh. Same behavior:
# - Bootstraps .\venv and installs api/requirements-local-dashboard.txt on first
#   run so the dev loop is a single command (idempotent — re-run safely).
# - Seeds .dev-state\ with the token / config / credential files the dashboard
#   expects.
# - Brings up deploy/docker-compose.dev.yml (arcadedb + bifrost) so wizard,
#   query, and chat features actually work.
# - Runs uvicorn with --reload pointing at the source tree, so edits to
#   api/app/local_dashboard/ (static or Python) show up instantly.
# - Pre-seeds a dev session via LOCAL_DASHBOARD_DEV_MODE + /dev/login, so you
#   never have to go through the token handshake or account setup.
#
# Usage:
#   scripts\dev-local-dashboard.ps1
# Then open http://127.0.0.1:4466/dev/login once to set the session cookie.

$ErrorActionPreference = 'Stop'

$RepoRoot    = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DevState    = Join-Path $RepoRoot '.dev-state'
$VenvDir     = Join-Path $RepoRoot 'venv'
$VenvPy      = Join-Path $VenvDir 'Scripts\python.exe'
$VenvPip     = Join-Path $VenvDir 'Scripts\pip.exe'
$VenvUvicorn = Join-Path $VenvDir 'Scripts\uvicorn.exe'
$ComposeFile = Join-Path $RepoRoot 'deploy\docker-compose.dev.yml'

function Blue($msg)  { Write-Host "[dev-dashboard] $msg" -ForegroundColor Blue }
function Green($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Red($msg)   { Write-Host "[X] $msg" -ForegroundColor Red }

# ---------- sanity checks ----------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Red 'docker CLI not found on PATH.'
    exit 1
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Red 'docker compose plugin not available.'
    exit 1
}

# ---------- bootstrap the venv ----------
# Create .\venv and install the dashboard requirements on first run so the dev
# loop is a single command. Idempotent — re-running only installs what's missing.
if (-not (Test-Path $VenvPy)) {
    # Prefer the py launcher (standard on Windows); fall back to python.
    Blue "Creating Python venv at $VenvDir..."
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $VenvDir
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv $VenvDir
    } else {
        Red 'No Python interpreter found (need py or python on PATH).'
        exit 1
    }
    & $VenvPip install --quiet --upgrade pip
    Green 'venv created'
}

# ---------- install missing python deps into venv ----------
$RequirementsFile = Join-Path $RepoRoot 'api\requirements-local-dashboard.txt'
& $VenvPy -c 'import fastapi, docker' *> $null
if ($LASTEXITCODE -ne 0) {
    Blue 'Installing dashboard requirements into venv...'
    & $VenvPip install --quiet -r $RequirementsFile
    Green 'dashboard requirements installed'
}

# ---------- seed .dev-state\ ----------
New-Item -ItemType Directory -Force -Path $DevState | Out-Null

$DevTokenFile              = Join-Path $DevState 'local-dashboard.token'
$DevSyncTokenFile          = Join-Path $DevState 'local-sync.token'
$DevApiKeyFile             = Join-Path $DevState 'local-api.token'
$DevRegistryFile           = Join-Path $DevState 'databases.json'
$DevKeysFile               = Join-Path $DevState 'dashboard-api-keys.json'
$DevCredsFile              = Join-Path $DevState 'dashboard-credentials.json'
$DevPrefsFile              = Join-Path $DevState 'dashboard-preferences.json'
$DevBifrostConfig          = Join-Path $DevState 'chat-bifrost-config.json'
$DevArcadedbRootPasswdFile = Join-Path $DevState 'arcadedb-root.password'

function Seed-RandomToken($path) {
    if (-not (Test-Path $path) -or (Get-Item $path).Length -eq 0) {
        & $VenvPy -c 'import secrets; print(secrets.token_urlsafe(32))' | Set-Content -NoNewline $path
    }
}

function Seed-Json($path, $body) {
    if (-not (Test-Path $path) -or (Get-Item $path).Length -eq 0) {
        Set-Content -Path $path -Value $body
    }
}

Seed-RandomToken $DevTokenFile
Seed-RandomToken $DevSyncTokenFile
Seed-RandomToken $DevApiKeyFile
# The ArcadeDB container reads this file (mounted read-only at
# /opt/arcadedb/root-password) via -Darcadedb.server.rootPasswordPath.
# Generating it up front avoids the interactive first-run prompt.
Seed-RandomToken $DevArcadedbRootPasswdFile

Seed-Json $DevKeysFile      '{"version":1,"keys":[]}'
Seed-Json $DevPrefsFile     '{"version":1}'
Seed-Json $DevBifrostConfig '{"providers":{}}'

# Start with an empty registry, exactly like a real install (web/install.sh
# ships databases.json with "databases": []). The dev loop then walks the same
# product path: you create your first database through the dashboard, which
# registers it AND runs CREATE DATABASE on ArcadeDB in one coupled operation.
# (Pre-registering a `dev` record here without also creating the ArcadeDB
# database made the reconciler sweep a database that didn't exist -> 403.)
# See scripts/README.md for a scripted shortcut if you want to skip the click.
Seed-Json $DevRegistryFile  '{"version":1,"databases":[]}'

# Pre-seed credentials so _is_account_setup() reports True and nothing nudges
# us toward the first-run setup wizard. Matches main.py's pbkdf2 params.
if (-not (Test-Path $DevCredsFile) -or (Get-Item $DevCredsFile).Length -eq 0) {
    & $VenvPy -c @'
import hashlib, json, secrets
from datetime import datetime, timezone
salt = secrets.token_hex(32)
password_hash = hashlib.pbkdf2_hmac(
    "sha256", b"devpass", salt.encode("utf-8"), 260000
).hex()
print(json.dumps({
    "version": 1,
    "username": "dev",
    "password_hash": password_hash,
    "salt": salt,
    "created_at": datetime.now(timezone.utc).isoformat(),
}, indent=2))
'@ | Set-Content $DevCredsFile
}

Green "Dev state seeded at $DevState"

# ---------- preflight: port conflicts ----------
# The dev stack binds 127.0.0.1:8080 (bifrost) and :2480 (arcadedb). A stray
# `fastapi dev app/main.py` from the API step commonly squats on 8080 — surface
# that here instead of leaving the user with a raw Docker daemon error.
function Test-PortInUse($port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}
$portHints = @{
    8080 = "likely a 'fastapi dev app/main.py' from the API dev step, or another bifrost."
    2480 = 'another ArcadeDB server is running.'
}
foreach ($port in $portHints.Keys) {
    if (Test-PortInUse $port) {
        Red "Port $port is already in use — $($portHints[$port])"
        Red 'Free it (e.g. stop that process) and re-run this script.'
        exit 1
    }
}

# ---------- dev stack ----------
Blue 'Bringing up dev containers (arcadedb + bifrost)...'
& docker compose -f $ComposeFile up -d
if ($LASTEXITCODE -ne 0) { Red 'docker compose up failed.'; exit 1 }
Green 'Dev containers up'

# ---------- env for uvicorn ----------
$env:LOCAL_DASHBOARD_DEV_MODE = '1'
if (-not $env:LOCAL_DASHBOARD_DEV_SESSION_ID) { $env:LOCAL_DASHBOARD_DEV_SESSION_ID = 'dev-session' }
$env:LOCAL_DASHBOARD_SESSION_COOKIE_SECURE = 'false'
$env:LOCAL_DASHBOARD_SESSION_TTL_SECONDS = '31536000'

$env:LOCAL_DASHBOARD_TOKEN_FILE = $DevTokenFile
$env:LOCAL_SYNC_TOKEN_FILE = $DevSyncTokenFile
$env:LOCAL_API_KEY_FILE = $DevApiKeyFile
$env:LOCAL_DASHBOARD_REGISTRY_FILE = $DevRegistryFile
$env:LOCAL_DASHBOARD_KEYS_FILE = $DevKeysFile
$env:LOCAL_DASHBOARD_CREDENTIALS_FILE = $DevCredsFile
$env:LOCAL_DASHBOARD_PREFERENCES_FILE = $DevPrefsFile
$env:LOCAL_DASHBOARD_BIFROST_CONFIG_FILE = $DevBifrostConfig

$env:LOCAL_DASHBOARD_BIFROST_CONTAINER = 'loreholm-dev-bifrost'
$env:LOCAL_DASHBOARD_BIFROST_URL = 'http://127.0.0.1:8080'
$env:LOCAL_DASHBOARD_ARCADEDB_HOST = '127.0.0.1'
$env:LOCAL_DASHBOARD_ARCADEDB_PORT = '2480'
$env:LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE = $DevArcadedbRootPasswdFile

Green 'Dev stack ready'
Blue ''
Blue '  -> Open http://127.0.0.1:4466/dev/login (once) to set the session cookie.'
Blue '  -> Edits to api/app/local_dashboard/static/ — browser refresh.'
Blue '  -> Edits to api/app/local_dashboard/*.py — uvicorn --reload handles it.'
Blue '  -> Stop dev containers: docker compose -f deploy/docker-compose.dev.yml down'
Blue ''

Set-Location (Join-Path $RepoRoot 'api')
& $VenvUvicorn app.local_dashboard.main:app `
    --host 127.0.0.1 `
    --port 4466 `
    --reload `
    --reload-dir app/local_dashboard
