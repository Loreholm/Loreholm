#!/usr/bin/env pwsh
#
# loreholm BYODB Install Script (Windows/PowerShell)
# Usage: irm loreholm.com/install.ps1 | iex
#        Or: Invoke-WebRequest -Uri https://loreholm.com/install.ps1 -OutFile install.ps1; .\install.ps1 -Key "your-key"
#
param(
    [Parameter(Mandatory=$false)]
    [string]$Key,
    
    [Parameter(Mandatory=$false)]
    [string]$Name,
    
    [Parameter(Mandatory=$false)]
    [string]$HeadscaleUrl = "https://loreholm.com:50443",
    
    [Parameter(Mandatory=$false)]
    [string]$InstallDir = "$env:USERPROFILE\.loreholm",

    [Parameter(Mandatory=$false)]
    [string]$SyncToken = "",

    [Parameter(Mandatory=$false)]
    [ValidateSet("", "small", "default", "generous")]
    [string]$Profile = "",

    [Parameter(Mandatory=$false)]
    [ValidateSet("", "minilm", "harrier-270m")]
    [string]$EmbeddingModel = "",

    [Parameter(Mandatory=$false)]
    [string]$ArcadedbMemory = "",

    [Parameter(Mandatory=$false)]
    [switch]$Help
)

$ErrorActionPreference = "Stop"

# Colors for output
$script:Colors = @{
    Red = "Red"
    Green = "Green"
    Yellow = "Yellow"
    Blue = "Cyan"
    Reset = "White"
}
$script:LocalLanIp = ""
$script:ChatComposeFile = ""
$script:LocalDashboardDir = ""
$script:LocalDashboardFile = ""
$script:LocalDashboardEndpointPort = 8081
$script:LocalDashboardTokenFile = ""
$script:LocalDashboardCredentialsFile = ""
$script:LocalDashboardPreferencesFile = ""
$script:LocalSyncTokenFile = ""
$script:DatabaseRegistryFile = ""
$script:ChatBifrostConfigFile = ""
$script:LocalAdminPort = 4466
$script:LocalAdminBindHost = "0.0.0.0"
$script:LocalAdminAccess = "network"
$script:LocalAdminDisplayHost = ""
$script:LocalDashboardImage = if ($env:LOCAL_DASHBOARD_IMAGE) { $env:LOCAL_DASHBOARD_IMAGE } else { "ghcr.io/loreholm/mcp-local-dashboard:latest" }
$script:BifrostImage = if ($env:BIFROST_IMAGE) { $env:BIFROST_IMAGE } elseif ($env:MCP_API_IMAGE) { $env:MCP_API_IMAGE } else { "maximhq/bifrost:latest" }
$script:LocalSyncSharedToken = if ($SyncToken) { $SyncToken } elseif ($env:LOCAL_SYNC_SHARED_TOKEN) { $env:LOCAL_SYNC_SHARED_TOKEN } else { "" }
# True when the caller passed -SyncToken explicitly; drives the overwrite
# branch in New-LocalSyncToken so re-running install with a freshly
# derived per-user token replaces any stale token file on disk.
$script:LocalSyncSharedTokenExplicit = [bool]$SyncToken
$script:ArcadedbImage = if ($env:ARCADEDB_IMAGE) { $env:ARCADEDB_IMAGE } else { "arcadedata/arcadedb:26.3.1" }
$script:ArcadedbRootPasswordFile = ""

# Profile / embedding / memory selection inputs. CLI flag wins; env var is
# the fallback for non-interactive installs.
$script:LoreholmProfile = if ($Profile) { $Profile } elseif ($env:LOREHOLM_PROFILE) { $env:LOREHOLM_PROFILE } else { "" }
$script:LoreholmEmbeddingModel = if ($EmbeddingModel) { $EmbeddingModel } elseif ($env:LOREHOLM_EMBEDDING_MODEL) { $env:LOREHOLM_EMBEDDING_MODEL } else { "" }
$script:LoreholmArcadedbMemory = if ($ArcadedbMemory) { $ArcadedbMemory } elseif ($env:LOREHOLM_ARCADEDB_MEMORY) { $env:LOREHOLM_ARCADEDB_MEMORY } else { "" }
$script:SelectedProfile = ""
$script:SelectedEmbeddingModel = ""
$script:SelectedArcadedbMemory = ""
$script:DetectedRamMb = 0
$script:DetectedArch = ""

function Write-Log {
    param([string]$Message)
    Write-Host "[loreholm] " -ForegroundColor $Colors.Blue -NoNewline
    Write-Host $Message
}

function Write-Success {
    param([string]$Message)
    Write-Host "[✓] " -ForegroundColor $Colors.Green -NoNewline
    Write-Host $Message
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[!] " -ForegroundColor $Colors.Yellow -NoNewline
    Write-Host $Message
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "[✗] " -ForegroundColor $Colors.Red -NoNewline
    Write-Host $Message -ForegroundColor $Colors.Red
    exit 1
}

function Show-Banner {
    Write-Host ""
    Write-Host "                                __           ____  " -ForegroundColor $Colors.Blue
    Write-Host "   _________  _      ______  __/ /_______   / __ \____" -ForegroundColor $Colors.Blue
    Write-Host "  / ___/ __ \| | /| / / __ \/ ___/ //_/  / / / / __ /" -ForegroundColor $Colors.Blue
    Write-Host " / /__/ /_/ /| |/ |/ / /_/ / /  / ,<    / /_/ / /_/ /" -ForegroundColor $Colors.Blue
    Write-Host " \___/\____/ |__/|__/\____/_/  /_/|_|  /_____/\__,_/" -ForegroundColor $Colors.Blue
    Write-Host ""
    Write-Host "    Bring Your Own Database - Memory for LLMs" -ForegroundColor $Colors.Blue
    Write-Host ""
}

function Show-Usage {
    @"
Usage: .\install.ps1 -Key <pre-auth-key> [options]

Required:
  -Key <key>            Pre-authentication key from loreholm.com dashboard

Options:
  -Name <name>                Custom node name (default: computer name)
  -HeadscaleUrl <url>         Headscale server URL (default: $HeadscaleUrl)
  -InstallDir <path>          Installation directory (default: $InstallDir)
  -SyncToken <token>          Shared cloud->local sync token (optional, recommended)
  -Profile <p>                Resource profile: small, default, generous (auto-detected from RAM)
  -EmbeddingModel <m>         Embedding model: minilm or harrier-270m (auto-selected; arm64 defaults to minilm)
  -ArcadedbMemory <opts>      JVM heap args for ArcadeDB (e.g. "-Xms800M -Xmx800M"); overrides profile
  -Help                       Show this help message

Examples:
  .\install.ps1 -Key "preauthkey-abc123"
  .\install.ps1 -Key "preauthkey-abc123" -Name "my-workstation"

For one-line web install:
  irm loreholm.com/install.ps1 | iex

"@
    exit 0
}

function Normalize-NodeName {
    param([string]$RawName)

    $candidate = if ($null -ne $RawName) { $RawName } else { "" }
    $candidate = $candidate -replace "[\r\n]", ""
    $candidate = $candidate -replace "[^A-Za-z0-9_.-]", "-"
    $candidate = $candidate.Trim("-")

    if ([string]::IsNullOrWhiteSpace($candidate)) {
        return "loreholm-node"
    }

    return $candidate
}

function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-Dependencies {
    Write-Log "Checking dependencies..."
    
    # Check for Docker
    try {
        $dockerVersion = docker --version 2>$null
        if (-not $dockerVersion) {
            Write-Error-Custom "Docker is not installed. Please install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
        }
    } catch {
        Write-Error-Custom "Docker is not installed. Please install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
    }
    
    # Check Docker is running
    try {
        docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Error-Custom "Docker daemon is not running. Please start Docker Desktop and try again."
        }
    } catch {
        Write-Error-Custom "Docker daemon is not running. Please start Docker Desktop and try again."
    }
    
    # Check for Docker Compose
    try {
        docker compose version 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $script:ComposeCmd = "docker compose"
        } else {
            docker-compose version 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $script:ComposeCmd = "docker-compose"
            } else {
                Write-Error-Custom "Docker Compose is not installed. Please install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
            }
        }
    } catch {
        Write-Error-Custom "Docker Compose is not installed."
    }
    
    Write-Success "Docker and Docker Compose are available"
}

function New-InstallDirectory {
    Write-Log "Creating installation directory: $InstallDir"
    
    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }

    $script:LocalDashboardDir = Join-Path $InstallDir "local-dashboard"
    $script:LocalDashboardFile = Join-Path $script:LocalDashboardDir "local-dashboard.json"
    $script:ChatComposeFile = Join-Path $InstallDir "docker-compose.chat.yml"
    $script:LocalDashboardTokenFile = Join-Path $InstallDir "local-dashboard.token"
    $script:LocalDashboardCredentialsFile = Join-Path $InstallDir "dashboard-credentials.json"
    $script:LocalDashboardApiKeysFile = Join-Path $InstallDir "dashboard-api-keys.json"
    $script:LocalDashboardPreferencesFile = Join-Path $InstallDir "dashboard-preferences.json"
    $script:LocalDashboardChatDbFile = Join-Path $InstallDir "chat.db"
    $script:LocalSyncTokenFile = Join-Path $InstallDir "local-sync.token"
    $script:DatabaseRegistryFile = Join-Path $InstallDir "databases.json"
    $script:ChatBifrostConfigFile = Join-Path $InstallDir "chat-bifrost-config.json"
    $script:ArcadedbRootPasswordFile = Join-Path $InstallDir "arcadedb-root.password"
    New-Item -ItemType Directory -Path $script:LocalDashboardDir -Force | Out-Null

    Write-Success "Installation directory created"
}

function Get-HostSpecs {
    $ramMb = 0
    try {
        $cim = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction SilentlyContinue
        if ($cim -and $cim.TotalPhysicalMemory) {
            $ramMb = [int]([math]::Floor($cim.TotalPhysicalMemory / 1MB))
        }
    } catch {
        # ignore — fall through
    }

    $arch = "unknown"
    try {
        if ($env:PROCESSOR_ARCHITECTURE) {
            $arch = $env:PROCESSOR_ARCHITECTURE
        }
    } catch {
        # ignore
    }

    $script:DetectedRamMb = $ramMb
    $script:DetectedArch = $arch
    Write-Log "Detected host: $ramMb MB RAM, arch $arch"
}

function Select-InstallProfile {
    $ramMb = $script:DetectedRamMb
    $arch = $script:DetectedArch

    # Profile tier.
    $profile = ""
    if ($script:LoreholmProfile) {
        $profile = $script:LoreholmProfile
    } elseif ($ramMb -lt 4096) {
        Write-Warn "Host has less than 4 GB RAM ($ramMb MB); proceeding with 'small' profile but expect slow embedding performance."
        $profile = "small"
    } elseif ($ramMb -lt 8192) {
        $profile = "small"
    } elseif ($ramMb -lt 16384) {
        $profile = "default"
    } else {
        $profile = "generous"
    }
    $script:SelectedProfile = $profile

    # Embedding model. arm64 hosts default to minilm unless explicitly overridden.
    $model = ""
    if ($script:LoreholmEmbeddingModel) {
        $model = $script:LoreholmEmbeddingModel
    } else {
        switch ($profile) {
            "small"    { $model = "minilm" }
            "default"  { $model = "harrier-270m" }
            "generous" { $model = "harrier-270m" }
        }
        if ($arch -match "ARM64|AARCH64") {
            if ($model -ne "minilm") {
                Write-Log "arm64 host detected — defaulting to minilm embeddings. Override with -EmbeddingModel harrier-270m."
                $model = "minilm"
            }
        }
    }
    $script:SelectedEmbeddingModel = $model

    # ArcadeDB heap.
    $memory = ""
    if ($script:LoreholmArcadedbMemory) {
        $memory = $script:LoreholmArcadedbMemory
    } else {
        switch ($profile) {
            "small"    { $memory = "-Xms512M -Xmx512M" }
            "default"  { $memory = "-Xms800M -Xmx800M" }
            "generous" { $memory = "-Xms2G -Xmx2G" }
        }
    }
    $script:SelectedArcadedbMemory = $memory

    Write-Log "Profile: $($script:SelectedProfile) | embedding: $($script:SelectedEmbeddingModel) | arcadedb-memory: $($script:SelectedArcadedbMemory)"
}

function New-ArcadedbRootPassword {
    if ((Test-Path $script:ArcadedbRootPasswordFile -PathType Leaf) -and ((Get-Item $script:ArcadedbRootPasswordFile).Length -gt 0)) {
        Write-Success "Using existing ArcadeDB root password"
        return
    }

    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [Convert]::ToHexString($bytes).ToLowerInvariant()
    # Write without trailing newline for ArcadeDB's root-password file loader.
    [System.IO.File]::WriteAllText($script:ArcadedbRootPasswordFile, $token)
    Write-Success "Generated ArcadeDB root password"
}

function Get-LocalLanIp {
    try {
        $defaultRoute = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Sort-Object RouteMetric, InterfaceMetric |
            Select-Object -First 1
        if ($defaultRoute) {
            $routeIp = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $defaultRoute.InterfaceIndex -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.IPAddress -and
                    $_.IPAddress -ne "127.0.0.1" -and
                    (-not $_.IPAddress.StartsWith("169.254."))
                } |
                Select-Object -ExpandProperty IPAddress -First 1
            if ($routeIp) {
                return $routeIp
            }
        }
    } catch {
        # fall through to DNS lookup
    }

    try {
        $dnsIp = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
            Where-Object {
                $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
                $_.IPAddressToString -ne "127.0.0.1" -and
                (-not $_.IPAddressToString.StartsWith("169.254."))
            } |
            Select-Object -First 1
        if ($dnsIp) {
            return $dnsIp.IPAddressToString
        }
    } catch {
        # fall through to localhost
    }

    return "127.0.0.1"
}

function ConvertTo-BoolPreference {
    param([string]$Value)

    $normalized = if ($null -ne $Value) { $Value.Trim().ToLowerInvariant() } else { "" }
    switch ($normalized) {
        { $_ -in @("1", "true", "yes", "y", "on") } { return $true }
        { $_ -in @("0", "false", "no", "n", "off") } { return $false }
        default { return $null }
    }
}

function Set-LocalDashboardAccessPreference {
    Write-Log "Configuring local dashboard network access..."

    $allowNetwork = $null
    $override = ConvertTo-BoolPreference -Value $env:LOCAL_DASHBOARD_NETWORK_ACCESS
    if ($null -ne $override) {
        $allowNetwork = [bool]$override
    } elseif (-not [Console]::IsInputRedirected) {
        $response = Read-Host "Expose the local dashboard on your local network? [Y/n]"
        $parsed = ConvertTo-BoolPreference -Value $response
        if ($null -eq $parsed) {
            if ([string]::IsNullOrWhiteSpace($response)) {
                $allowNetwork = $true
            } else {
                Write-Warn "Unrecognized answer '$response'; defaulting to yes."
                $allowNetwork = $true
            }
        } else {
            $allowNetwork = [bool]$parsed
        }
    } else {
        $allowNetwork = $true
    }

    if ($allowNetwork) {
        $script:LocalAdminBindHost = "0.0.0.0"
        $script:LocalAdminAccess = "network"
        $script:LocalAdminDisplayHost = $script:LocalLanIp
        Write-Success "Local dashboard access set to local network ($script:LocalAdminDisplayHost)."
    } else {
        $script:LocalAdminBindHost = "127.0.0.1"
        $script:LocalAdminAccess = "localhost"
        $script:LocalAdminDisplayHost = "127.0.0.1"
        Write-Warn "Local dashboard access set to local-only (127.0.0.1)."
    }
}

function Write-LocalDashboardMetadata {
    param([string]$LanIp)

    if (-not (Test-Path $script:LocalDashboardDir -PathType Container)) {
        New-Item -ItemType Directory -Path $script:LocalDashboardDir -Force | Out-Null
    }

    $metadata = @{
        lan_ip = $LanIp
        port = $script:LocalAdminPort
        path = "/"
        local_admin_host = $script:LocalAdminDisplayHost
        local_admin_port = $script:LocalAdminPort
        local_admin_path = "/"
        local_admin_access = $script:LocalAdminAccess
        source = "loreholm-install.ps1"
    } | ConvertTo-Json -Depth 3

    Set-Content -Path $script:LocalDashboardFile -Value $metadata -Encoding UTF8
}

function Write-LocalDashboardEndpointServer {
    if (-not (Test-Path $script:LocalDashboardDir -PathType Container)) {
        New-Item -ItemType Directory -Path $script:LocalDashboardDir -Force | Out-Null
    }

    $endpointScript = @'
#!/usr/bin/env python3
"""Tailnet-facing shim for the FastAPI local dashboard.

Runs inside the Tailscale container's network namespace (the only thing
on this host with a Tailnet IP) and forwards every `/api/sync/*` request
to the real FastAPI local dashboard container over the Docker bridge.
Sync routes on the FastAPI side enforce bearer-token auth against the
same `local-sync.token` file the cloud's per-user derived token is
compared against, so the shim only relays headers - it does not verify
the bearer itself.

Also serves two local-only routes that predate the sync shim and don't
belong on FastAPI:
  GET /healthz              - liveness check
  GET /local-dashboard.json - LAN-admin metadata for the dashboard link
"""
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


META_FILE = os.getenv("LOCAL_DASHBOARD_META_FILE", "/opt/local-dashboard/local-dashboard.json")
BIND_HOST = os.getenv("LOCAL_SYNC_BIND_HOST", "0.0.0.0")
BIND_PORT = int(os.getenv("LOCAL_SYNC_BIND_PORT", "8081"))

# Upstream FastAPI local dashboard. Reachable from inside the tailscale
# netns over the Docker bridge via Docker's embedded DNS - both the
# tailscale container and the loreholm-local-dashboard container live on
# the same compose-default network.
UPSTREAM_URL = os.getenv(
    "LOCAL_DASHBOARD_UPSTREAM",
    "http://loreholm-local-dashboard:4466",
)
UPSTREAM_TIMEOUT = float(os.getenv("LOCAL_DASHBOARD_UPSTREAM_TIMEOUT", "30"))

_parsed_upstream = urlparse(UPSTREAM_URL)
UPSTREAM_HOST = _parsed_upstream.hostname or "loreholm-local-dashboard"
UPSTREAM_PORT = _parsed_upstream.port or 4466

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

# Path prefixes the shim forwards to the upstream FastAPI local dashboard.
# Sync lanes carry cloud->local pull traffic; chat lanes carry the chat proxy
# traffic originating from chat.loreholm.com via the cloud /chat router.
_FORWARD_PREFIXES = ("/api/sync/", "/api/chat/")

# Paths that must stream their response body without buffering (SSE).
_STREAM_PATHS = {"/api/chat/stream"}


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _detail(code, message):
    return {"detail": {"error": {"code": code, "message": message}}}


class Handler(BaseHTTPRequestHandler):
    def _write_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self, method, stream=False):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b""

        forward_headers = {}
        for name, value in self.headers.items():
            lower = name.lower()
            if lower in _HOP_BY_HOP or lower in {"host", "content-length"}:
                continue
            forward_headers[name] = value
        if body:
            forward_headers["Content-Length"] = str(len(body))

        try:
            conn = http.client.HTTPConnection(
                UPSTREAM_HOST, UPSTREAM_PORT, timeout=UPSTREAM_TIMEOUT
            )
        except OSError as exc:
            self._write_json(
                502,
                _detail(
                    "UPSTREAM_UNREACHABLE",
                    "Could not reach local dashboard upstream at "
                    "{}:{}: {}".format(UPSTREAM_HOST, UPSTREAM_PORT, exc),
                ),
            )
            return

        try:
            try:
                conn.request(method, self.path, body=body, headers=forward_headers)
                response = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                self._write_json(
                    502,
                    _detail(
                        "UPSTREAM_UNREACHABLE",
                        "Could not reach local dashboard upstream at "
                        "{}:{}: {}".format(UPSTREAM_HOST, UPSTREAM_PORT, exc),
                    ),
                )
                return

            if stream:
                # Relay without buffering — required for SSE endpoints.
                self.send_response(response.status)
                for name, value in response.getheaders():
                    lower = name.lower()
                    if lower in _HOP_BY_HOP or lower == "content-length":
                        continue
                    self.send_header(name, value)
                self.end_headers()
                try:
                    while True:
                        chunk = response.read(4096)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        try:
                            self.wfile.flush()
                        except OSError:
                            break
                except (OSError, http.client.HTTPException):
                    pass
                return

            response_body = response.read()
            status = response.status
            response_headers = list(response.getheaders())

            self.send_response(status)
            saw_content_type = False
            for name, value in response_headers:
                lower = name.lower()
                if lower in _HOP_BY_HOP or lower == "content-length":
                    continue
                if lower == "content-type":
                    saw_content_type = True
                self.send_header(name, value)
            if not saw_content_type:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _should_forward(self):
        return any(self.path.startswith(p) for p in _FORWARD_PREFIXES)

    def _is_stream_path(self):
        return self.path in _STREAM_PATHS

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self._write_json(200, {"ok": True})
            return
        if self.path == "/local-dashboard.json":
            payload = _read_json(META_FILE, {})
            if not isinstance(payload, dict):
                payload = {}
            self._write_json(200, payload)
            return
        if self._should_forward():
            self._forward("GET", stream=self._is_stream_path())
            return
        self._write_json(404, _detail("NOT_FOUND", "Route not found."))

    def do_POST(self):  # noqa: N802
        if self._should_forward():
            self._forward("POST", stream=self._is_stream_path())
            return
        self._write_json(404, _detail("NOT_FOUND", "Route not found."))

    def do_DELETE(self):  # noqa: N802
        if self._should_forward():
            self._forward("DELETE")
            return
        self._write_json(404, _detail("NOT_FOUND", "Route not found."))

    def log_message(self, _format, *_args):  # noqa: A003
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    server.serve_forever()
'@

    $endpointPath = Join-Path $script:LocalDashboardDir "endpoint_server.py"
    Set-Content -Path $endpointPath -Value $endpointScript -Encoding UTF8
}

function Initialize-DashboardApiKeys {
    if (Test-Path $script:LocalDashboardApiKeysFile -PathType Leaf) {
        Write-Success "Using existing dashboard API keys file"
        return
    }

    Set-Content -Path $script:LocalDashboardApiKeysFile -Value '{"version":1,"keys":[]}' -Encoding UTF8
    Write-Success "Initialized dashboard API keys file"
}

function Initialize-DashboardCredentials {
    if (Test-Path $script:LocalDashboardCredentialsFile -PathType Leaf) {
        Write-Success "Using existing dashboard credentials file"
        return
    }

    Set-Content -Path $script:LocalDashboardCredentialsFile -Value "" -Encoding UTF8
    Write-Success "Initialized dashboard credentials file"
}

function Initialize-DashboardPreferences {
    if ((Test-Path $script:LocalDashboardPreferencesFile -PathType Leaf) -and ((Get-Item $script:LocalDashboardPreferencesFile).Length -gt 0)) {
        Write-Success "Using existing dashboard preferences file"
        return
    }

    Set-Content -Path $script:LocalDashboardPreferencesFile -Value '{"version":1}' -Encoding UTF8
    Write-Success "Initialized dashboard preferences file"
}

function New-LocalDashboardToken {
    # If credentials are already set up, no bootstrap token needed
    if ((Test-Path $script:LocalDashboardCredentialsFile -PathType Leaf) -and ((Get-Item $script:LocalDashboardCredentialsFile).Length -gt 0)) {
        Write-Success "Dashboard credentials exist; skipping bootstrap token"
        return
    }

    # Always generate a fresh token when no credentials exist
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [Convert]::ToHexString($bytes).ToLowerInvariant()
    Set-Content -Path $script:LocalDashboardTokenFile -Value $token -Encoding UTF8
    Write-Success "Generated local dashboard token"
}

function New-LocalSyncToken {
    # If the caller passed -SyncToken explicitly, always overwrite so a
    # newly-derived per-user token (post secret rotation) actually lands
    # on disk instead of being swallowed by the existing-token early return.
    if ($script:LocalSyncSharedTokenExplicit -and -not [string]::IsNullOrWhiteSpace($script:LocalSyncSharedToken)) {
        Set-Content -Path $script:LocalSyncTokenFile -Value $script:LocalSyncSharedToken -Encoding UTF8
        Write-Success "Installed sync token from -SyncToken (per-user derived)"
        return
    }

    if ((Test-Path $script:LocalSyncTokenFile -PathType Leaf) -and ((Get-Item $script:LocalSyncTokenFile).Length -gt 0)) {
        Write-Success "Using existing local sync token"
        return
    }

    $token = $script:LocalSyncSharedToken
    if ([string]::IsNullOrWhiteSpace($token)) {
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $token = [Convert]::ToHexString($bytes).ToLowerInvariant()
        Write-Warn "LOCAL_SYNC_SHARED_TOKEN was not provided; generated a local-only sync token."
    }

    Set-Content -Path $script:LocalSyncTokenFile -Value $token -Encoding UTF8
    Write-Success "Initialized local sync token"
}

function Initialize-DatabaseRegistry {
    if ((Test-Path $script:DatabaseRegistryFile -PathType Leaf) -and ((Get-Item $script:DatabaseRegistryFile).Length -gt 0)) {
        Write-Success "Using existing database registry"
        return
    }

    $payload = @{
        version = 1
        databases = @()
    } | ConvertTo-Json -Depth 5

    Set-Content -Path $script:DatabaseRegistryFile -Value $payload -Encoding UTF8
    Write-Success "Initialized empty local database registry"
}

function Initialize-BifrostConfig {
    if ((Test-Path $script:ChatBifrostConfigFile -PathType Leaf) -and ((Get-Item $script:ChatBifrostConfigFile).Length -gt 0)) {
        Write-Success "Using existing Bifrost config"
        return
    }

    Set-Content -Path $script:ChatBifrostConfigFile -Value "{`n  `"providers`": {}`n}" -Encoding UTF8
    Write-Success "Initialized default Bifrost config"
}

function New-ComposeFile {
    param([string]$PreAuthKey, [string]$NodeName)
    
    Write-Log "Generating Docker Compose configuration..."
    
    $composeFile = Join-Path $InstallDir "docker-compose.yml"
    $timestamp = Get-Date -Format "o"
    
    $composeContent = @"
# loreholm BYODB Stack
# Generated by install.ps1 on $timestamp
# Documentation: https://loreholm.com/docs
# API Reference: https://api.loreholm.com/docs
#
# Profile: $($script:SelectedProfile) (RAM=$($script:DetectedRamMb) MB, arch=$($script:DetectedArch))
# Embedding model: $($script:SelectedEmbeddingModel)
# ArcadeDB heap: $($script:SelectedArcadedbMemory)

services:
  # Tailscale sidecar - connects to Headscale mesh network
  tailscale:
    image: tailscale/tailscale:latest
    container_name: loreholm-tailscale
    hostname: $NodeName
    restart: unless-stopped
    # Only NET_ADMIN is required: /dev/net/tun is bind-mounted, so the host
    # already provides the tun device. Re-add SYS_MODULE only if a host lacks
    # the tun kernel module and tailscale cannot create the interface.
    cap_add:
      - NET_ADMIN
    volumes:
      - tailscale_state:/var/lib/tailscale
      - /dev/net/tun:/dev/net/tun
    environment:
      - TS_AUTHKEY=$PreAuthKey
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_USERSPACE=false
      # No --accept-routes: a leaf node never needs subnet routes pushed by the
      # control server, so Headscale cannot steer this node's traffic.
      - TS_EXTRA_ARGS=--login-server=$HeadscaleUrl
    healthcheck:
      test: ["CMD", "tailscale", "status"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # Single shared ArcadeDB server. All per-database CRUD is HTTP against
  # this one container; no per-database containers, no Docker socket.
  # Lives on the default compose bridge — NOT in the tailscale netns —
  # so 2480 is unreachable from the tailnet regardless of ACL state.
  # Reached from the dashboard as `loreholm-arcadedb:2480` over the bridge.
  arcadedb:
    image: $script:ArcadedbImage
    container_name: loreholm-arcadedb
    restart: unless-stopped
    environment:
      JAVA_OPTS: "-Darcadedb.server.httpIncoming.port=2480 -Darcadedb.server.rootPasswordPath=/opt/arcadedb/root-password -Darcadedb.server.mode=production -Darcadedb.profile=low-ram $($script:SelectedArcadedbMemory)"
    volumes:
      - arcadedb_data:/home/arcadedb/databases
      - arcadedb_log:/home/arcadedb/log
      - "./arcadedb-root.password:/opt/arcadedb/root-password:ro"

  # Bifrost proxy for local wizard and chat-compatible /v1 model APIs.
  # Lives on the default compose bridge — NOT in the tailscale netns —
  # so 8080 is unreachable from the tailnet. Reached from the dashboard
  # as `loreholm-bifrost-proxy:8080` over the bridge.
  bifrost-proxy:
    image: $script:BifrostImage
    container_name: loreholm-bifrost-proxy
    restart: unless-stopped
    volumes:
      - "./chat-bifrost-config.json:/app/data/config.json"

  # Local admin API + setup wizard for local BYODB databases.
  local-dashboard:
    image: $script:LocalDashboardImage
    container_name: loreholm-local-dashboard
    restart: unless-stopped
    depends_on:
      tailscale:
        condition: service_healthy
      arcadedb:
        condition: service_started
    ports:
      - "${script:LocalAdminBindHost}:${script:LocalAdminPort}:${script:LocalAdminPort}"
    environment:
      - LOCAL_DASHBOARD_TOKEN_FILE=/opt/loreholm/local-dashboard.token
      - LOCAL_DASHBOARD_CREDENTIALS_FILE=/opt/loreholm/dashboard-credentials.json
      - LOCAL_DASHBOARD_KEYS_FILE=/opt/loreholm/dashboard-api-keys.json
      - LOCAL_DASHBOARD_PREFERENCES_FILE=/opt/loreholm/dashboard-preferences.json
      - LOCAL_DASHBOARD_CHAT_DB_FILE=/opt/loreholm/chat.db
      - LOCAL_SYNC_TOKEN_FILE=/opt/loreholm/local-sync.token
      - LOCAL_DASHBOARD_REGISTRY_FILE=/opt/loreholm/databases.json
      - LOCAL_DASHBOARD_BIFROST_CONFIG_FILE=/opt/loreholm/chat-bifrost-config.json
      - LOCAL_DASHBOARD_BIFROST_URL=http://loreholm-bifrost-proxy:8080
      - LOCAL_DASHBOARD_ARCADEDB_HOST=loreholm-arcadedb
      - LOCAL_DASHBOARD_ARCADEDB_PORT=2480
      - LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE=/opt/loreholm/arcadedb-root.password
      - LOCAL_DASHBOARD_EMBEDDING_MODEL=$($script:SelectedEmbeddingModel)
    command:
      - uvicorn
      - app.local_dashboard.main:app
      - --host
      - 0.0.0.0
      - --port
      - "$script:LocalAdminPort"
    volumes:
      - "./local-dashboard.token:/opt/loreholm/local-dashboard.token:ro"
      - "./dashboard-credentials.json:/opt/loreholm/dashboard-credentials.json"
      - "./dashboard-api-keys.json:/opt/loreholm/dashboard-api-keys.json"
      - "./dashboard-preferences.json:/opt/loreholm/dashboard-preferences.json"
      - "./chat.db:/opt/loreholm/chat.db"
      - "./local-sync.token:/opt/loreholm/local-sync.token:ro"
      - "./databases.json:/opt/loreholm/databases.json"
      - "./chat-bifrost-config.json:/opt/loreholm/chat-bifrost-config.json"
      - "./arcadedb-root.password:/opt/loreholm/arcadedb-root.password:ro"
      # Persist the embedding-model cache across restarts and image
      # upgrades so first-start downloads (~300 MB Harrier or ~80 MB
      # MiniLM) are amortized over the life of the install.
      - "loreholm-hf-cache:/root/.cache/huggingface"
      - "loreholm-st-cache:/root/.cache/torch"

  # Tailnet-facing shim. Runs inside the tailscale container's netns
  # (the only thing on this host with a Tailnet IP) and:
  #   - serves /healthz and /local-dashboard.json locally, and
  #   - reverse-proxies every /api/sync/* request to loreholm-local-dashboard
  #     over the Docker bridge. The FastAPI dashboard verifies the sync
  #     bearer token against /opt/loreholm/local-sync.token on its side, so
  #     this shim just relays the Authorization header unmodified.
  local-dashboard-endpoint:
    image: python:3.12-alpine
    container_name: loreholm-local-dashboard-endpoint
    restart: unless-stopped
    network_mode: service:tailscale
    depends_on:
      tailscale:
        condition: service_healthy
      local-dashboard:
        condition: service_started
    command:
      - python
      - /opt/local-dashboard/endpoint_server.py
    environment:
      - LOCAL_DASHBOARD_META_FILE=/opt/local-dashboard/local-dashboard.json
      - LOCAL_SYNC_BIND_PORT=$script:LocalDashboardEndpointPort
      - LOCAL_DASHBOARD_UPSTREAM=http://loreholm-local-dashboard:$script:LocalAdminPort
    volumes:
      - "./local-dashboard:/opt/local-dashboard:ro"

volumes:
  tailscale_state:
    name: loreholm-tailscale-state
  arcadedb_data:
    name: loreholm-arcadedb-data
  arcadedb_log:
    name: loreholm-arcadedb-log
  loreholm-hf-cache:
    name: loreholm-hf-cache
  loreholm-st-cache:
    name: loreholm-st-cache
"@

    Set-Content -Path $composeFile -Value $composeContent -Encoding UTF8
    Write-Success "Docker Compose file generated: $composeFile"
}

function Remove-ExistingContainers {
    Write-Log "Checking for existing loreholm containers..."
    
    $existingContainers = docker ps -a --filter "name=loreholm-" --format "{{.Names}}" 2>$null
    
    if ($existingContainers) {
        Write-Warn "Found existing loreholm containers, cleaning up..."
        
        foreach ($container in $existingContainers) {
            Write-Log "Stopping $container..."
            docker stop $container 2>&1 | Out-Null
            docker rm $container 2>&1 | Out-Null
        }
        
        Write-Success "Cleaned up existing containers"
    }
}

function Test-PortAvailability {
    param([int]$Port)
    
    $tcpConnections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    return ($null -eq $tcpConnections)
}

function Start-Services {
    Write-Log "Starting loreholm services..."
    
    Remove-ExistingContainers

    if (-not (Test-PortAvailability -Port $script:LocalAdminPort)) {
        Write-Error-Custom "Port $script:LocalAdminPort is in use. Stop the conflicting service and rerun install."
    }
    
    Push-Location $InstallDir
    try {
        $composeArgs = @("-f", (Join-Path $InstallDir "docker-compose.yml"))
        if (Test-Path $script:ChatComposeFile -PathType Leaf) {
            Write-Log "Including optional chat compose overlay: $script:ChatComposeFile"
            $composeArgs += @("-f", $script:ChatComposeFile)
        }
        & $ComposeCmd @composeArgs pull
        if ($LASTEXITCODE -ne 0) {
            Write-Error-Custom "Failed to pull latest service images"
        }
        & $ComposeCmd @composeArgs up -d --remove-orphans
        if ($LASTEXITCODE -ne 0) {
            Write-Error-Custom "Failed to start services"
        }
    } finally {
        Pop-Location
    }
    
    Write-Success "Services started"
}

function Remove-UnusedImages {
    Write-Log "Cleaning up unused Docker images..."
    
    docker image prune -f 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Cleaned up unused images"
    } else {
        Write-Warn "Could not clean up images (non-critical)"
    }
}

function Wait-ForTailscale {
    Write-Log "Waiting for Tailscale to connect..."
    
    $maxAttempts = 30
    $attempt = 0
    
    while ($attempt -lt $maxAttempts) {
        try {
            docker exec loreholm-tailscale tailscale status 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $ip = docker exec loreholm-tailscale tailscale ip -4 2>$null
                if ($ip) {
                    Write-Success "Tailscale connected! IP: $ip"
                    return
                }
            }
        } catch {
            # Continue waiting
        }
        
        $attempt++
        Start-Sleep -Seconds 2
    }
    
    Write-Warn "Tailscale connection timeout. It may still be connecting in the background."
    Write-Warn "Check status with: docker logs loreholm-tailscale"
}

function Show-Status {
    param([string]$NodeName)
    
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor $Colors.Green
    Write-Host "  Installation Complete!" -ForegroundColor $Colors.Green
    Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor $Colors.Green
    Write-Host ""
    
    # Get Tailscale IP if available
    $tsIp = docker exec loreholm-tailscale tailscale ip -4 2>$null
    if (-not $tsIp) {
        $tsIp = "pending..."
    }
    $credentialsExist = (Test-Path $script:LocalDashboardCredentialsFile -PathType Leaf) -and ((Get-Item $script:LocalDashboardCredentialsFile).Length -gt 0)
    $dashboardToken = ""
    if (-not $credentialsExist -and (Test-Path $script:LocalDashboardTokenFile)) {
        $dashboardToken = (Get-Content $script:LocalDashboardTokenFile -Raw -ErrorAction SilentlyContinue).Trim()
    }
    if (-not $dashboardToken -and -not $credentialsExist) {
        $dashboardToken = "unavailable"
    }

    Write-Host "  Node Name:     " -ForegroundColor $Colors.Blue -NoNewline
    Write-Host $NodeName
    Write-Host "  Tailscale IP:  " -ForegroundColor $Colors.Blue -NoNewline
    Write-Host $tsIp
    Write-Host "  LAN IP:        " -ForegroundColor $Colors.Blue -NoNewline
    Write-Host $script:LocalLanIp
    Write-Host "  Admin Access:  " -ForegroundColor $Colors.Blue -NoNewline
    Write-Host $script:LocalAdminAccess
    Write-Host "  Local Admin:   " -ForegroundColor $Colors.Blue -NoNewline
    Write-Host "http://$script:LocalAdminDisplayHost:$script:LocalAdminPort"
    if (-not $credentialsExist) {
        Write-Host "  Local Token:   " -ForegroundColor $Colors.Blue -NoNewline
        Write-Host $dashboardToken
    }
    Write-Host "  Install Dir:   " -ForegroundColor $Colors.Blue -NoNewline
    Write-Host $InstallDir
    Write-Host ""
    Write-Host "  Useful Commands:" -ForegroundColor $Colors.Yellow
    Write-Host "  └─ View logs:     docker logs loreholm-local-dashboard"
    Write-Host "  └─ Check status:  docker exec loreholm-tailscale tailscale status"
    Write-Host "  └─ Local admin:   Start-Process http://$script:LocalAdminDisplayHost:$script:LocalAdminPort"
    if (-not $credentialsExist) {
        Write-Host "  └─ Token file:    $script:LocalDashboardTokenFile"
        Write-Host "  └─ Show token:    Get-Content `"$script:LocalDashboardTokenFile`""
    }
    Write-Host "  └─ Resolver data: Get-Content $script:LocalDashboardFile"
    Write-Host "  └─ Stop services: cd $InstallDir; docker compose down"
    Write-Host "  └─ Restart:       cd $InstallDir; docker compose restart"
    Write-Host "  └─ Uninstall:     irm loreholm.com/uninstall.ps1 | iex"
    Write-Host ""
    Write-Host "  Next Steps:" -ForegroundColor $Colors.Yellow
    if ($script:LocalAdminAccess -eq "localhost") {
        Write-Host "  1. Open http://$script:LocalAdminDisplayHost:$script:LocalAdminPort on this machine"
    } else {
        Write-Host "  1. Open http://$script:LocalAdminDisplayHost:$script:LocalAdminPort on your local network"
    }
    Write-Host "  2. Complete the local setup wizard to create your first database"
    Write-Host "  3. Return to https://loreholm.com/dashboard to verify connection"
    Write-Host "  4. Configure your LLM client to use the MCP tools"
    Write-Host ""
}

function Invoke-Main {
    Show-Banner
    
    if ($Help) {
        Show-Usage
    }
    
    # Check if running from web (no parameters provided)
    if (-not $Key -and -not $PSBoundParameters.ContainsKey('Key')) {
        Write-Host "To install loreholm, you need a pre-authentication key." -ForegroundColor $Colors.Yellow
        Write-Host ""
        Write-Host "Get your key from: https://loreholm.com/dashboard" -ForegroundColor $Colors.Blue
        Write-Host ""
        Write-Host "Then run:" -ForegroundColor $Colors.Yellow
        Write-Host "  .\install.ps1 -Key `"your-pre-auth-key`"" -ForegroundColor $Colors.Green
        Write-Host ""
        exit 1
    }
    
    if (-not $Key) {
        if (Test-Path (Join-Path $InstallDir "docker-compose.yml")) {
            Write-Error-Custom "Pre-auth key is required for first-time install. Existing installation detected at $InstallDir. Run: irm loreholm.com/update.ps1 | iex"
        }
        Write-Error-Custom "Pre-auth key is required. Use -Key <key>"
    }
    
    # Default node name to computer name
    if (-not $Name) {
        $Name = $env:COMPUTERNAME
    }
    $Name = Normalize-NodeName -RawName $Name
    
    Test-Dependencies
    Get-HostSpecs
    Select-InstallProfile
    New-InstallDirectory
    $script:LocalLanIp = Get-LocalLanIp
    if ($script:LocalLanIp -eq "127.0.0.1") {
        Write-Warn "Could not auto-detect LAN IP; using $script:LocalLanIp"
    } else {
        Write-Success "Detected LAN IP: $script:LocalLanIp"
    }
    Set-LocalDashboardAccessPreference
    Initialize-DashboardCredentials
    Initialize-DashboardApiKeys
    Initialize-DashboardPreferences
    New-LocalDashboardToken
    New-LocalSyncToken
    New-ArcadedbRootPassword
    Initialize-DatabaseRegistry
    Initialize-BifrostConfig
    Write-LocalDashboardMetadata -LanIp $script:LocalLanIp
    Write-LocalDashboardEndpointServer
    New-ComposeFile -PreAuthKey $Key -NodeName $Name
    Start-Services
    Write-LocalDashboardMetadata -LanIp $script:LocalLanIp
    Remove-UnusedImages
    Wait-ForTailscale
    Show-Status -NodeName $Name
}

# Run main function
Invoke-Main
