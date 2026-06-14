# loreholm Web Frontend

Static frontend for the BYODB (Bring Your Own Database) service. This directory contains the public-facing website and the BYODB install/update scripts.

## Structure

```
web/
├── index.html          # Landing page
├── dashboard.html      # User dashboard (auth required)
├── docs.html           # Documentation with platform-specific install
├── install.sh          # Linux/macOS installation script
├── install.ps1         # Windows PowerShell installation script
├── install-legacy.sh   # Legacy install (Linux/macOS)
├── install-legacy.ps1  # Legacy install (Windows)
├── update.sh           # Update script (Linux/macOS)
├── update.ps1          # Update script (Windows)
├── update-legacy.sh    # Legacy update (Linux/macOS)
├── update-legacy.ps1   # Legacy update (Windows)
├── CONFIG.md           # Setup instructions
├── css/
│   └── style.css       # Styles (vanilla CSS)
└── js/
    ├── config.js       # Configuration (edit this!)
    ├── auth.js         # OIDC integration (oidc-client-ts)
    └── dashboard.js    # Dashboard logic
```

## Quick Start

1. **Configure your OIDC provider** (see CONFIG.md)
   - Create a public client (Authorization Code + PKCE)
   - Provide issuer/clientId/audience to the API (served at runtime)

2. **Deploy**
   - Files are automatically deployed via GitHub Actions
   - Served by nginx from `/var/www/html`

3. **Test locally**
   ```bash
   python3 -m http.server 8000 --directory web
   ```

## Tech Stack

- **HTML5** - Semantic markup
- **CSS3** - Vanilla CSS (no frameworks)
- **JavaScript** - ES6+ (no build tools)
- **oidc-client-ts** - Loaded from CDN

## No Build Required

This frontend intentionally has **zero dependencies** and **no build step**.

- Edit any file and refresh
- No npm install
- No webpack/vite/etc
- Deploy = copy files

## API Integration

The frontend calls these backend endpoints:

- `POST /onboarding/initialize` - Create user and generate install key
- `GET /onboarding/status` - Check connection status
- `GET /install.sh` - Download Linux/macOS install script
- `GET /install.ps1` - Download Windows install script
- `POST /mcp/*` - MCP tool endpoints (for LLMs)

All API calls use JWT Bearer tokens from the configured OIDC provider.

## Installation Scripts

Two platform-specific scripts are provided for BYODB setup:

### Linux/macOS (`install.sh`)
- Bash script for Unix-like systems
- One-line install: `curl -fsSL loreholm.com/install.sh | bash -s -- --key YOUR_KEY`

### Windows (`install.ps1`)
- PowerShell script for Windows 10/11
- One-line install: `irm loreholm.com/install.ps1 | iex`

### What the install scripts deploy

Both scripts deploy four Docker containers to `~/.loreholm/`:

| Container | Image | Purpose |
|-----------|-------|---------|
| `loreholm-tailscale` | `tailscale/tailscale:latest` | Encrypted mesh networking |
| `loreholm-bifrost-proxy` | `maximhq/bifrost:latest` | LLM provider gateway |
| `loreholm-local-dashboard` | `ghcr.io/loreholm/mcp-local-dashboard:latest` | Web UI + API (port 4466) |
| `loreholm-local-dashboard-endpoint` | `python:3.12-alpine` | Metadata server on Tailscale |

### Credential and config files created

| File | Purpose |
|------|---------|
| `local-dashboard.token` | Bootstrap token for first dashboard login |
| `local-sync.token` | Cloud-to-local sync bearer token |
| `local-api.token` | Agent API key (64-hex random token) |
| `databases.json` | Database registry |
| `chat-bifrost-config.json` | LLM provider configuration |
| `dashboard-api-keys.json` | Dashboard API key store |
| `dashboard-credentials.json` | User account credentials |
| `manage-keys.sh` | CLI utility for API key management |

### Environment variables

Key install-time variables:

- `PRE_AUTH_KEY` (required) — Headscale pre-auth key
- `NODE_NAME` (optional) — Custom hostname
- `HEADSCALE_URL` (default: `https://loreholm.com:50443`)
- `INSTALL_DIR` (default: `$HOME/.loreholm`)
- `LOCAL_DASHBOARD_NETWORK_ACCESS` — Expose dashboard on LAN
- `LOCAL_ADMIN_PORT` (default: `4466`)

## Update Scripts

`update.sh` / `update.ps1` update the Docker Compose file and pull latest images while preserving all data, credentials, and configuration. They back up `docker-compose.yml` before regenerating it.
