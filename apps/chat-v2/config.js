const {protocol,hostname,host}=window.location;
window.LOREHOLM_CHAT = {
  apiBase: window.LOREHOLM_API_BASE_URL || (hostname.startsWith("chat.") ? `${protocol}//${host.replace(/^chat\./,"api.")}` : window.location.origin),
  oidc: {
    issuer: "",
    clientId: "",
    audience: "",
    scope: "openid profile email",
  },
};
