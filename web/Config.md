# Web Frontend Configuration

## Required Setup

You need to configure two things:
1. **Auth0 Application** (for user authentication)
2. **Auth0 API** (for backend token validation)

---

## Step 1: Create Auth0 Single Page Application

1. Go to https://manage.auth0.com/
2. Create a new "Single Page Application"
3. Configure the following settings:

**Allowed Callback URLs:**
```
https://loreholm.com/dashboard.html
```

**Allowed Logout URLs:**
```
https://loreholm.com
```

**Allowed Web Origins:**
```
https://loreholm.com
```

**Allowed Origins (CORS):**
```
https://loreholm.com
```

---

## Step 2: Create Auth0 API

1. Go to **Applications → APIs** in Auth0
2. Click **Create API**
3. Set:
   - **Name:** "loreholm API"
   - **Identifier:** `https://api.loreholm.com` (use this exactly)
   - **Signing Algorithm:** RS256
4. Click **Create**

⚠️ **This identifier becomes your AUTH0_AUDIENCE** - it's NOT your Auth0 domain!

---

## Step 3: Update Frontend Config

Copy the Domain, Client ID, and Audience to web/js/config.js:

```javascript
window.APP_CONFIG = {
    auth0: {
        domain: 'YOUR_DOMAIN.us.auth0.com',           // From Step 1 (SPA Application)
        clientId: 'YOUR_CLIENT_ID',                   // From Step 1 (SPA Application)
        audience: 'https://api.loreholm.com',        // From Step 2 (API Identifier) ⚠️ NOT your Auth0 domain!
        redirectUri: window.location.origin + '/dashboard.html',
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

### Auth0 Secrets
- ✅ `AUTH0_CLIENT_ID` - From SPA Application (you have this)
- ✅ `AUTH0_CLIENT_SECRET` - From SPA Application settings (you have this)
- ✅ `AUTH0_DOMAIN` - Your Auth0 tenant (e.g., `your-tenant.us.auth0.com`) (you have this)
- ❌ `AUTH0_AUDIENCE` - The API identifier from Step 2 (e.g., `https://api.loreholm.com`)
  - ⚠️ **This is NOT your Auth0 domain!**
  - It's the custom identifier you created in Step 2

### Headscale Secrets
See [docs/09_HeadscaleSetup.md](../docs/09_HeadscaleSetup.md) for full setup guide.

- ❌ `HEADSCALE_API_URL` - Your Headscale endpoint
  - If same VM: `http://headscale:8080`
  - If separate server: `https://headscale.yourdomain.com`
- ❌ `HEADSCALE_API_KEY` - Generated via `headscale apikeys create` command

### Optional
- `PUBLIC_API_HOST` - Defaults to `loreholm.com` if not set
- ✅ `DOMAIN` - Your main domain (you have this)

---

## Quick Reference

| Secret | Example Value | Where to Get It |
|--------|--------------|-----------------|
| `AUTH0_DOMAIN` | `your-tenant.us.auth0.com` | Auth0 Application settings |
| `AUTH0_CLIENT_ID` | `abc123xyz...` | Auth0 Application settings |
| `AUTH0_CLIENT_SECRET` | `secret-abc123...` | Auth0 Application settings |
| `AUTH0_AUDIENCE` | `https://api.loreholm.com` | Auth0 API identifier (Step 2) |
| `HEADSCALE_API_URL` | `http://headscale:8080` | Depends on deployment |
| `HEADSCALE_API_KEY` | `hskey-abc123...` | `headscale apikeys create` |

---

## What You Need to Do

### Immediate (Auth0):
1. ✅ Create Auth0 SPA Application
2. ⬜ **Create Auth0 API** with identifier `https://api.loreholm.com`
3. ⬜ Add `AUTH0_AUDIENCE` secret with that identifier
4. ⬜ Update `web/js/config.js` with your Auth0 values

### Later (Headscale):
5. ⬜ Deploy Headscale (see [docs/09_HeadscaleSetup.md](../docs/09_HeadscaleSetup.md))
6. ⬜ Generate API key with `docker exec headscale headscale apikeys create`
7. ⬜ Add `HEADSCALE_API_URL` and `HEADSCALE_API_KEY` secrets

### Finally:
8. ⬜ Deploy and test the full flow!
- `HEADSCALE_API_KEY` - Your Headscale API key
- `PUBLIC_API_HOST` - Your public domain (loreholm.com)
