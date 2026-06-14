// Derive the API base URL from the page origin so the chat app is not tied to
// any one domain. The stack's convention is a `chat.<domain>` front-end talking
// to an `api.<domain>` backend, so we swap the leading `chat.` for `api.`.
// Self-hosters on a different layout can override window.LOREHOLM_API_BASE_URL
// (e.g. via a <script> before this file) to set it explicitly.
(function () {
  function deriveApiBaseUrl() {
    if (window.LOREHOLM_API_BASE_URL) {
      return window.LOREHOLM_API_BASE_URL;
    }
    const { protocol, hostname, host } = window.location;
    if (hostname.startsWith('chat.')) {
      return `${protocol}//${host.replace(/^chat\./, 'api.')}`;
    }
    // Same-origin fallback (e.g. a single-host deployment serving chat + API).
    return window.location.origin;
  }

  window.APP_CONFIG = {
    // Auth values are intentionally blank in source: auth.js fetches the auth
    // configuration at runtime from `${api.baseUrl}/onboarding/auth/config`.
    // Fill these in only as a static fallback.
    oidc: {
      issuer: '',
      clientId: '',
      audience: '',
      redirectUri: window.location.origin,
      scope: 'openid profile email'
    },
    api: {
      baseUrl: deriveApiBaseUrl()
    }
  };
})();
