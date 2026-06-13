/**
 * LOREHOLM.DB Local Dashboard
 * Industrial Space-Ops Edition
 */

const CONFIG = {
  TOKEN_STORAGE_KEY: "loreholm_local_dashboard_token_v1",
  POLL_INTERVAL: 10000,
  MIN_POLL_VISIBLE_MS: 700,
  LOG_MAX_LINES: 100,
  PROVIDERS: {
    openai:    { label: "OpenAI",    model: "openai/gpt-4o-mini",              keyLabel: "OpenAI API Key",    keyPlaceholder: "sk-..." },
    anthropic: { label: "Anthropic", model: "anthropic/claude-3-5-sonnet-latest", keyLabel: "Anthropic API Key", keyPlaceholder: "sk-ant-..." },
    google:    { label: "Google",    model: "gemini/gemini-2.5-flash",          keyLabel: "Google API Key",    keyPlaceholder: "AIza..." },
    groq:      { label: "Groq",          model: "groq/llama-3.3-70b-versatile", keyLabel: "Groq API Key", keyPlaceholder: "gsk_...",                           isLocal: false },
    local:     { label: "Local (Ollama)", model: "ollama/llama3.2",             keyLabel: "Ollama URL",   keyPlaceholder: "http://host.docker.internal:11434", isLocal: true  }
  },
  RECOMMENDED_MODELS: {
    openai:    ["openai/gpt-4o-mini", "openai/gpt-4o", "openai/o4-mini", "openai/o3-mini"],
    anthropic: ["anthropic/claude-3-5-haiku-latest", "anthropic/claude-3-5-sonnet-latest", "anthropic/claude-3-7-sonnet-latest"],
    google:    ["gemini/gemini-2.0-flash", "gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro"],
    groq:      ["groq/llama-3.1-8b-instant", "groq/llama-3.3-70b-versatile", "groq/gemma2-9b-it"],
    local:     []
  }
};

class StateManager {
  constructor() {
    this.authenticated = false;
    this.setupComplete = false;
    this.llmStatus = { ready: false, models: [], error: "" };
    this.dbStatus = { count: 0, databases: [], online: 0 };
    this.logs = []; // Each item: { text: string, count: number, timestamp: string }
    this.currentView = "overview";
    this.selectedProvider = "openai";
    this.providerModels = {};
    this.providerModelEntries = {};
    this.providerHasKey = {};
    this.providerBaseUrl = {};
    this.providersLoaded = false;
    this.selectedModelByProvider = StateManager._loadFavorites();
    this.favoriteWizardModel = "";
    this.selectedWizardModel = "";
    this.wizardMessages = [{ role: "assistant", content: "Hello! I'm your Loreholm.DB onboarding agent. What kind of data are you looking to organize in a graph?" }];
    this.wizardPendingApproval = null;
  }

  setAuthenticated(val) { this.authenticated = val; }
  updateLlm(data) { this.llmStatus = { ...this.llmStatus, ...data }; }
  updateDb(data) { this.dbStatus = { ...this.dbStatus, ...data }; }
  
  addLog(text) {
    const timestamp = new Date().toLocaleTimeString('en-GB', { hour12: false }) + "." + String(new Date().getMilliseconds()).padStart(3, '0');
    
    let existingIndex = -1;

    // Check last 5 logs for exact text match
    for (let i = Math.max(0, this.logs.length - 5); i < this.logs.length; i++) {
      if (this.logs[i].text === text) {
        existingIndex = i;
        break;
      }
    }

    if (existingIndex !== -1) {
      // Extract the existing log
      const log = this.logs.splice(existingIndex, 1)[0];
      log.count++;
      log.timestamp = timestamp;
      // Push it back to the bottom so it's the most recent line
      this.logs.push(log);
    } else {
      // New log entry
      this.logs.push({ text, count: 1, timestamp });
      if (this.logs.length > CONFIG.LOG_MAX_LINES) this.logs.shift();
    }
  }

  getFormattedLogs() {
    return this.logs.map(log => {
      const countLabel = log.count > 1 ? ` (x${log.count})` : "";
      return `[${log.timestamp}] ${log.text}${countLabel}`;
    }).join('\n');
  }

  saveFavorites() {
    try { localStorage.setItem("loreholm_favorite_models", JSON.stringify(this.selectedModelByProvider)); } catch (_) {}
  }

  static _loadFavorites() {
    try {
      const raw = localStorage.getItem("loreholm_favorite_models");
      if (raw) { const parsed = JSON.parse(raw); if (parsed && typeof parsed === "object") return parsed; }
    } catch (_) {}
    return {};
  }

}

const state = new StateManager();

const MODEL_TYPE_ORDER = ["reasoning", "chat", "vision", "image", "video", "audio", "embedding", "other"];
const MODEL_TYPE_LABELS = {
  reasoning: "Reasoning",
  chat: "Chat",
  vision: "Vision",
  image: "Image",
  video: "Video",
  audio: "Audio",
  embedding: "Embedding",
  other: "Other"
};
const TEXT_WIZARD_MODEL_TYPES = new Set(["chat", "reasoning"]);

/** SVG icon set (Feather/Lucide style) */
const Icons = {
  send:    `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>`,
  logout:  `<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>`,
  trash:   `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>`,
  copy:    `<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>`,
  openExt: `<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`,
  run:     `<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="5 3 19 12 5 21 5 3"/></svg>`,
  check:   `<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`,
  x:       `<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  lock:    `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`,
  refresh:    `<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`,
  starEmpty:  `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`,
  starFilled: `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`,
};

/** Markdown renderer — handles code blocks with copy buttons, inline formatting, lists */
const Markdown = {
  render(text) {
    if (!text) return '';
    // Split on fenced code blocks (``` ... ```)
    const parts = text.split(/(```[\w-]*\n?[\s\S]*?```)/g);
    return parts.map((part, i) =>
      i % 2 === 1 ? this._codeBlock(part) : this._prose(part)
    ).join('');
  },

  _codeBlock(raw) {
    const m = raw.match(/^```([\w-]*)\n?([\s\S]*?)```$/);
    if (!m) return `<pre class="md-pre"><code>${escapeHtml(raw)}</code></pre>`;
    const lang = m[1].trim();
    const code = m[2].replace(/\n$/, '');
    const id = 'cb_' + Math.random().toString(36).slice(2, 10);
    return [
      `<div class="md-code-block">`,
      `<div class="md-code-header">`,
      `<span class="md-code-lang">${escapeHtml(lang) || 'code'}</span>`,
      `<button id="${id}" class="md-copy-btn icon-btn" title="Copy" onclick="Markdown.copy('${id}')">${Icons.copy}</button>`,
      `</div>`,
      `<pre class="md-pre"><code>${escapeHtml(code)}</code></pre>`,
      `</div>`,
    ].join('');
  },

  _prose(text) {
    const lines = text.split('\n');
    const out = [];
    let listType = null;

    const closeList = () => {
      if (listType) { out.push(`</${listType}>`); listType = null; }
    };

    for (const line of lines) {
      const ul = line.match(/^[ \t]*[-*] (.+)/);
      const ol = line.match(/^[ \t]*\d+\. (.+)/);
      if (ul) {
        if (listType !== 'ul') { closeList(); out.push('<ul class="md-list">'); listType = 'ul'; }
        out.push(`<li>${this._inline(ul[1])}</li>`);
      } else if (ol) {
        if (listType !== 'ol') { closeList(); out.push('<ol class="md-list">'); listType = 'ol'; }
        out.push(`<li>${this._inline(ol[1])}</li>`);
      } else {
        closeList();
        out.push(line === '' ? '<br>' : this._inline(line) + '<br>');
      }
    }
    closeList();
    return out.join('');
  },

  _inline(text) {
    return text
      .replace(/`([^`]+)`/g, (_, c) => `<code class="md-code-inline">${escapeHtml(c)}</code>`)
      .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>');
  },

  copy(btnId) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    const code = btn.closest('.md-code-block')?.querySelector('code');
    if (!code) return;
    const text = code.textContent;
    const flash = () => {
      const orig = btn.innerHTML;
      btn.innerHTML = Icons.check;
      setTimeout(() => { btn.innerHTML = orig; }, 1500);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(flash).catch(() => this._fallbackCopy(text, flash));
    } else {
      this._fallbackCopy(text, flash);
    }
  },

  _fallbackCopy(text, callback) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); callback(); } catch (_) {}
    document.body.removeChild(ta);
  }
};

const escapeHtml = value => String(value || "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&#39;");

const inferModelTypeFromId = modelId => {
  const token = String(modelId || "").toLowerCase();
  if (token.includes("embedding")) return "embedding";
  if (token.includes("dall-e") || token.includes("image")) return "image";
  if (token.includes("sora")) return "video";
  if (token.includes("audio") || token.includes("whisper") || token.includes("tts")) return "audio";
  if (token.includes("realtime")) return "audio";
  if (token.includes("transcribe") || token.includes("diarize")) return "audio";
  if (token.includes("moderation")) return "other";
  if (token.includes("search-preview") || token.includes("search-api") || token.includes("deep-research")) return "other";
  if (token.includes("reason") || token.includes("thinking") || /(^|[-_/])o[134]($|[-_/])/.test(token)) return "reasoning";
  if (token.includes("vision") || token.includes("-vl-") || token.includes("/vl")) return "vision";
  return "chat";
};

const normalizeModelEntries = entries => {
  const source = Array.isArray(entries) ? entries : [];
  return source.map(item => {
    if (item && typeof item === "object") {
      const id = String(item.id || "").trim();
      const type = String(item.type || "").trim().toLowerCase();
      return {
        id,
        type: MODEL_TYPE_ORDER.includes(type) ? type : inferModelTypeFromId(id),
        age_label: String(item.age_label || "").trim(),
        created_at: String(item.created_at || "").trim()
      };
    }
    const id = String(item || "").trim();
    return {
      id,
      type: inferModelTypeFromId(id),
      age_label: "",
      created_at: ""
    };
  }).filter(item => item.id);
};

const getWizardTextModelIds = (models = []) => {
  const normalized = normalizeModelEntries((models || []).map(modelId => ({ id: modelId })));
  const seen = new Set();
  const textModels = [];
  normalized.forEach(entry => {
    const type = MODEL_TYPE_ORDER.includes(entry.type) ? entry.type : inferModelTypeFromId(entry.id);
    if (!TEXT_WIZARD_MODEL_TYPES.has(type)) return;
    if (seen.has(entry.id)) return;
    seen.add(entry.id);
    textModels.push(entry.id);
  });
  return textModels;
};

const providerMatchesModel = (provider, modelId) => {
  const selected = String(provider || "").trim().toLowerCase();
  const token = String(modelId || "").trim().toLowerCase();
  if (!selected || !token) return false;
  if (selected === "google") return token.startsWith("gemini/") || token.startsWith("google/");
  // Ollama models come back as raw names (e.g. "qwen3.5:27b") without a provider prefix
  if (selected === "local") return token.startsWith("ollama/") || !token.includes("/");
  return token.startsWith(`${selected}/`);
};

/** Preferences (persisted server-side in /opt/loreholm/dashboard-preferences.json) */
const Preferences = {
  async load() {
    try {
      const data = await API.request("/preferences");
      state.favoriteWizardModel = String(data?.favorite_wizard_model || "");
    } catch (err) {
      state.addLog(`Preferences load failed: ${err.message}`);
    }
  },

  async setFavoriteWizardModel(modelId) {
    const value = String(modelId || "").trim();
    try {
      const data = await API.request("/preferences", {
        method: "PUT",
        body: JSON.stringify({ favorite_wizard_model: value }),
      });
      state.favoriteWizardModel = String(data?.favorite_wizard_model || "");
      return true;
    } catch (err) {
      state.addLog(`Failed to save favorite model: ${err.message}`);
      alert(`Failed to save favorite model: ${err.message}`);
      return false;
    }
  },
};

/** API Utilities */
const API = {
  _reauthing: false,

  async request(path, options = {}) {
    const headers = {
      "Content-Type": "application/json",
      ...(options.headers || {})
    };

    try {
      const response = await fetch(`/api${path}`, { ...options, headers });

      if (response.status === 401 && path !== "/auth/handshake" && path !== "/auth/login") {
        if (this._reauthing) throw new Error("Authentication required");
        this._reauthing = true;
        try {
          await this.showLogin();
          this._reauthing = false;
          return this.request(path, options);
        } catch (err) {
          this._reauthing = false;
          throw err;
        }
      }

      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail?.error?.message || data?.error?.message || `HTTP ${response.status}`);
      return data;
    } catch (err) {
      if (!err.message?.includes("Authentication required")) {
        state.addLog(`API ERROR: ${err.message}`);
      }
      throw err;
    }
  },

  showLogin() {
    return new Promise((resolve) => {
      const overlay = document.getElementById("loginOverlay");
      if (overlay) overlay.classList.remove("hidden");
      this._loginResolve = resolve;
      this._showLoginStep(state.setupComplete ? "password" : "token");
    });
  },

  _loginResolve: null,

  _showLoginStep(step) {
    ["loginStepPassword", "loginStepToken", "loginStepSetup"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.classList.add("hidden");
    });
    if (step === "password") this._initPasswordStep();
    else if (step === "token") this._initTokenStep();
    else if (step === "setup") this._initSetupStep();
  },

  _finishLogin() {
    const overlay = document.getElementById("loginOverlay");
    if (overlay) overlay.classList.add("hidden");
    state.setAuthenticated(true);
    if (this._loginResolve) { this._loginResolve(); this._loginResolve = null; }
  },

  _initPasswordStep() {
    const stepEl = document.getElementById("loginStepPassword");
    if (stepEl) stepEl.classList.remove("hidden");
    const usernameInput = document.getElementById("loginUsername");
    const passwordInput = document.getElementById("loginPassword");
    const btn = document.getElementById("loginBtn");
    const errorEl = document.getElementById("loginError");
    if (errorEl) errorEl.classList.add("hidden");
    if (usernameInput) usernameInput.value = "";
    if (passwordInput) passwordInput.value = "";
    setTimeout(() => usernameInput && usernameInput.focus(), 50);
    const attempt = async () => {
      const username = usernameInput ? usernameInput.value.trim() : "";
      const password = passwordInput ? passwordInput.value : "";
      if (!username || !password) return;
      btn.disabled = true;
      btn.textContent = "Signing in...";
      if (errorEl) errorEl.classList.add("hidden");
      try {
        const res = await fetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password })
        });
        if (res.ok) {
          this._finishLogin();
        } else {
          const data = await res.json().catch(() => ({}));
          const msg = data?.detail?.error?.message || "Invalid username or password.";
          if (errorEl) { errorEl.textContent = msg; errorEl.classList.remove("hidden"); }
          btn.disabled = false;
          btn.textContent = "Sign In";
          if (passwordInput) { passwordInput.value = ""; passwordInput.focus(); }
        }
      } catch (_) {
        if (errorEl) { errorEl.textContent = "Connection error. Try again."; errorEl.classList.remove("hidden"); }
        btn.disabled = false;
        btn.textContent = "Sign In";
      }
    };
    if (btn) btn.onclick = attempt;
    if (passwordInput) passwordInput.onkeydown = (e) => { if (e.key === "Enter") attempt(); };
  },

  _initTokenStep() {
    const stepEl = document.getElementById("loginStepToken");
    if (stepEl) stepEl.classList.remove("hidden");
    const input = document.getElementById("loginKeyInput");
    const btn = document.getElementById("loginTokenBtn");
    const errorEl = document.getElementById("loginTokenError");
    const hintEl = document.getElementById("loginHintCmd");
    const isWin = /Win/.test(navigator.userAgent);
    if (hintEl) hintEl.textContent = isWin
      ? 'Get-Content "$env:USERPROFILE\\.loreholm\\local-dashboard.token"'
      : 'cat ~/.loreholm/local-dashboard.token';
    if (errorEl) errorEl.classList.add("hidden");
    if (input) { input.value = ""; setTimeout(() => input.focus(), 50); }
    const attempt = async () => {
      const token = input ? input.value.trim() : "";
      if (!token) return;
      btn.disabled = true;
      btn.textContent = "Verifying...";
      if (errorEl) errorEl.classList.add("hidden");
      try {
        const res = await fetch("/api/auth/handshake", {
          method: "POST",
          headers: { "X-Local-Token": token }
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          if (data.setup_required) {
            this._showLoginStep("setup");
          } else {
            this._finishLogin();
          }
        } else {
          const msg = data?.detail?.error?.message || "Invalid token. Try again.";
          if (errorEl) { errorEl.textContent = msg; errorEl.classList.remove("hidden"); }
          btn.disabled = false;
          btn.textContent = "Continue";
          if (input) input.focus();
        }
      } catch (_) {
        if (errorEl) { errorEl.textContent = "Connection error. Try again."; errorEl.classList.remove("hidden"); }
        btn.disabled = false;
        btn.textContent = "Continue";
      }
    };
    if (btn) btn.onclick = attempt;
    if (input) input.onkeydown = (e) => { if (e.key === "Enter") attempt(); };
  },

  _initSetupStep() {
    const stepEl = document.getElementById("loginStepSetup");
    if (stepEl) stepEl.classList.remove("hidden");
    const usernameInput = document.getElementById("setupUsername");
    const passwordInput = document.getElementById("setupPassword");
    const confirmInput = document.getElementById("setupPasswordConfirm");
    const btn = document.getElementById("setupAccountBtn");
    const errorEl = document.getElementById("loginSetupError");
    if (errorEl) errorEl.classList.add("hidden");
    if (usernameInput) usernameInput.value = "";
    if (passwordInput) passwordInput.value = "";
    if (confirmInput) confirmInput.value = "";
    setTimeout(() => usernameInput && usernameInput.focus(), 50);
    const attempt = async () => {
      const username = usernameInput ? usernameInput.value.trim() : "";
      const password = passwordInput ? passwordInput.value : "";
      const confirm = confirmInput ? confirmInput.value : "";
      if (!username) {
        if (errorEl) { errorEl.textContent = "Username is required."; errorEl.classList.remove("hidden"); }
        return;
      }
      if (password.length < 8) {
        if (errorEl) { errorEl.textContent = "Password must be at least 8 characters."; errorEl.classList.remove("hidden"); }
        return;
      }
      if (password !== confirm) {
        if (errorEl) { errorEl.textContent = "Passwords do not match."; errorEl.classList.remove("hidden"); }
        return;
      }
      btn.disabled = true;
      btn.textContent = "Creating account...";
      if (errorEl) errorEl.classList.add("hidden");
      try {
        const res = await fetch("/api/auth/setup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          state.setupComplete = true;
          this._finishLogin();
        } else {
          const msg = data?.detail?.error?.message || "Failed to create account. Try again.";
          if (errorEl) { errorEl.textContent = msg; errorEl.classList.remove("hidden"); }
          btn.disabled = false;
          btn.textContent = "Create Account";
        }
      } catch (_) {
        if (errorEl) { errorEl.textContent = "Connection error. Try again."; errorEl.classList.remove("hidden"); }
        btn.disabled = false;
        btn.textContent = "Create Account";
      }
    };
    if (btn) btn.onclick = attempt;
    if (confirmInput) confirmInput.onkeydown = (e) => { if (e.key === "Enter") attempt(); };
  }
};

/** Router Component */
const Router = {
  views: ["overview", "databases", "bifrost", "wizard", "query-builder", "settings"],
  
  init() {
    document.querySelectorAll(".nav-item").forEach(btn => {
      btn.addEventListener("click", () => this.navigate(btn.dataset.view));
    });
    this.navigate("overview");
  },

  navigate(viewId) {
    if (!this.views.includes(viewId)) return;
    state.currentView = viewId;
    
    // Update UI
    document.querySelectorAll(".nav-item").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.view === viewId);
    });
    
    document.getElementById("viewTitle").textContent = viewId.charAt(0).toUpperCase() + viewId.slice(1);
    
    const container = document.getElementById("viewContainer");
    const template = document.getElementById(`view-${viewId}`);
    container.innerHTML = "";
    container.appendChild(template.content.cloneNode(true));
    
    this.initView(viewId);
  },

  initView(viewId) {
    switch(viewId) {
      case "overview": Views.overview(); break;
      case "databases": Views.databases(); break;
      case "bifrost": Views.bifrost(); break;
      case "wizard": Views.wizard(); break;
      case "query-builder": Views.queryBuilder(); break;
      case "settings": Views.settings(); break;
    }
  }
};

/** View Handlers */
const Views = {
  overview() {
    this.renderModels();
    this.renderDatabases();
    
    const banner = document.getElementById("onboardingBanner");
    if (state.dbStatus.count > 0) banner.classList.add("hidden");
  },

  renderModels() {
    const list = document.getElementById("modelList");
    if (!list) return;
    const textModels = getWizardTextModelIds(state.llmStatus.models || []);
    if (textModels.length === 0) {
      list.innerHTML = `<li class="text-muted">No models configured</li>`;
      return;
    }
    list.innerHTML = textModels.map(m => `<li><span>${escapeHtml(m)}</span><span class="status-dot status-ok"></span></li>`).join('');
  },

  renderDatabases() {
    const list = document.getElementById("activeDbList");
    if (!list) return;
    if (state.dbStatus.databases.length === 0) {
      list.innerHTML = `<li class="text-muted">No databases registered</li>`;
      return;
    }
    list.innerHTML = state.dbStatus.databases.map(db => `
      <li>
        <span>${db.name} <small class="text-muted">:${db.port}</small></span>
        <span class="status-dot status-${db.status === 'online' ? 'ok' : 'bad'}"></span>
      </li>
    `).join('');
  },

  databases() {
    const grid = document.getElementById("databasesGrid");
    if (!grid) return;
    if (state.dbStatus.databases.length === 0) {
      grid.innerHTML = `<div class="panel text-center" style="grid-column: 1/-1">No databases found. Use the wizard to create one.</div>`;
      return;
    }
    grid.innerHTML = state.dbStatus.databases.map(db => `
      <div class="db-card">
        <div class="db-card-header" onclick="Views.showDbDetail('${db.database_id}')" style="cursor:pointer">
          <div class="db-info">
            <h4 style="margin:0">${db.name}</h4>
            <code style="font-size:10px">${db.database_id}</code>
          </div>
          <span class="db-badge">${db.status.toUpperCase()}</span>
        </div>
        <div class="db-meta" style="font-size:12px; color:var(--text-soft)">
          <div>Host: ${db.host}</div>
          <div>Port: ${db.port}</div>
        </div>
        <div class="db-card-actions">
          <button class="secondary-btn icon-btn" title="Open" onclick="Views.showDbDetail('${db.database_id}')">${Icons.openExt}</button>
          <button class="danger-btn icon-btn" title="Remove database" onclick="Views.deleteDb('${db.database_id}', '${db.name}')">${Icons.trash}</button>
        </div>
      </div>
    `).join('');
  },

  async loadConfiguredProviders() {
    try {
      const data = await API.request("/wizard/bifrost/providers");
      const list = Array.isArray(data?.providers) ? data.providers : [];
      const hasKey = {};
      const baseUrls = {};
      list.forEach(item => {
        const name = String(item?.provider || "").trim().toLowerCase();
        if (!name) return;
        hasKey[name] = !!item.has_key;
        if (item.base_url) baseUrls[name] = String(item.base_url);
        const models = Array.isArray(item.models) ? item.models : [];
        if (models.length) {
          state.providerModelEntries[name] = models.map(id => ({ id: String(id) }));
          state.providerModels[name] = models.map(String);
        }
      });
      state.providerHasKey = hasKey;
      state.providerBaseUrl = baseUrls;
      state.providersLoaded = true;
    } catch (err) {
      // Bifrost may be starting up — keep defaults; pollStatus will surface errors.
    }
  },

  bifrost() {
    const keyInput = document.getElementById("providerApiKey");
    const baseUrlInput = document.getElementById("providerBaseUrl");
    const keyGroup = document.getElementById("providerKeyGroup");
    const baseUrlGroup = document.getElementById("providerBaseUrlGroup");
    const btnSave = document.getElementById("saveLlmConfigBtn");
    const btnDisconnect = document.getElementById("disconnectProviderBtn");
    const btnRecheck = document.getElementById("recheckLlmBtn");
    const btnLoadModels = document.getElementById("loadProviderModelsBtn");

    // Lazy-load saved-provider state on first entry; re-render once it lands.
    if (!state.providersLoaded) {
      this.loadConfiguredProviders().then(() => {
        if (state.currentView === "bifrost") this.bifrost();
      });
    }

    document.querySelectorAll(".provider-btn").forEach(btn => {
      btn.classList.toggle("selected", btn.dataset.provider === state.selectedProvider);
      btn.onclick = () => {
        state.selectedProvider = btn.dataset.provider;
        this.bifrost();
      };
    });

    const spec = CONFIG.PROVIDERS[state.selectedProvider];
    const isLocal = !!spec.isLocal;
    const hasSavedCredential = !!state.providerHasKey[state.selectedProvider];
    const savedBaseUrl = state.providerBaseUrl[state.selectedProvider] || "";

    // Toggle key vs URL field
    if (keyGroup) keyGroup.classList.toggle("hidden", isLocal);
    if (baseUrlGroup) baseUrlGroup.classList.toggle("hidden", !isLocal);

    const keyLabel = document.getElementById("providerKeyLabel");
    if (keyLabel) keyLabel.textContent = spec.keyLabel;
    if (keyInput) {
      keyInput.placeholder = hasSavedCredential
        ? "Key saved — leave blank to keep, or paste a new key to replace"
        : spec.keyPlaceholder;
      // Never reflect saved keys back to the browser; just keep the input blank.
      keyInput.value = "";
    }
    if (baseUrlInput) {
      // base_url is non-sensitive (it's a local network endpoint), so we
      // prefill it for convenience and so Load Models works without retyping.
      if (savedBaseUrl && !baseUrlInput.value) baseUrlInput.value = savedBaseUrl;
    }
    if (btnDisconnect) {
      btnDisconnect.disabled = !hasSavedCredential;
      btnDisconnect.title = hasSavedCredential
        ? `Disconnect ${spec.label}`
        : `${spec.label} is not configured`;
    }

    const getCredential = () => {
      if (isLocal) {
        const typed = baseUrlInput ? baseUrlInput.value.trim() : "";
        return typed || savedBaseUrl || spec.keyPlaceholder;
      }
      const typed = keyInput ? keyInput.value.trim() : "";
      return typed;
    };

    const getSelectedModel = () =>
      state.selectedModelByProvider[state.selectedProvider] || spec.model;

    if (btnLoadModels) btnLoadModels.onclick = async () => {
      const typedKey = !isLocal && keyInput ? keyInput.value.trim() : "";
      const typedBaseUrl = isLocal && baseUrlInput ? baseUrlInput.value.trim() : "";
      const useSaved = hasSavedCredential && (isLocal ? !typedBaseUrl : !typedKey);
      if (!useSaved) {
        if (isLocal && !typedBaseUrl) return alert("Enter an Ollama URL first (e.g. http://host.docker.internal:11434).");
        if (!isLocal && !typedKey) return alert("Enter an API key first, then load models.");
      }

      btnLoadModels.disabled = true;
      btnLoadModels.textContent = "Loading Models...";
      try {
        const requestBody = {
          provider: state.selectedProvider,
          preferred_model: state.selectedModelByProvider[state.selectedProvider] || spec.model
        };
        if (isLocal) {
          if (typedBaseUrl) requestBody.base_url = typedBaseUrl;
        } else {
          if (typedKey) requestBody.api_key = typedKey;
        }
        const discovered = await API.request("/wizard/bifrost/discover-models", {
          method: "POST",
          body: JSON.stringify(requestBody)
        });
        const modelEntries = Array.isArray(discovered?.model_entries) ? discovered.model_entries : [];
        const models = Array.isArray(discovered?.models)
          ? discovered.models
          : modelEntries.map(entry => String(entry?.id || "").trim()).filter(Boolean);
        state.providerModelEntries[state.selectedProvider] = modelEntries;
        state.providerModels[state.selectedProvider] = models;
        this.renderLlmModels();
        state.addLog(`Loaded ${models.length} model(s) for ${state.selectedProvider}.`);
      } catch (err) {
        alert(`Failed to load models: ${err.message}`);
      } finally {
        btnLoadModels.disabled = false;
        btnLoadModels.textContent = "Load Provider Models";
      }
    };
    
    if (btnSave) btnSave.onclick = async () => {
      const typedKey = !isLocal && keyInput ? keyInput.value.trim() : "";
      const typedBaseUrl = isLocal && baseUrlInput ? baseUrlInput.value.trim() : "";
      const useSaved = hasSavedCredential && (isLocal ? !typedBaseUrl : !typedKey);
      if (!useSaved) {
        if (isLocal && !typedBaseUrl) return alert("Enter an Ollama URL (e.g. http://host.docker.internal:11434).");
        if (!isLocal && !typedKey) return alert("Please enter an API key.");
      }

      btnSave.disabled = true;
      btnSave.textContent = "Connecting...";
      state.addLog(`Updating AI provider to ${state.selectedProvider}...`);

      try {
        const providerEntry = {
          provider: state.selectedProvider,
          model: getSelectedModel()
        };
        if (isLocal) {
          if (typedBaseUrl) providerEntry.base_url = typedBaseUrl;
        } else {
          if (typedKey) providerEntry.api_key = typedKey;
        }
        const saveResult = await API.request("/wizard/bifrost/config", {
          method: "POST",
          body: JSON.stringify({ providers: [providerEntry] })
        });
        if (saveResult?.configured_model_count) {
          state.addLog(
            `Configured ${saveResult.configured_model_count} model(s) for ${state.selectedProvider}.`
          );
        }
        if (keyInput) keyInput.value = "";
        state.addLog("Settings saved. Restarting AI container (this may take 10 seconds)...");
        // Wait a bit longer for restart before rechecking
        setTimeout(async () => {
          state.addLog("Checking AI connectivity...");
          const statusOk = await App.pollStatus();
          await Views.loadConfiguredProviders();
          Views.bifrost();
          if (!statusOk) {
            state.addLog("Connectivity check did not complete. Verify your local dashboard authentication token.");
          }
          btnSave.disabled = false;
          btnSave.textContent = "Connect & Save";
        }, 8000);
      } catch (err) {
        alert(`Connection failed: ${err.message}`);
        btnSave.disabled = false;
        btnSave.textContent = "Connect & Save";
      }
    };

    if (btnDisconnect) btnDisconnect.onclick = async () => {
      const provider = state.selectedProvider;
      const specLabel = CONFIG.PROVIDERS[provider]?.label || provider;
      const confirmed = window.confirm(`Disconnect ${specLabel} and remove its configured models?`);
      if (!confirmed) return;

      btnDisconnect.disabled = true;
      btnDisconnect.textContent = "Disconnecting...";
      state.addLog(`Disconnecting provider ${provider}...`);

      try {
        const result = await API.request("/wizard/bifrost/disconnect-provider", {
          method: "POST",
          body: JSON.stringify({ provider })
        });

        state.providerModels[provider] = [];
        state.providerModelEntries[provider] = [];
        state.providerHasKey[provider] = false;
        delete state.providerBaseUrl[provider];
        state.selectedModelByProvider[provider] = CONFIG.PROVIDERS[provider]?.model || "";
        state.saveFavorites();
        if (keyInput) keyInput.value = "";
        if (baseUrlInput) baseUrlInput.value = "";

        const removedCount = Number(result?.removed_model_count || 0);
        state.addLog(`Disconnected ${provider}. Removed ${removedCount} configured model(s).`);
        await App.pollStatus(true);
        await this.loadConfiguredProviders();
        this.bifrost();
      } catch (err) {
        alert(`Disconnect failed: ${err.message}`);
      } finally {
        btnDisconnect.disabled = false;
        btnDisconnect.textContent = "Disconnect Provider";
      }
    };

    if (btnRecheck) btnRecheck.onclick = async () => { 
      btnRecheck.disabled = true;
      btnRecheck.textContent = "Verifying...";
      state.addLog("Manual verification of AI and Database status started...");
      try {
        const statusOk = await App.pollStatus(true);
        this.renderLlmModels();
        if (statusOk) {
          state.addLog("Verification complete.");
        } else {
          state.addLog("Verification failed. Check authentication and retry.");
          alert("Verification failed. Re-enter your local dashboard token and try again.");
        }
      } finally {
        btnRecheck.disabled = false;
        btnRecheck.textContent = "Verify Connection";
      }
    };

    this.renderLlmModels();
  },

  renderLlmModels() {
    const grid = document.getElementById("bifrostModelGrid");
    if (!grid) return;
    if (!state.llmStatus.ready) {
      const errorText = escapeHtml(state.llmStatus.error || "No AI model is currently reachable.");
      grid.innerHTML = `
        <div class="panel llm-status-panel">
          <div class="llm-status-row">
            <span class="status-dot status-bad"></span>
            <strong>AI provider offline</strong>
          </div>
          <div class="llm-status-error">${errorText}</div>
        </div>
      `;
      return;
    }
    const selectedProvider = state.selectedProvider;
    const cachedEntries = normalizeModelEntries(
      Array.isArray(state.providerModelEntries[selectedProvider])
        ? state.providerModelEntries[selectedProvider]
        : []
    ).filter(entry => providerMatchesModel(selectedProvider, entry.id));

    const statusEntries = normalizeModelEntries(
      (state.llmStatus.models || []).map(modelId => ({ id: modelId }))
    ).filter(entry => providerMatchesModel(selectedProvider, entry.id));

    const allEntries = cachedEntries.length > 0 ? cachedEntries : statusEntries;
    // Only show text-to-text models
    const entries = allEntries.filter(e => TEXT_WIZARD_MODEL_TYPES.has(e.type));
    if (entries.length === 0) {
      grid.innerHTML = `<div class="text-muted">No text models detected for ${escapeHtml(selectedProvider)}. Click "Load Models" after entering your API key.</div>`;
      return;
    }

    const preferred = state.selectedModelByProvider[selectedProvider] || "";

    grid.innerHTML = entries.map(entry => {
      const isFav = entry.id === preferred;
      return `
        <div class="panel llm-model-row">
          <span>${escapeHtml(entry.id)}</span>
          <button class="model-star-btn icon-btn ${isFav ? 'model-star-active' : ''}" data-model-id="${escapeHtml(entry.id)}" title="${isFav ? 'Default model for wizard' : 'Set as default'}">
            ${isFav ? Icons.starFilled : Icons.starEmpty}
          </button>
        </div>`;
    }).join("");

    grid.querySelectorAll(".model-star-btn").forEach(btn => {
      btn.onclick = () => {
        state.selectedModelByProvider[selectedProvider] = btn.dataset.modelId;
        state.saveFavorites();
        this.renderLlmModels();
      };
    });
  },

  wizard() {
    const msgContainer = document.getElementById("wizardMessages");
    const input = document.getElementById("wizardInput");
    const btnSend = document.getElementById("wizardSendBtn");
    const btnStop = document.getElementById("wizardStopBtn");
    const btnNewChat = document.getElementById("wizardNewChatBtn");
    const btnCopy = document.getElementById("wizardCopyBtn");
    if (btnCopy) btnCopy.innerHTML = Icons.copy;
    let wizardAbortController = null;
    let streamState = null; // { toolEvents: [{tool, status, ok?}], textContent: "" }

    const TOOL_LABELS = {
      list_databases: "Listed databases",
      get_database_status: "Checked database status",
      get_database_schema: "Inspected schema",
      run_readonly_query: "Ran query",
      run_query: "Ran query",
      deploy_database: "Deployed database",
      start_database: "Started database",
      redeploy_database: "Redeployed database",
    };

    const TOOL_LABELS_ACTIVE = {
      list_databases: "Listing databases",
      get_database_status: "Checking database status",
      get_database_schema: "Inspecting schema",
      run_readonly_query: "Running query",
      run_query: "Running query",
      deploy_database: "Deploying database",
      start_database: "Starting database",
      redeploy_database: "Redeploying database",
    };

    const setWizardBusy = (busy) => {
      if (busy) {
        btnSend.classList.add("hidden");
        btnStop.classList.remove("hidden");
        input.disabled = true;
      } else {
        btnStop.classList.add("hidden");
        btnSend.classList.remove("hidden");
        input.disabled = false;
        wizardAbortController = null;
        input.focus();
      }
    };

    if (btnStop) {
      btnStop.onclick = async () => {
        if (wizardAbortController) wizardAbortController.abort();
        try { await API.request("/wizard/chat/abort", { method: "POST" }); } catch (_) {}
        setWizardBusy(false);
      };
    }

    if (btnNewChat) {
      btnNewChat.onclick = () => {
        state.wizardMessages = [{ role: "assistant", content: "Hello! I'm your Loreholm.DB onboarding agent. What kind of data are you looking to organize in a graph?" }];
        state.wizardPendingApproval = null;
        renderMsgs();
        if (input) { input.value = ""; input.focus(); }
      };
    }

    const formatToolArgsForMarkdown = (args) => {
      if (!args || typeof args !== 'object') return "";
      try {
        const pretty = JSON.stringify(args, null, 2);
        if (!pretty || pretty === "{}") return "";
        return "\n  ```json\n" + pretty.split("\n").map(l => "  " + l).join("\n") + "\n  ```";
      } catch (_) { return ""; }
    };

    const messagesToMarkdown = () => {
      const lines = ["# Database Setup Wizard Chat", ""];
      for (const m of state.wizardMessages) {
        if (m.role === "user") {
          lines.push("## You", "", m.content, "");
        } else if (m.role === "assistant") {
          lines.push("## Assistant", "", m.content, "");
        } else if (m.role === "tool_summary") {
          lines.push("## Tool calls", "");
          for (const e of (m.events || [])) {
            const mark = e.ok ? "✅" : "❌";
            const name = e.tool || e.label || "tool";
            lines.push(`- ${mark} **${name}** — ${e.label || name}${formatToolArgsForMarkdown(e.arguments)}`);
          }
          lines.push("");
        } else if (m.role === "approval_request") {
          const label = APPROVAL_LABELS[m.tool_name] || m.tool_name || "Action";
          const argsBlock = formatToolArgsForMarkdown(m.arguments);
          if (m.resolved) {
            lines.push(`> **Approval — ${label}:** ${m.approved ? "approved" : "denied"}${argsBlock}`, "");
          } else {
            lines.push(`> **Approval requested — ${label}** (pending)${argsBlock}`, "");
          }
        }
      }
      return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
    };

    if (btnCopy) {
      btnCopy.onclick = () => {
        const md = messagesToMarkdown();
        const flash = () => {
          const orig = btnCopy.innerHTML;
          btnCopy.innerHTML = Icons.check;
          setTimeout(() => { btnCopy.innerHTML = orig; }, 1500);
        };
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(md).then(flash).catch(() => Markdown._fallbackCopy(md, flash));
        } else {
          Markdown._fallbackCopy(md, flash);
        }
      };
    }

    const APPROVAL_LABELS = {
      deploy_database: "Deploy Database",
      run_query: "Run Query",
    };

    const renderApprovalArgs = (toolName, args) => {
      if (!args || typeof args !== 'object') return '';
      const items = Object.entries(args)
        .filter(([, v]) => v !== null && v !== undefined && v !== '')
        .map(([k, v]) => `<li><span class="arg-key">${escapeHtml(k)}:</span> <code>${escapeHtml(String(v))}</code></li>`)
        .join('');
      return items ? `<ul class="approval-args">${items}</ul>` : '';
    };

    const renderMsgs = () => {
      let html = state.wizardMessages.map(m => {
        if (m.role === "tool_summary") {
          const summaryHtml = (m.events || []).map(e =>
            `<span class="tool-summary-line ${e.ok ? 'tool-ok' : 'tool-err'}">${e.ok ? Icons.check : Icons.x} ${escapeHtml(e.label)}</span>`
          ).join('');
          return `<div class="wizard-msg tool-summary">${summaryHtml}</div>`;
        }
        if (m.role === "approval_request") {
          const label = APPROVAL_LABELS[m.tool_name] || m.tool_name;
          const argsHtml = renderApprovalArgs(m.tool_name, m.arguments);
          if (m.resolved) {
            const cls = m.approved ? 'approval-resolved-ok' : 'approval-resolved-denied';
            const text = m.approved ? `Approved: ${label}` : `Denied: ${label}`;
            return `<div class="wizard-msg approval-request ${cls}"><span class="approval-status-icon">${m.approved ? Icons.check : Icons.x}</span> ${escapeHtml(text)}</div>`;
          }
          return `
            <div class="wizard-msg approval-request" id="approval-card">
              <div class="approval-header">
                <span class="approval-icon">${Icons.lock}</span>
                <strong>Permission required</strong>
              </div>
              <p class="approval-desc">The assistant wants to <strong>${escapeHtml(label)}</strong>:</p>
              ${argsHtml}
              <div class="approval-actions">
                <button class="primary-btn" onclick="Views.wizardApprove(true)">Approve</button>
                <button class="danger-btn" onclick="Views.wizardApprove(false)">Deny</button>
              </div>
            </div>`;
        }
        if (m.role === "assistant") {
          return `<div class="wizard-msg assistant">${Markdown.render(m.content)}</div>`;
        }
        if (m.role === "user") {
          return `<div class="wizard-msg user">${escapeHtml(m.content)}</div>`;
        }
        return '';
      }).join('');

      // Render in-progress streaming state
      if (streamState) {
        if (streamState.toolEvents.length > 0) {
          const linesHtml = streamState.toolEvents.map(e => {
            if (e.status === "running") {
              const label = TOOL_LABELS_ACTIVE[e.tool] || e.tool;
              return `<span class="tool-summary-line tool-running"><span class="tool-spinner"></span> ${escapeHtml(label)}</span>`;
            }
            const label = TOOL_LABELS[e.tool] || e.tool;
            return `<span class="tool-summary-line ${e.ok ? 'tool-ok' : 'tool-err'}">${e.ok ? Icons.check : Icons.x} ${escapeHtml(label)}</span>`;
          }).join('');
          html += `<div class="wizard-msg tool-summary">${linesHtml}</div>`;
        }
        if (streamState.textContent) {
          html += `<div class="wizard-msg assistant">${Markdown.render(streamState.textContent)}</div>`;
        }
      }

      msgContainer.innerHTML = html;
      msgContainer.scrollTop = msgContainer.scrollHeight;
    };

    this.renderWizardModelPicker();
    renderMsgs();

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        btnSend.click();
      }
    });

    const chatMessages = () => state.wizardMessages.filter(m => m.role === "user" || m.role === "assistant");

    const wizardChatError = (message) => {
      const isInvalidModel = /invalid model/i.test(message);
      const text = isInvalidModel
        ? `The selected model is not recognized by the provider. Go to **AI Models** and pick a valid model, then try again.\n\n_Details: ${message}_`
        : `Something went wrong: ${message}`;
      state.wizardMessages.push({ role: "assistant", content: text });
      streamState = null;
      renderMsgs();
    };

    // --- SSE stream consumer ---
    const consumeSSEStream = async (body, signal) => {
      streamState = { toolEvents: [], textContent: "" };
      let deployedDb = false;

      const response = await fetch("/api/wizard/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal,
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.detail?.error?.message || `HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Parse SSE events from buffer
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          let eventType = "message";
          let eventData = "";
          for (const line of raw.split("\n")) {
            if (line.startsWith("event: ")) eventType = line.slice(7);
            else if (line.startsWith("data: ")) eventData = line.slice(6);
          }
          if (!eventData) continue;
          let data;
          try { data = JSON.parse(eventData); } catch { continue; }

          switch (eventType) {
            case "tool_start":
              streamState.toolEvents.push({ tool: data.tool, status: "running", arguments: data.arguments || null });
              renderMsgs();
              break;
            case "tool_end":
              // Update matching running entry to done
              for (let i = streamState.toolEvents.length - 1; i >= 0; i--) {
                if (streamState.toolEvents[i].tool === data.tool && streamState.toolEvents[i].status === "running") {
                  streamState.toolEvents[i].status = "done";
                  streamState.toolEvents[i].ok = data.ok;
                  break;
                }
              }
              if (data.tool === "deploy_database" && data.ok) deployedDb = true;
              renderMsgs();
              break;
            case "text_delta":
              streamState.textContent += data.content;
              renderMsgs();
              break;
            case "pending_approval": {
              // Commit any tool events so far
              const doneEvents = streamState.toolEvents.filter(e => e.status === "done");
              if (doneEvents.length > 0) {
                state.wizardMessages.push({ role: "tool_summary", events: doneEvents.map(e => ({ tool: e.tool, ok: e.ok, label: TOOL_LABELS[e.tool] || e.tool, arguments: e.arguments || null })) });
              }
              streamState = null;
              state.wizardPendingApproval = data;
              state.wizardMessages.push({
                role: "approval_request",
                tool_name: data.tool_name,
                arguments: data.arguments,
                resolved: false,
              });
              renderMsgs();
              return;
            }
            case "done": {
              // Commit all streamed content to messages
              const finishedEvents = streamState.toolEvents.filter(e => e.status === "done");
              const text = streamState.textContent;
              streamState = null;
              if (finishedEvents.length > 0) {
                state.wizardMessages.push({ role: "tool_summary", events: finishedEvents.map(e => ({ tool: e.tool, ok: e.ok, label: TOOL_LABELS[e.tool] || e.tool, arguments: e.arguments || null })) });
              }
              if (text) {
                state.wizardMessages.push({ role: "assistant", content: text });
              }
              renderMsgs();
              if (deployedDb) await App.pollStatus();
              this.updateRecommendation();
              return;
            }
            case "error":
              streamState = null;
              wizardChatError(data.message);
              return;
          }
        }
      }
      // Stream ended without a done event — commit whatever we have
      if (streamState) {
        const events = streamState.toolEvents.filter(e => e.status === "done");
        const text = streamState.textContent;
        streamState = null;
        if (events.length > 0) {
          state.wizardMessages.push({ role: "tool_summary", events: events.map(e => ({ tool: e.tool, ok: e.ok, label: TOOL_LABELS[e.tool] || e.tool, arguments: e.arguments || null })) });
        }
        if (text) {
          state.wizardMessages.push({ role: "assistant", content: text });
        }
        renderMsgs();
        if (deployedDb) await App.pollStatus();
        this.updateRecommendation();
      }
    };

    btnSend.onclick = async () => {
      const text = input.value.trim();
      if (!text) return;
      if (state.wizardPendingApproval) return;

      state.wizardMessages.push({ role: "user", content: text });
      input.value = "";
      renderMsgs();

      wizardAbortController = new AbortController();
      setWizardBusy(true);
      try {
        await consumeSSEStream.call(this, {
          messages: chatMessages(),
          model: state.selectedWizardModel || undefined,
        }, wizardAbortController.signal);
      } catch (err) {
        if (err.name === "AbortError") { streamState = null; return; }
        state.addLog(`Wizard Error: ${err.message}`);
        streamState = null;
        wizardChatError(err.message);
      } finally {
        setWizardBusy(false);
      }
    };

    Views.wizardApprove = async (approved) => {
      const pa = state.wizardPendingApproval;
      if (!pa) return;
      state.wizardPendingApproval = null;

      const approvalMsg = [...state.wizardMessages].reverse().find(m => m.role === "approval_request" && !m.resolved);
      if (approvalMsg) { approvalMsg.resolved = true; approvalMsg.approved = approved; }
      renderMsgs();

      wizardAbortController = new AbortController();
      setWizardBusy(true);
      try {
        const body = {
          messages: chatMessages(),
          model: state.selectedWizardModel || undefined,
          conversation_state: pa.conversation_state,
        };
        if (approved) {
          body.approved_tool_call_id = pa.tool_call_id;
        } else {
          body.denied_tool_call_id = pa.tool_call_id;
        }
        await consumeSSEStream.call(this, body, wizardAbortController.signal);
      } catch (err) {
        if (err.name === "AbortError") { streamState = null; return; }
        state.addLog(`Wizard Error: ${err.message}`);
        streamState = null;
        wizardChatError(err.message);
      } finally {
        setWizardBusy(false);
      }
    };
  },

  async updateRecommendation() {
    const panel = document.getElementById("recommendationPanel");
    const actions = document.getElementById("recommendationActions");
    // The recommendation endpoint's WizardMessage schema only accepts
    // role in {system,user,assistant} with a non-empty content string.
    // state.wizardMessages also contains tool_summary and approval_request
    // rows rendered in the UI — filter those out so a wizard turn that
    // used tools doesn't 422 the probe.
    const chatOnly = state.wizardMessages.filter(
      m => (m.role === "user" || m.role === "assistant")
        && typeof m.content === "string"
        && m.content.length > 0
    );
    if (chatOnly.length === 0) return;
    try {
      const rec = await API.request("/wizard/recommendation", {
        method: "POST",
        body: JSON.stringify({
          messages: chatOnly,
          model: state.selectedWizardModel || undefined
        })
      });
      if (rec.ready_to_create) {
        panel.innerHTML = `
          <div style="color:var(--accent); font-weight:700">${rec.name}</div>
          <div style="font-family:var(--font-mono); font-size:11px; margin-bottom:10px">${rec.database_id}</div>
          <p style="font-size:13px">${rec.reasoning}</p>
        `;
        panel.classList.remove("empty");
        actions.classList.remove("hidden");
        document.getElementById("createDbBtn").onclick = () => this.deployDb(rec);
      }
    } catch (err) { /* silent fail on rec */ }
  },

  renderWizardModelPicker() {
    const select = document.getElementById("wizardModelSelect");
    if (!select) return;
    const favBtn = document.getElementById("wizardModelFavoriteBtn");

    const textModels = getWizardTextModelIds(state.llmStatus.models || []);
    const previousValue = state.selectedWizardModel || "";
    select.innerHTML = "";

    const updateFavBtn = () => {
      if (!favBtn) return;
      const current = state.selectedWizardModel || "";
      const isFav = !!current && state.favoriteWizardModel === current;
      favBtn.innerHTML = isFav ? Icons.starFilled : Icons.starEmpty;
      favBtn.classList.toggle("model-star-active", isFav);
      favBtn.title = isFav
        ? "Default wizard model (click to unfavorite)"
        : "Set as default wizard model";
      favBtn.disabled = !current;
    };

    if (textModels.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No text LLM models available";
      select.appendChild(option);
      select.disabled = true;
      state.selectedWizardModel = "";
      updateFavBtn();
      return;
    }

    textModels.forEach(modelId => {
      const option = document.createElement("option");
      option.value = modelId;
      option.textContent = modelId;
      select.appendChild(option);
    });

    // Preference order: existing selection > saved favorite > first model
    let nextValue = "";
    if (previousValue && textModels.includes(previousValue)) {
      nextValue = previousValue;
    } else if (state.favoriteWizardModel && textModels.includes(state.favoriteWizardModel)) {
      nextValue = state.favoriteWizardModel;
    } else {
      nextValue = textModels[0];
    }
    select.value = nextValue;
    state.selectedWizardModel = nextValue;
    select.disabled = false;
    select.onchange = () => {
      state.selectedWizardModel = select.value.trim();
      updateFavBtn();
    };

    if (favBtn) {
      favBtn.onclick = async () => {
        const current = state.selectedWizardModel || "";
        if (!current) return;
        const next = state.favoriteWizardModel === current ? "" : current;
        favBtn.disabled = true;
        const ok = await Preferences.setFavoriteWizardModel(next);
        favBtn.disabled = false;
        if (ok) updateFavBtn();
      };
    }
    updateFavBtn();
  },

  async deleteDb(databaseId, displayName) {
    if (!window.confirm(`Remove database "${displayName}"?\n\nThis will stop and delete the container. This cannot be undone.`)) return;
    try {
      await API.request(`/databases/${encodeURIComponent(databaseId)}`, { method: "DELETE" });
      state.addLog(`Database ${databaseId} removed.`);
      await App.pollStatus(true);
      if (state.currentView === "databases") Views.databases();
    } catch (err) {
      alert(`Failed to remove database: ${err.message}`);
    }
  },

  async deployDb(rec) {
    state.addLog(`Deploying database ${rec.database_id}...`);
    try {
      await API.request("/databases", {
        method: "POST",
        body: JSON.stringify({
          database_id: rec.database_id,
          name: rec.name,
          sslmode: rec.sslmode
        })
      });
      state.addLog(`Database ${rec.database_id} deployed successfully.`);
      Router.navigate("databases");
    } catch (err) {
      alert(`Deployment failed: ${err.message}`);
    }
  },

  queryBuilder() {
    const select = document.getElementById("queryDbSelect");
    select.innerHTML = state.dbStatus.databases.map(db => `<option value="${db.database_id}">${db.name}</option>`).join('');
    
    const btnGen = document.getElementById("generateQueryBtn");
    btnGen.onclick = async () => {
      const goal = document.getElementById("queryGoal").value.trim();
      const dbId = select.value;
      if (!goal) return;

      btnGen.disabled = true;
      btnGen.textContent = "Building Query...";
      
      try {
        const res = await API.request("/wizard/prompt-draft", {
          method: "POST",
          body: JSON.stringify({ goal, database_id: dbId })
        });
        
        document.getElementById("queryResult").classList.remove("hidden");
        document.getElementById("queryTitle").textContent = res.title;
        document.getElementById("queryOutput").textContent = res.prompt;
        document.getElementById("queryNotes").textContent = res.notes;
        
        document.getElementById("copyQueryBtn").onclick = () => {
          navigator.clipboard.writeText(res.prompt);
          state.addLog("Copied Cypher query");
        };

        document.getElementById("runGeneratedQueryBtn").onclick = async () => {
          state.addLog(`Executing generated query on ${dbId}...`);
          try {
            const result = await API.request(`/databases/${dbId}/query`, {
              method: "POST",
              body: JSON.stringify({ cypher: res.prompt })
            });
            state.addLog(`Query completed: ${result.row_count} rows returned.`);
            Views.showDbDetail(dbId);
            // Switch to query tab in modal
            setTimeout(() => {
              const tab = document.querySelector('[data-tab="query"]');
              if (tab) tab.click();
              const input = document.getElementById("queryInput");
              if (input) input.value = res.prompt;
              const qResult = document.getElementById("queryResult");
              if (qResult) qResult.innerHTML = `<pre class="code-preview">${JSON.stringify(result.rows, null, 2)}</pre>`;
            }, 100);
          } catch (err) {
            alert(`Execution failed: ${err.message}`);
          }
        };
      } catch (err) {
        alert(err.message);
      } finally {
        btnGen.disabled = false;
        btnGen.textContent = "Build Cypher Query";
      }
    };
  },

  showDbDetail(dbId) {
    const overlay = document.getElementById("dbDetailOverlay");
    const close = document.getElementById("closeModalBtn");
    const db = state.dbStatus.databases.find(d => d.database_id === dbId);
    
    document.getElementById("modalDbName").textContent = db.name;
    overlay.classList.remove("hidden");
    
    close.onclick = () => overlay.classList.add("hidden");
    
    this.loadTab("health", dbId);
    document.querySelectorAll(".tab-btn").forEach(btn => {
      btn.onclick = () => {
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        this.loadTab(btn.dataset.tab, dbId);
      };
    });
  },

  async loadTab(tabId, dbId) {
    const content = document.getElementById("tabContent");
    content.innerHTML = `<div class="loading-item">Loading ${tabId}...</div>`;
    
    try {
      if (tabId === "health") {
        const data = await API.request(`/databases/${dbId}/health`);
        content.innerHTML = `
          <div class="overview-grid">
            <div class="panel">
              <h3>Stats</h3>
              <p>Nodes: <strong>${data.node_count}</strong></p>
              <p>Edges: <strong>${data.edge_count}</strong></p>
            </div>
            <div class="panel">
              <h3>System</h3>
              <p>Engine: <strong>${data.engine}</strong></p>
              <p>Version: <strong>${data.version}</strong></p>
            </div>
          </div>
        `;
      } else if (tabId === "schema") {
        const data = await API.request(`/databases/${dbId}/schema`);
        content.innerHTML = `
          <div class="panel">
            <h3>Labels</h3>
            <div style="display:flex; gap:8px; flex-wrap:wrap">
              ${data.labels.map(l => `<span class="db-badge">${l}</span>`).join('')}
            </div>
            <h3 style="margin-top:20px">Relationships</h3>
            <div style="display:flex; gap:8px; flex-wrap:wrap">
              ${data.relationships.map(r => `<span class="db-badge" style="border-color:var(--primary); color:var(--primary)">${r}</span>`).join('')}
            </div>
          </div>
        `;
      } else if (tabId === "authored-schema") {
        await SchemaEditor.render(content, dbId);
      } else if (tabId === "query") {
        content.innerHTML = `
          <div class="panel">
            <textarea id="queryInput" style="width:100%; height:100px; font-family:var(--font-mono)" placeholder="MATCH (n) RETURN n LIMIT 10"></textarea>
            <button id="runQueryBtn" class="primary-btn icon-btn" title="Run Query" style="margin-top:10px">${Icons.run}</button>
            <div id="queryResult" style="margin-top:20px; overflow:auto; max-height:300px"></div>
          </div>
        `;
        document.getElementById("runQueryBtn").onclick = async () => {
          const cypher = document.getElementById("queryInput").value;
          const res = await API.request(`/databases/${dbId}/query`, {
            method: "POST",
            body: JSON.stringify({ cypher })
          });
          document.getElementById("queryResult").innerHTML = `<pre class="code-preview">${JSON.stringify(res.rows, null, 2)}</pre>`;
        };
      } else if (tabId === "graph") {
        content.innerHTML = `<div class="panel">Graph visualization coming in v1.1. For now, use the Query Console to explore data.</div>`;
      }
    } catch (err) {
      content.innerHTML = `<div class="panel text-danger">Error: ${err.message}</div>`;
    }
  },

  async settings() {
    const listEl = document.getElementById("apiKeysList");
    const createBtn = document.getElementById("createKeyBtn");
    const labelInput = document.getElementById("newKeyLabel");
    const resultEl = document.getElementById("newKeyResult");
    const keyValueEl = document.getElementById("newKeyValue");
    const copyBtn = document.getElementById("copyNewKeyBtn");

    const renderKeys = async () => {
      if (!listEl) return;
      listEl.innerHTML = `<div class="loading-item">Loading...</div>`;
      try {
        const data = await API.request("/auth/keys");
        const keys = data.keys || [];
        if (keys.length === 0) {
          listEl.innerHTML = `<p class="text-muted">No API keys yet. Generate one above.</p>`;
          return;
        }
        listEl.innerHTML = `
          <table class="keys-table" style="width:100%;border-collapse:collapse">
            <thead>
              <tr style="text-align:left;font-size:12px;color:var(--text-soft);border-bottom:1px solid var(--border,#333)">
                <th style="padding:8px 4px">Label</th>
                <th style="padding:8px 4px">Key (hint)</th>
                <th style="padding:8px 4px">Created</th>
                <th style="padding:8px 4px"></th>
              </tr>
            </thead>
            <tbody>
              ${keys.map(k => `
                <tr data-key-id="${escapeHtml(k.key_id)}" style="border-bottom:1px solid var(--border,#222)">
                  <td style="padding:10px 4px">
                    ${escapeHtml(k.label)}
                    ${k.used ? `<span class="key-used-badge">used</span>` : ''}
                  </td>
                  <td style="padding:10px 4px;font-family:var(--font-mono);font-size:12px">${escapeHtml(k.token_hint)}</td>
                  <td style="padding:10px 4px;font-size:12px;color:var(--text-soft)">${escapeHtml(k.created_at.split("T")[0] || "")}</td>
                  <td style="padding:10px 4px;text-align:right;display:flex;gap:6px;justify-content:flex-end">
                    ${k.used ? `<button class="secondary-btn rotate-btn" title="Rotate key" data-key-id="${escapeHtml(k.key_id)}">Rotate</button>` : ''}
                    <button class="danger-btn icon-btn revoke-btn" title="Revoke key" data-key-id="${escapeHtml(k.key_id)}">${Icons.trash}</button>
                  </td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        `;
        listEl.querySelectorAll(".revoke-btn").forEach(btn => {
          btn.onclick = async () => {
            const keyId = btn.dataset.keyId;
            if (!window.confirm("Revoke this API key? Any devices using it will need a new key.")) return;
            btn.disabled = true;
            try {
              await API.request(`/auth/keys/${encodeURIComponent(keyId)}`, { method: "DELETE" });
              state.addLog(`API key ${keyId} revoked.`);
              await renderKeys();
            } catch (err) {
              alert(`Failed to revoke: ${err.message}`);
              btn.disabled = false;
            }
          };
        });
        listEl.querySelectorAll(".rotate-btn").forEach(btn => {
          btn.onclick = async () => {
            const keyId = btn.dataset.keyId;
            if (!window.confirm("Rotate this key? The old token will stop working immediately.")) return;
            btn.disabled = true;
            try {
              const result = await API.request(`/auth/keys/${encodeURIComponent(keyId)}/rotate`, { method: "POST" });
              if (keyValueEl) keyValueEl.textContent = result.token;
              if (resultEl) resultEl.classList.remove("hidden");
              if (copyBtn) copyBtn.onclick = () => {
                navigator.clipboard.writeText(result.token);
                const orig = copyBtn.innerHTML;
                copyBtn.innerHTML = Icons.check;
                setTimeout(() => { copyBtn.innerHTML = orig; }, 2000);
              };
              state.addLog(`API key rotated: ${result.label}`);
              await renderKeys();
            } catch (err) {
              alert(`Failed to rotate: ${err.message}`);
              btn.disabled = false;
            }
          };
        });
      } catch (err) {
        listEl.innerHTML = `<div class="text-danger">Failed to load keys: ${escapeHtml(err.message)}</div>`;
      }
    };

    if (createBtn) createBtn.onclick = async () => {
      const label = labelInput ? labelInput.value.trim() : "";
      if (!label) return alert("Enter a label for this key (e.g. 'My Laptop').");
      createBtn.disabled = true;
      createBtn.textContent = "Generating...";
      try {
        const result = await API.request("/auth/keys", {
          method: "POST",
          body: JSON.stringify({ label })
        });
        if (labelInput) labelInput.value = "";
        if (keyValueEl) keyValueEl.textContent = result.token;
        if (resultEl) resultEl.classList.remove("hidden");
        if (copyBtn) copyBtn.onclick = () => {
          navigator.clipboard.writeText(result.token);
          const orig = copyBtn.innerHTML;
          copyBtn.innerHTML = Icons.check;
          setTimeout(() => { copyBtn.innerHTML = orig; }, 2000);
        };
        state.addLog(`New API key created: ${result.label}`);
        await renderKeys();
      } catch (err) {
        alert(`Failed to create key: ${err.message}`);
      } finally {
        createBtn.disabled = false;
        createBtn.textContent = "Generate Key";
      }
    };

    await renderKeys();
  }
};

/**
 * Authored-schema editor module (Phase 6).
 *
 * Renders the per-database schema editor inside the database detail
 * modal's "Schema" tab. Talks to the `/databases/{id}/authored-schema/*`
 * endpoints. Soft-alias semantics (append-only, never chained) are
 * enforced at the backend; the UI just surfaces them as read-only.
 */
const SchemaEditor = {
  async render(container, dbId) {
    container.innerHTML = `<div class="loading-item">Loading authored schema...</div>`;
    let schema;
    try {
      schema = await API.request(`/databases/${encodeURIComponent(dbId)}/authored-schema`);
    } catch (err) {
      container.innerHTML = `<div class="panel text-danger">Failed to load schema: ${escapeHtml(err.message)}</div>`;
      return;
    }
    this.paint(container, dbId, schema);
  },

  paint(container, dbId, schema) {
    const entityTypes = Array.isArray(schema.entity_types) ? schema.entity_types : [];
    const relTypes = Array.isArray(schema.relationship_types) ? schema.relationship_types : [];
    const entityAliases = schema.entity_type_aliases || {};
    const relAliases = schema.relationship_type_aliases || {};

    container.innerHTML = `
      <div class="panel" style="display:flex;flex-direction:column;gap:24px">
        <div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <h3 style="margin:0">Entity Types</h3>
            <button class="secondary-btn" data-schema-action="add-entity">+ Add</button>
          </div>
          <p class="section-desc" style="margin:0 0 12px">
            Authoritative list of entity types the MCP tools will offer to LLMs writing into this database.
            Descriptions are surfaced directly to the model, so keep them specific.
          </p>
          ${this.renderTypeList(entityTypes, "entity")}
        </div>

        <div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <h3 style="margin:0">Relationship Types</h3>
            <button class="secondary-btn" data-schema-action="add-relationship">+ Add</button>
          </div>
          <p class="section-desc" style="margin:0 0 12px">
            Authoritative list of relationship types. Same write-strict / read-loose rules as entity types.
          </p>
          ${this.renderTypeList(relTypes, "relationship")}
        </div>

        <details>
          <summary style="cursor:pointer;font-weight:600">
            Entity Aliases (${Object.keys(entityAliases).length})
          </summary>
          ${this.renderAliasMap(entityAliases)}
        </details>

        <details>
          <summary style="cursor:pointer;font-weight:600">
            Relationship Aliases (${Object.keys(relAliases).length})
          </summary>
          ${this.renderAliasMap(relAliases)}
        </details>
      </div>

      <div id="schemaEditorForm"></div>
    `;

    container.querySelectorAll("[data-schema-action]").forEach(btn => {
      btn.onclick = () => this.handleAction(container, dbId, btn.dataset.schemaAction, btn.dataset.name);
    });
  },

  renderTypeList(types, kind) {
    if (!types.length) {
      return `<p class="text-muted" style="font-size:13px">No ${kind} types defined yet.</p>`;
    }
    const rows = types.map(t => `
      <tr style="border-bottom:1px solid var(--border,#222)">
        <td style="padding:10px 4px;font-weight:600">${escapeHtml(t.name)}</td>
        <td style="padding:10px 4px;color:var(--text-soft);font-size:13px">${escapeHtml(t.description || "")}</td>
        <td style="padding:10px 4px;text-align:right;white-space:nowrap">
          <button class="secondary-btn" data-schema-action="edit-${kind}" data-name="${escapeHtml(t.name)}">Edit</button>
          <button class="secondary-btn" data-schema-action="rename-${kind}" data-name="${escapeHtml(t.name)}">Rename</button>
          <button class="danger-btn" data-schema-action="delete-${kind}" data-name="${escapeHtml(t.name)}">Delete</button>
        </td>
      </tr>
    `).join("");
    return `
      <table style="width:100%;border-collapse:collapse">
        <thead>
          <tr style="text-align:left;font-size:12px;color:var(--text-soft);border-bottom:1px solid var(--border,#333)">
            <th style="padding:8px 4px;width:22%">Name</th>
            <th style="padding:8px 4px">Description</th>
            <th style="padding:8px 4px;width:220px"></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  },

  renderAliasMap(aliases) {
    const entries = Object.entries(aliases || {});
    if (!entries.length) {
      return `<p class="text-muted" style="font-size:13px;margin-top:8px">No aliases recorded.</p>`;
    }
    const rows = entries.map(([oldName, newName]) => `
      <tr>
        <td style="padding:6px 4px;font-family:var(--font-mono);font-size:13px">${escapeHtml(oldName)}</td>
        <td style="padding:6px 4px;color:var(--text-soft)">&rarr;</td>
        <td style="padding:6px 4px;font-family:var(--font-mono);font-size:13px">${escapeHtml(newName)}</td>
      </tr>
    `).join("");
    return `
      <p class="text-muted" style="font-size:12px;margin:8px 0">
        Aliases are append-only. Once recorded they cannot be removed &mdash; the write path would otherwise lose its ability to canonicalize historical writes.
      </p>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <tbody>${rows}</tbody>
      </table>
    `;
  },

  async handleAction(container, dbId, action, name) {
    const [verb, kindRaw] = action.split("-");
    const kind = kindRaw === "entity" ? "entity" : "relationship";
    const kindLabel = kind === "entity" ? "entity" : "relationship";
    const endpoint = kind === "entity" ? "entity-types" : "relationship-types";

    try {
      if (verb === "add" || verb === "edit") {
        const existingDescription = verb === "edit"
          ? await this.lookupDescription(dbId, kind, name)
          : "";
        const typedName = verb === "edit" ? name : window.prompt(`New ${kindLabel} type name:`);
        if (!typedName) return;
        const description = window.prompt(
          `Description for "${typedName}" (shown to LLMs):`,
          existingDescription,
        );
        if (!description) return;
        const res = await API.request(
          `/databases/${encodeURIComponent(dbId)}/authored-schema/${endpoint}`,
          {
            method: "PUT",
            body: JSON.stringify({ name: typedName, description }),
          },
        );
        state.addLog(`Upserted ${kindLabel} type: ${typedName}`);
        this.paint(container, dbId, res.schema);
        return;
      }

      if (verb === "delete") {
        const confirmed = window.confirm(
          `Delete ${kindLabel} type "${name}"?\n\n` +
          `Existing nodes with this label will remain in the graph (read path is loose) ` +
          `but the type will no longer be offered to LLMs for new writes. ` +
          `If you meant to rename, cancel this and use "Rename" instead.`
        );
        if (!confirmed) return;
        const res = await API.request(
          `/databases/${encodeURIComponent(dbId)}/authored-schema/${endpoint}/${encodeURIComponent(name)}`,
          { method: "DELETE" },
        );
        state.addLog(`Deleted ${kindLabel} type: ${name}`);
        this.paint(container, dbId, res.schema);
        return;
      }

      if (verb === "rename") {
        const newName = window.prompt(
          `Rename "${name}" to:`,
          name,
        );
        if (!newName || newName === name) return;
        const existingDescription = await this.lookupDescription(dbId, kind, name);
        const description = window.prompt(
          `Description for "${newName}" (LLM-facing, leave as-is to reuse the old one):`,
          existingDescription || "",
        );
        if (description === null) return;
        const res = await API.request(
          `/databases/${encodeURIComponent(dbId)}/authored-schema/${endpoint}/rename`,
          {
            method: "POST",
            body: JSON.stringify({
              old_name: name,
              new_name: newName,
              description: description || null,
            }),
          },
        );
        state.addLog(`Renamed ${kindLabel} type: ${name} → ${newName}`);
        this.paint(container, dbId, res.schema);
        return;
      }
    } catch (err) {
      alert(`Schema edit failed: ${err.message}`);
    }
  },

  async lookupDescription(dbId, kind, name) {
    try {
      const schema = await API.request(
        `/databases/${encodeURIComponent(dbId)}/authored-schema`,
      );
      const listKey = kind === "entity" ? "entity_types" : "relationship_types";
      const list = Array.isArray(schema[listKey]) ? schema[listKey] : [];
      const match = list.find(t => (t.name || "").toLowerCase() === (name || "").toLowerCase());
      return match ? String(match.description || "") : "";
    } catch (_) {
      return "";
    }
  },
};

/** Main Application */
const App = {
  async init() {
    state.addLog("Initializing Loreholm.DB Dashboard...");

    // Make body visible immediately so the login overlay is interactable if auth is needed
    document.body.classList.remove("loading");

    // Check account setup status before anything else
    try {
      const res = await fetch("/api/auth/setup-status");
      const data = await res.json().catch(() => ({}));
      state.setupComplete = data.setup_complete === true;
    } catch (_) {
      state.setupComplete = false;
    }

    // UI Setup
    this.setupConsole();
    Router.init();

    // Initial Poll
    try {
      await this.pollStatus();
    } catch (err) {
      console.error("Initial poll failed", err);
    }

    // Load configured providers so the AI Models view can pre-populate the
    // model grid and indicate which providers already have a saved key.
    try {
      await Views.loadConfiguredProviders();
      if (state.currentView === "bifrost") Views.bifrost();
    } catch (err) {
      console.error("Provider load failed", err);
    }

    // Load server-persisted preferences (favorite wizard model, etc.)
    try {
      await Preferences.load();
      if (state.currentView === "wizard") Views.renderWizardModelPicker();
    } catch (err) {
      console.error("Preferences load failed", err);
    }
    
    // Show App
    const appEl = document.getElementById("app");
    if (appEl) appEl.classList.remove("hidden");
    
    this.updateLogDisplay();
    
    // Refresh Loop
    setInterval(() => {
      this.pollStatus().catch(err => state.addLog(`Interval Poll Error: ${err.message}`));
    }, CONFIG.POLL_INTERVAL);
    
    const refreshBtn = document.getElementById("refreshAllBtn");
    if (refreshBtn) refreshBtn.onclick = () => this.pollStatus(true);
    
    const logoutBtn = document.getElementById("logoutBtn");
    if (logoutBtn) {
      logoutBtn.onclick = async () => {
        try { await fetch("/api/auth/logout", { method: "POST" }); } catch (_) {}
        location.reload();
      };
    }
  },

  updateLogDisplay() {
    const consoleEl = document.getElementById("pollLogConsole");
    if (consoleEl) {
      consoleEl.value = state.getFormattedLogs();
      consoleEl.scrollTop = consoleEl.scrollHeight;
    }
  },

  setupConsole() {
    // Console is now fixed at the bottom
  },

  async pollStatus(manual = false) {
    if (manual) state.addLog("System status check started...");
    
    const llmStatusIcon = document.getElementById("llmStatusIndicator");
    const dbStatusIcon = document.getElementById("dbStatusIndicator");
    const pollStartedAt = Date.now();
    
    const setDotStatus = (el, type) => {
      if (!el) return;
      const dot = el.classList.contains("status-dot") ? el : el.querySelector(".status-dot");
      if (!dot) return;
      dot.classList.remove('status-ok', 'status-bad', 'status-pending');
      dot.classList.add(`status-${type}`);
    };

    const setPollingState = (el, isPolling) => {
      if (!el) return;
      const dot = el.classList.contains("status-dot") ? el : el.querySelector(".status-dot");
      if (dot) dot.classList.toggle("is-polling", isPolling);
    };

    [llmStatusIcon, dbStatusIcon].forEach(el => setPollingState(el, true));

    try {
      // LLM Status
      const llm = await API.request("/wizard/bifrost/status");
      const llmChanged = state.llmStatus.ready !== llm.ready;
      state.updateLlm({
        ready: llm.ready,
        models: llm.models || [],
        error: llm.error || llm.probe_error
      });
      setDotStatus(llmStatusIcon, llm.ready ? 'ok' : 'bad');
      if (llmChanged) state.addLog(`AI Provider status changed: ${llm.ready ? 'ONLINE' : 'OFFLINE'}`);
      
      if (!llm.ready && llm.error) {
        state.addLog(`AI Provider Error: ${llm.error}`);
      }

      // DB Status
      const dbs = await API.request("/databases");
      const onlineCount = (dbs.databases || []).filter(d => d.status === 'online').length;
      const dbChanged = state.dbStatus.online !== onlineCount;
      state.updateDb({
        count: dbs.count || 0,
        databases: dbs.databases || [],
        online: onlineCount
      });
      setDotStatus(dbStatusIcon, onlineCount > 0 ? 'ok' : 'bad');
      if (dbChanged) state.addLog(`Database Cluster status changed: ${onlineCount} node(s) reachable.`);

      // Update current view if it needs data
      if (state.currentView === "overview") Views.overview();
      if (state.currentView === "databases") Views.databases();
      if (state.currentView === "bifrost") Views.renderLlmModels();
      if (state.currentView === "wizard") Views.renderWizardModelPicker();
      
      state.addLog("System status check completed.");
      this.updateLogDisplay();
      return true;

    } catch (err) {
      state.addLog(`Status Check Error: ${err.message}`);
      this.updateLogDisplay();
      return false;
    } finally {
      const elapsed = Date.now() - pollStartedAt;
      if (elapsed < CONFIG.MIN_POLL_VISIBLE_MS) {
        await new Promise(resolve => setTimeout(resolve, CONFIG.MIN_POLL_VISIBLE_MS - elapsed));
      }
      [llmStatusIcon, dbStatusIcon].forEach(el => setPollingState(el, false));
    }
  }
};

// Start App
window.addEventListener("DOMContentLoaded", () => App.init());
