# Web Frontend Configuration

loreholm authenticates against **any OpenID Connect (OIDC) provider** — there
is no hard dependency on a specific vendor. You pick one issuer per deployment
(Auth0, Keycloak, Zitadel, Authentik, Google, …) and point loreholm at it.

## Required Setup

You need to configure two things in your OIDC provider:
1. **A public client** (Authorization Code + PKCE) for the browser apps
2. **An audience / API identifier** so the backend can validate access tokens

The backend discovers all provider endpoints automatically from
`${OIDC_ISSUER}/.well-known/openid-configuration`, so you only ever supply the
issuer URL — never hand-built JWKS or authorize/token URLs.

---

## Step 1: Create a public OIDC client

In your provider, create an application of type **Single Page App / public
client** using **Authorization Code flow with PKCE**. Configure:

**Allowed Callback / Redirect URLs:**
```
https://example.com/dashboard
https://chat.example.com
```

**Allowed Logout URLs:**
```
https://example.com
https://chat.example.com
```

**Allowed Web Origins (CORS), if your provider asks:**
```
https://example.com
https://chat.example.com
```

Note the **Client ID** — this becomes `OIDC_CLIENT_ID`.

---

## Step 2: Define the API audience

Register an API / resource (in Auth0 this is **Applications → APIs**; in
Keycloak/Zitadel it is the client/resource audience) with:
- **Identifier / audience:** a stable identifier you choose, e.g. `https://api.example.com` (it becomes `OIDC_AUDIENCE`; just keep it consistent)
- **Signing algorithm:** RS256

⚠️ This identifier becomes your `OIDC_AUDIENCE` — it is **not** your issuer URL.

> Some providers carry the API in the `azp` claim rather than `aud`. If yours
> does, set `OIDC_AUDIENCE_CLAIM=azp` and the backend will validate that claim
> instead.

---

## Step 3: Frontend config (optional fallback)

The browser apps fetch their config at runtime from
`/onboarding/auth/config`, so `web/js/config.js` can stay blank. Fill it in
only as a static fallback for environments where that endpoint is unavailable:

```javascript
window.APP_CONFIG = {
    oidc: {
        issuer: 'https://YOUR_ISSUER',               // e.g. https://your-tenant.us.auth0.com
        clientId: 'YOUR_CLIENT_ID',                  // From Step 1
        audience: 'https://api.example.com',        // From Step 2 (⚠️ not the issuer)
        redirectUri: window.location.origin + '/dashboard',
        scope: 'openid profile email'
    },
    api: {
        baseUrl: window.location.origin,
    }
};
```

---

## Step 4: GitHub Secrets

Add these secrets to your GitHub repository (Settings → Secrets and variables → Actions):

### OIDC Secrets (the whole required surface)
- `OIDC_ISSUER` - Your provider's issuer URL (e.g. `https://your-tenant.us.auth0.com`)
- `OIDC_CLIENT_ID` - The public client ID from Step 1
- `OIDC_AUDIENCE` - The API identifier from Step 2 (e.g. `https://api.example.com`)

Everything else (authorize/token/JWKS URLs, signing keys) is discovered from
`${OIDC_ISSUER}/.well-known/openid-configuration` at runtime.

Two optional knobs exist for less common providers and are normally unset:
- `OIDC_AUDIENCE_CLAIM` - Set to `azp` if your provider carries the API there instead of `aud`
- `OIDC_FRONTEND_AUDIENCE` - Override the audience the browser apps request

### Headscale Secrets
See [docs/09_HeadscaleSetup.md](../docs/09_HeadscaleSetup.md) for full setup guide.

- `HEADSCALE_API_URL` - Your Headscale endpoint
  - If same VM: `http://headscale:8080`
  - If separate server: `https://headscale.yourdomain.com`
- `HEADSCALE_API_KEY` - Generated via `headscale apikeys create` command

### Optional
- `PUBLIC_API_HOST` - Public app origin used for install commands; defaults to `https://<your base domain>`
- `CORS_ALLOWED_ORIGINS` - CSV of browser origins allowed to call the API (your dashboard / chat / api origins)

---

## Quick Reference

| Secret | Example Value | Where to Get It |
|--------|--------------|-----------------|
| `OIDC_ISSUER` | `https://your-tenant.us.auth0.com` | Provider tenant / realm URL |
| `OIDC_CLIENT_ID` | `abc123xyz...` | Public client (Step 1) |
| `OIDC_AUDIENCE` | `https://api.example.com` | API identifier (Step 2) |
| `OIDC_AUDIENCE_CLAIM` | `aud` (default) or `azp` | Provider-dependent |
| `HEADSCALE_API_URL` | `http://headscale:8080` | Depends on deployment |
| `HEADSCALE_API_KEY` | `hskey-abc123...` | `headscale apikeys create` |

---

## What You Need to Do

### OIDC
1. ⬜ Create a public OIDC client with the callback URLs above
2. ⬜ Define the API audience `https://api.example.com`
3. ⬜ Add `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_AUDIENCE` secrets

### Headscale
4. ⬜ Deploy Headscale (see [docs/09_HeadscaleSetup.md](../docs/09_HeadscaleSetup.md))
5. ⬜ Generate API key with `docker exec headscale headscale apikeys create`
6. ⬜ Add `HEADSCALE_API_URL` and `HEADSCALE_API_KEY` secrets

### Finally
7. ⬜ Deploy and test the full flow!
