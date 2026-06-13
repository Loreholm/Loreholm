window.APP_CONFIG = {
  // Auth values are intentionally blank in source: auth.js fetches the auth
  // configuration at runtime from `${api.baseUrl}/onboarding/auth/config`.
  // Fill these in only as a static fallback.
  auth0: {
    domain: '',
    clientId: '',
    audience: '',
    redirectUri: window.location.origin,
    scope: 'openid profile email'
  },
  api: {
    baseUrl: 'https://api.loreholm.com'
  }
};
