// Configuration for the frontend.
// Auth values are intentionally blank in source: the dashboard fetches its
// auth configuration at runtime from /onboarding/auth/config, which the API
// serves from its AUTH0_* environment variables. Fill these in only as a
// static fallback for environments where that endpoint is unavailable.

window.APP_CONFIG = {
    // Auth0 Configuration (runtime config from /onboarding/auth/config takes precedence)
    auth0: {
        domain: '',
        clientId: '',
        audience: '',
        redirectUri: window.location.origin + '/dashboard',
        scope: 'openid profile email'
    },

    // API Configuration
    api: {
        baseUrl: window.location.origin,  // Same origin by default
    }
};
