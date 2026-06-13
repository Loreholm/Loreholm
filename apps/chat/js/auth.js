// Auth0 integration for chat.loreholm.com
// Reuses the same Auth0 tenant and patterns as the main dashboard.

(function () {
  'use strict';

  let auth0Client = null;
  let auth0InitPromise = null;

  // Mirror of the dashboard's runtime-config pattern: tenant values come from
  // the API at runtime, not from source. Only domain/clientId/audience are
  // taken from the endpoint — redirectUri must stay this origin (the endpoint
  // returns the dashboard's), and scope stays local.
  async function loadRuntimeAuthConfig(staticConfig) {
    const apiBase = (window.APP_CONFIG?.api?.baseUrl || '').replace(/\/$/, '');
    try {
      const resp = await fetch(`${apiBase}/onboarding/auth/config`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const runtime = (await resp.json())?.auth0 || {};
      return {
        ...staticConfig,
        domain: runtime.domain || staticConfig.domain,
        clientId: runtime.clientId || staticConfig.clientId,
        audience: runtime.audience || staticConfig.audience,
      };
    } catch (err) {
      console.warn('Runtime auth config unavailable, using static fallback:', err);
      return staticConfig;
    }
  }

  async function ensureAuthClient() {
    if (auth0Client) return auth0Client;
    if (!auth0InitPromise) {
      auth0InitPromise = initAuth0().catch((err) => {
        auth0InitPromise = null;
        throw err;
      });
    }
    return await auth0InitPromise;
  }

  async function initAuth0() {
    if (!window.auth0) {
      const script = document.createElement('script');
      script.src = 'https://cdn.auth0.com/js/auth0-spa-js/2.1/auth0-spa-js.production.js';
      script.async = true;
      await new Promise((resolve, reject) => {
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
      });
    }

    const config = await loadRuntimeAuthConfig(window.APP_CONFIG?.auth0 || {});
    if (!config.domain || !config.clientId) {
      throw new Error('Auth is not configured: missing domain/clientId from runtime config and static fallback.');
    }
    auth0Client = new window.auth0.Auth0Client({
      domain: config.domain,
      clientId: config.clientId,
      authorizationParams: {
        audience: config.audience,
        redirect_uri: config.redirectUri,
        scope: config.scope,
      },
      cacheLocation: 'localstorage',
    });
    return auth0Client;
  }

  async function handleRedirectCallback() {
    const query = window.location.search;
    if (query.includes('code=') && query.includes('state=')) {
      try {
        await auth0Client.handleRedirectCallback();
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
    try {
      const client = await ensureAuthClient();
      return await client.getTokenSilently();
    } catch (err) {
      const code = err?.error || err?.code || '';
      if (code === 'login_required' || code === 'consent_required' || code === 'missing_refresh_token') {
        await login();
        return null;
      }
      throw err;
    }
  }

  async function login() {
    const client = await ensureAuthClient();
    await client.loginWithRedirect({
      authorizationParams: { screen_hint: 'signup' },
    });
  }

  async function logout() {
    const client = await ensureAuthClient();
    await client.logout({ logoutParams: { returnTo: window.location.origin } });
  }

  async function isAuthenticated() {
    const client = await ensureAuthClient();
    return await client.isAuthenticated();
  }

  async function getUser() {
    const client = await ensureAuthClient();
    return await client.getUser();
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
      await ensureAuthClient();
      const handled = await handleRedirectCallback();
      if (!handled) {
        // Attempt silent SSO — if the user has an Auth0 session on the
        // shared tenant domain, this logs them in without a redirect.
        try {
          await auth0Client.checkSession();
        } catch (err) {
          const code = err?.error || err?.code || '';
          if (code !== 'login_required' && code !== 'consent_required') {
            console.warn('checkSession failed:', err);
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
