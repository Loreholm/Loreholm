// OIDC Integration (Vanilla JS)
// Provider-neutral: uses oidc-client-ts via CDN against any OIDC issuer.

(function() {
    'use strict';

    const OIDC_SDK_URL = 'https://cdnjs.cloudflare.com/ajax/libs/oidc-client-ts/3.3.0/browser/oidc-client-ts.min.js';

    let userManager = null;
    let managerInitPromise = null;
    let loginRedirectInProgress = false;
    const AUTH_CONFIG_ENDPOINT = '/onboarding/auth/config';

    async function ensureAuthClient() {
        if (userManager) {
            return userManager;
        }
        if (!managerInitPromise) {
            managerInitPromise = initOidc()
                .catch((err) => {
                    managerInitPromise = null;
                    throw err;
                });
        }
        return await managerInitPromise;
    }

    async function loginOnce() {
        if (loginRedirectInProgress) {
            return;
        }
        loginRedirectInProgress = true;
        try {
            await login();
        } finally {
            loginRedirectInProgress = false;
        }
    }

    function readErrorMessage(payload, fallback) {
        const detail = payload?.detail;
        if (typeof detail === 'string' && detail.trim()) {
            return detail;
        }
        if (detail?.error?.message) {
            return detail.error.message;
        }
        if (payload?.error?.message) {
            return payload.error.message;
        }
        return fallback;
    }

    function validateAuthConfig(config) {
        const missing = [];
        if (!config?.issuer) {
            missing.push('issuer');
        }
        if (!config?.clientId) {
            missing.push('clientId');
        }
        if (!config?.redirectUri) {
            missing.push('redirectUri');
        }
        if (missing.length > 0) {
            throw new Error(`Auth configuration is incomplete: missing ${missing.join(', ')}`);
        }
    }

    async function fetchRuntimeAuthConfig() {
        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), 4000);
        try {
            const response = await fetch(AUTH_CONFIG_ENDPOINT, {
                method: 'GET',
                cache: 'no-store',
                signal: controller.signal
            });

            if (!response.ok) {
                let payload = null;
                try {
                    payload = await response.json();
                } catch (err) {
                    payload = null;
                }

                const message = readErrorMessage(payload, `Failed to fetch runtime auth config (${response.status})`);
                if (response.status === 503) {
                    const configError = new Error(message);
                    configError.code = 'AUTH_NOT_CONFIGURED';
                    throw configError;
                }
                console.warn(message);
                return null;
            }

            const payload = await response.json();
            if (!payload?.oidc) {
                return null;
            }
            return payload.oidc;
        } catch (err) {
            if (err?.code === 'AUTH_NOT_CONFIGURED') {
                throw err;
            }
            console.warn('Using static OIDC config because runtime config fetch failed:', err);
            return null;
        } finally {
            window.clearTimeout(timeoutId);
        }
    }

    async function resolveAuthConfig() {
        const staticConfig = window.APP_CONFIG?.oidc || {};
        const runtimeConfig = await fetchRuntimeAuthConfig();
        if (!runtimeConfig) {
            return staticConfig;
        }

        const mergedConfig = { ...staticConfig, ...runtimeConfig };
        const runtimeAudienceExplicit = Boolean(runtimeConfig.frontendAudienceExplicit);
        if (staticConfig.audience && runtimeConfig.audience && staticConfig.audience !== runtimeConfig.audience) {
            if (!runtimeAudienceExplicit) {
                console.warn(
                    `Auth audience mismatch detected; runtime audience "${runtimeConfig.audience}" is implicit. Using static fallback "${staticConfig.audience}" until OIDC_FRONTEND_AUDIENCE is explicitly set.`
                );
                mergedConfig.audience = staticConfig.audience;
                return mergedConfig;
            }
            console.warn(
                `Auth audience mismatch detected; using backend runtime audience "${runtimeConfig.audience}" instead of static "${staticConfig.audience}".`
            );
        }

        if (
            staticConfig.redirectUri &&
            runtimeConfig.redirectUri &&
            staticConfig.redirectUri.startsWith('https://') &&
            runtimeConfig.redirectUri.startsWith('http://')
        ) {
            console.warn(
                `Auth redirect URI downgrade detected; using static secure redirect URI "${staticConfig.redirectUri}" instead of runtime "${runtimeConfig.redirectUri}".`
            );
            mergedConfig.redirectUri = staticConfig.redirectUri;
        }
        return mergedConfig;
    }

    // Initialize the OIDC client
    async function initOidc() {
        // Load oidc-client-ts from CDN (UMD global: window.oidc)
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

        const config = await resolveAuthConfig();
        validateAuthConfig(config);
        window.APP_CONFIG = window.APP_CONFIG || {};
        window.APP_CONFIG.oidc = config;

        // Some providers (e.g. Auth0) require an `audience` request param to
        // mint a JWT access token for the API; pure OIDC providers ignore it.
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
            userStore: new window.oidc.WebStorageStateStore({ store: window.localStorage })
        });

        return userManager;
    }

    // Handle redirect callback
    async function handleRedirectCallback() {
        const query = window.location.search;
        if (query.includes('error=')) {
            const params = new URLSearchParams(query);
            const error = params.get('error') || 'auth_error';
            const description = params.get('error_description') || 'Authentication failed.';
            console.error('Auth redirect error:', error, description);
            return {
                error,
                error_description: description
            };
        }
        if (query.includes('code=') && query.includes('state=')) {
            try {
                const user = await userManager.signinRedirectCallback();
                // Clean up URL
                window.history.replaceState({}, document.title, window.location.pathname);
                return (user && user.state) ? user.state : {};
            } catch (err) {
                console.error('Error handling redirect:', err);
                // Clean up URL even on error to prevent infinite loops
                window.history.replaceState({}, document.title, window.location.pathname);
                // Return 'error' to distinguish from no redirect
                return 'error';
            }
        }
        return false;
    }

    // Get access token
    async function getAccessToken() {
        try {
            const client = await ensureAuthClient();
            let user = await client.getUser();
            if (user && user.expired) {
                try {
                    user = await client.signinSilent();
                } catch (silentErr) {
                    console.warn('Silent token renewal failed:', silentErr);
                    user = null;
                }
            }
            if (!user || !user.access_token) {
                await loginOnce();
                return null;
            }
            return user.access_token;
        } catch (err) {
            console.error('Error getting token:', err);
            throw err;
        }
    }

    async function extractUnauthorizedMessage(response) {
        let payload = null;
        try {
            payload = await response.clone().json();
        } catch (err) {
            payload = null;
        }

        return readErrorMessage(payload, `Authentication rejected by API (${response.status})`);
    }

    // Login
    async function login() {
        try {
            const client = await ensureAuthClient();
            const returnTo = window.location.pathname + window.location.search + window.location.hash;
            await client.signinRedirect({
                state: { returnTo }
            });
        } catch (err) {
            console.error('Login failed: OIDC client is not initialized.', err);
        }
    }

    // Logout
    async function logout() {
        const client = await ensureAuthClient();
        try {
            await client.signoutRedirect({
                post_logout_redirect_uri: window.location.origin
            });
        } catch (err) {
            // Provider may not expose an end_session_endpoint; clear locally.
            console.warn('Provider logout unavailable, clearing local session:', err);
            await client.removeUser();
            window.location.href = window.location.origin;
        }
    }

    // Check if authenticated
    async function isAuthenticated() {
        const client = await ensureAuthClient();
        const user = await client.getUser();
        return Boolean(user && !user.expired);
    }

    // Get user info
    async function getUser() {
        const client = await ensureAuthClient();
        const user = await client.getUser();
        return user ? user.profile : null;
    }

    // Fetch with authentication
    async function authenticatedFetch(url, options = {}) {
        const token = await getAccessToken();
        if (!token) {
            throw new Error('Not authenticated');
        }

        const headers = {
            ...options.headers,
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
        };

        const response = await fetch(url, {
            ...options,
            headers
        });

        if (response.status === 401) {
            const message = await extractUnauthorizedMessage(response);
            throw new Error(message);
        }

        return response;
    }

    // Initialize and expose API
    async function init() {
        try {
            await ensureAuthClient();

            // Handle callback if present
            const redirectResult = await handleRedirectCallback();

            if (redirectResult && redirectResult.error) {
                const details = `${redirectResult.error}: ${redirectResult.error_description || 'Authentication failed.'}`;
                console.error('Auth redirect failed:', details);
                return false;
            }

            // If we just handled a redirect successfully, redirect to dashboard clean URL
            // This ensures the auth state is properly established
            if (redirectResult && redirectResult !== 'error') {
                const returnTo = redirectResult.returnTo;
                window.location.href = returnTo || '/dashboard';
                return true;
            }

            // If there was an error during redirect handling, stay on current page
            // and let the user see the error in console (don't redirect away)
            if (redirectResult === 'error') {
                console.error('Auth redirect failed - check OIDC configuration');
                // Don't redirect, let user stay and see the error
                return false;
            }

            // Check if we're authenticated
            const authenticated = await isAuthenticated();

            // Redirect logic
            const path = window.location.pathname;
            const isDashboard = path.startsWith('/dashboard');
            const isHome = path === '/' || path === '/index.html';

            if (authenticated && isHome) {
                // Logged in but on home page - redirect to dashboard
                window.location.href = '/dashboard';
            } else if (!authenticated && isDashboard) {
                // Not logged in but on dashboard - redirect to home
                window.location.href = '/';
            }

            return true;
        } catch (err) {
            console.error('Auth initialization failed:', err);
            return false;
        }
    }

    // Expose auth API globally
    window.auth = {
        init,
        login,
        logout,
        isAuthenticated,
        getUser,
        getAccessToken,
        fetch: authenticatedFetch
    };

    // Auto-initialize on load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
