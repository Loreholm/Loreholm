# Frontend Setup (3 min read)

The loreholm web frontend provides user onboarding and dashboard functionality.

## Structure

```
web/
├── index.html          # Landing page with pricing
├── dashboard.html      # Authenticated user dashboard
├── docs.html           # Documentation browser
├── install.sh          # BYODB install script
├── install.ps1         # BYODB install script (Windows)
├── Config.md           # Configuration instructions
├── css/
│   └── style.css       # Clean, minimal styles
└── js/
    ├── config.js       # Auth0 and API configuration
    ├── auth.js         # Auth0 SPA integration
    └── dashboard.js    # Dashboard functionality
```

## Key Features

### Landing Page (`index.html`)
- Hero section with value proposition
- Feature grid explaining the BYODB flow
- Pricing card ($9/month)
- Sign up / Sign in buttons

### Dashboard (`dashboard.html`)
- Auth0 protected route
- Initialize onboarding flow
- Display install command with copy button
- Show connection status
- Regenerate install keys
- Access to ArcadeDB Studio (via the local dashboard "Open Database Studio" button)
- Resolve and open the user's LAN dashboard URL via API at runtime

## Authentication Flow

```mermaid
flowchart TD
    A[Browser] --> B[Auth0 Login]
    B --> C[JWT Token]
    C --> D[Dashboard]
    D --> E["POST /onboarding/initialize"]
    E --> F[Backend creates Headscale key]
    F --> G[Returns install command]
    G --> H[User copies and runs script]
    H --> I[ArcadeDB + Tailscale deployed locally]
```

## Configuration

### 1. Auth0 Setup

See [web/Config.md](../web/Config.md) for detailed instructions.

**Quick steps:**
1. Create Auth0 Single Page Application
2. Set callback URLs to `https://loreholm.com/dashboard.html`
3. Create Auth0 API with identifier `https://api.loreholm.com`
4. Copy Domain, Client ID, and Audience to `web/js/config.js`

### 2. Update `web/js/config.js`

```javascript
window.APP_CONFIG = {
    auth0: {
        domain: 'YOUR_DOMAIN.us.auth0.com',
        clientId: 'YOUR_CLIENT_ID',
        audience: 'https://api.loreholm.com',
        redirectUri: window.location.origin + '/dashboard.html',
        scope: 'openid profile email'
    },
    api: {
        baseUrl: window.location.origin,
    }
};
```

### 3. GitHub Secrets

Add to your repository secrets:
- `AUTH0_CLIENT_ID`
- `AUTH0_CLIENT_SECRET`
- `AUTH0_DOMAIN`
- `AUTH0_AUDIENCE`
- `HEADSCALE_API_URL`
- `HEADSCALE_API_KEY`

## Local Development

```bash
# Serve static files
python3 -m http.server 8000 --directory web

# Visit http://localhost:8000
```

Note: Auth0 won't work locally without configuring `http://localhost:8000` as an allowed callback URL.

## Platform Install Commands

Linux / macOS:
```bash
curl -fsSL https://loreholm.com/install.sh | bash -s -- --key YOUR_KEY
```

Windows (PowerShell):
```powershell
irm https://loreholm.com/install.ps1 | iex
```

## Design Philosophy

**No Build Tools Required:**
- Pure HTML/CSS/JavaScript
- Auth0 SDK loaded from CDN
- Edit and deploy instantly
- No npm, webpack, or bundlers

**Minimal & Clean:**
- ~500 lines of CSS total
- Vanilla JS - easy to understand
- No framework lock-in
- Fast page loads

## API Integration

The frontend calls these backend endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /onboarding/initialize` | Create user and generate install key |
| `GET /onboarding/status` | Check connection status |
| `GET /onboarding/local-dashboard/resolve` | Resolve LAN dashboard URL from BYODB node metadata |
| `GET /install.sh` | Download install script |
| `GET /install.ps1` | Download install script (Windows) |
| `GET /update.sh` | Download update script |
| `GET /update.ps1` | Download update script (Windows) |
| `POST /mcp/*` | MCP tool endpoints (for LLMs) |

All API calls use JWT Bearer tokens from Auth0.

## Deployment

The CI/CD workflow:
1. Uploads `web/` directory to server
2. Nginx serves static files from `/var/www/html`
3. API routes (`/onboarding/`, `/mcp/`) proxy to backend
