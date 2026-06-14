// OIDC integration for the chat front-end
// Provider-neutral: uses oidc-client-ts via CDN against the same issuer as
// the main dashboard and API.

(function () {
  'use strict';

  const OIDC_SDK_URL = 'https://cdnjs.cloudflare.com/ajax/libs/oidc-client-ts/3.3.0/browser/oidc-client-ts.min.js';

  let userManager = null;
  let managerInitPromise = null;

  // Mirror of the dashboard's runtime-config pattern: provider values come from
  // the API at runtime, not from source. Only issuer/clientId/audience are
  // taken from the endpoint — redirectUri must stay this origin (the endpoint
  // returns the dashboard's), and scope stays local.
  async function loadRuntimeAuthConfig(staticConfig) {
    const apiBase = (window.APP_CONFIG?.api?.baseUrl || '').replace(/\/$/, '');
    try {
      const resp = await fetch(`${apiBase}/onboarding/auth/config`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const runtime = (await resp.json())?.oidc || {};
      return {
        ...staticConfig,
        issuer: runtime.issuer || staticConfig.issuer,
        clientId: runtime.clientId || staticConfig.clientId,
        audience: runtime.audience || staticConfig.audience,
      };
    } catch (err) {
      console.warn('Runtime auth config unavailable, using static fallback:', err);
      return staticConfig;
    }
  }

  async function ensureAuthClient() {
    if (userManager) return userManager;
    if (!managerInitPromise) {
      managerInitPromise = initOidc().catch((err) => {
        managerInitPromise = null;
        throw err;
      });
    }
    return await managerInitPromise;
  }

  async function initOidc() {
    if (!window.oidc) {
      const script = document.createElement('script');
      script.src = OIDC_SDK_URL;
      script.async = true;
      await new Promise((resolve, reject) => {
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
      });
    }

    const config = await loadRuntimeAuthConfig(window.APP_CONFIG?.oidc || {});
    if (!config.issuer || !config.clientId) {
      throw new Error('Auth is not configured: missing issuer/clientId from runtime config and static fallback.');
    }

    // Some providers (e.g. Auth0) require an `audience` request param to mint a
    // JWT access token for the API; pure OIDC providers ignore it.
    const extraQueryParams = config.audience ? { audience: config.audience } : undefined;

    userManager = new window.oidc.UserManager({
      authority: config.issuer,
      client_id: config.clientId,
      redirect_uri: config.redirectUri,
      post_logout_redirect_uri: window.location.origin,
      response_type: 'code',
      scope: config.scope || 'openid profile email',
      automaticSilentRenew: true,
      extraQueryParams,
      userStore: new window.oidc.WebStorageStateStore({ store: window.localStorage }),
    });
    return userManager;
  }

  async function handleRedirectCallback() {
    const query = window.location.search;
    if (query.includes('code=') && query.includes('state=')) {
      try {
        await userManager.signinRedirectCallback();
        window.history.replaceState({}, document.title, window.location.pathname);
        return true;
      } catch (err) {
        console.error('Auth redirect error:', err);
        window.history.replaceState({}, document.title, window.location.pathname);
        return false;
      }
    }
    return false;
  }

  async function getAccessToken() {
    const client = await ensureAuthClient();
    let user = await client.getUser();
    if (user && user.expired) {
      try {
        user = await client.signinSilent();
      } catch (err) {
        console.warn('Silent token renewal failed:', err);
        user = null;
      }
    }
    if (!user || !user.access_token) {
      await login();
      return null;
    }
    return user.access_token;
  }

  async function login() {
    const client = await ensureAuthClient();
    await client.signinRedirect({
      extraQueryParams: { screen_hint: 'signup' },
    });
  }

  async function logout() {
    const client = await ensureAuthClient();
    try {
      await client.signoutRedirect({ post_logout_redirect_uri: window.location.origin });
    } catch (err) {
      console.warn('Provider logout unavailable, clearing local session:', err);
      await client.removeUser();
      window.location.href = window.location.origin;
    }
  }

  async function isAuthenticated() {
    const client = await ensureAuthClient();
    const user = await client.getUser();
    return Boolean(user && !user.expired);
  }

  async function getUser() {
    const client = await ensureAuthClient();
    const user = await client.getUser();
    return user ? user.profile : null;
  }

  async function authenticatedFetch(url, options = {}) {
    const token = await getAccessToken();
    if (!token) throw new Error('Not authenticated');
    const headers = {
      ...options.headers,
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    };
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401) {
      await login();
      throw new Error('Session expired');
    }
    return response;
  }

  async function init() {
    try {
      const client = await ensureAuthClient();
      const handled = await handleRedirectCallback();
      if (!handled) {
        // Attempt silent SSO — if the user has a provider session, this logs
        // them in without an interactive redirect.
        try {
          await client.signinSilent();
        } catch (err) {
          const code = err?.error || err?.code || '';
          if (code !== 'login_required' && code !== 'consent_required') {
            console.warn('signinSilent failed:', err);
          }
        }
      }
      return await isAuthenticated();
    } catch (err) {
      console.error('Auth init failed:', err);
      return false;
    }
  }

  window.auth = { init, login, logout, isAuthenticated, getUser, getAccessToken, fetch: authenticatedFetch };
})();
