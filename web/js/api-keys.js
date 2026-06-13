// API Keys Management
(function() {
    'use strict';

    const API_BASE = window.APP_CONFIG.api.baseUrl;
    
    // State
    let apiKeys = [];
    let keyToRevoke = null;
    let isInitialized = false;
    // The just-created key (plaintext) is held here while the show-key modal
    // is open so the platform picker can re-render the snippet without
    // re-fetching. Cleared when the modal closes.
    let currentNewKey = null;
    // Database target name for the just-created key, used to suffix the
    // suggested MCP server name so users registering keys for multiple
    // databases get distinct entries in their MCP client.
    let currentNewDatabaseName = null;
    // Map of database_id -> status string ('online' | 'offline' | ...)
    // populated from GET /database-targets/discover so each key card can
    // surface the live status of its bound database. Empty means discovery
    // hasn't run yet or failed soft; the UI falls back to "Unknown".
    let databaseStatusMap = {};
    // Interval handle for the periodic status poll so we can tear it down
    // if needed (e.g. page navigation in an SPA).
    let statusPollTimer = null;
    const STATUS_POLL_INTERVAL_MS = 30_000;

    // DOM Elements (populated after DOM ready)
    let elements = {};

    // Initialize when called (after auth is ready)
    function init() {
        if (isInitialized) return;
        
        elements = {
            apiKeysList: document.getElementById('apiKeysList'),
            keyCount: document.getElementById('keyCount'),
            createApiKeyBtn: document.getElementById('createApiKeyBtn'),
            createKeySidebar: document.getElementById('createKeySidebar'),
            sidebarOverlay: document.getElementById('sidebarOverlay'),
            closeSidebar: document.getElementById('closeSidebar'),
            createKeyForm: document.getElementById('createKeyForm'),
            cancelCreateKey: document.getElementById('cancelCreateKey'),
            showKeyModal: document.getElementById('showKeyModal'),
            newApiKey: document.getElementById('newApiKey'),
            copyNewKeyBtn: document.getElementById('copyNewKeyBtn'),
            mcpConfigExample: document.getElementById('mcpConfigExample'),
            mcpPlatformSelect: document.getElementById('mcpPlatformSelect'),
            mcpPlatformInstructions: document.getElementById('mcpPlatformInstructions'),
            copyMcpConfigBtn: document.getElementById('copyMcpConfigBtn'),
            closeShowKeyModal: document.getElementById('closeShowKeyModal'),
            revokeKeyModal: document.getElementById('revokeKeyModal'),
            revokeKeyName: document.getElementById('revokeKeyName'),
            cancelRevokeKey: document.getElementById('cancelRevokeKey'),
            confirmRevokeKey: document.getElementById('confirmRevokeKey'),
            dbTargetSelect: document.getElementById('dbTargetSelect'),
            dbDiscoveryStatus: document.getElementById('dbDiscoveryStatus'),
            dbRefreshBtn: document.getElementById('dbRefreshBtn'),
        };

        // Check if elements exist (we're on dashboard page)
        if (!elements.apiKeysList) {
            return;
        }

        // Bind event listeners
        if (elements.createApiKeyBtn) {
            elements.createApiKeyBtn.addEventListener('click', openCreateSidebar);
        }
        if (elements.closeSidebar) {
            elements.closeSidebar.addEventListener('click', closeCreateSidebar);
        }
        if (elements.sidebarOverlay) {
            elements.sidebarOverlay.addEventListener('click', closeCreateSidebar);
        }
        if (elements.cancelCreateKey) {
            elements.cancelCreateKey.addEventListener('click', (e) => {
                e.preventDefault();
                closeCreateSidebar();
            });
        }
        if (elements.createKeyForm) {
            elements.createKeyForm.addEventListener('submit', handleCreateKey);
        }
        if (elements.copyNewKeyBtn) {
            elements.copyNewKeyBtn.addEventListener('click', copyNewKey);
        }
        if (elements.mcpPlatformSelect) {
            // Restore the user's last choice so repeat-users don't have to
            // re-pick every time they mint a key.
            try {
                const saved = localStorage.getItem('mcpPlatformChoice');
                if (saved && [...elements.mcpPlatformSelect.options].some(o => o.value === saved)) {
                    elements.mcpPlatformSelect.value = saved;
                }
            } catch (_) { /* storage may be disabled */ }
            elements.mcpPlatformSelect.addEventListener('change', () => {
                try {
                    localStorage.setItem('mcpPlatformChoice', elements.mcpPlatformSelect.value);
                } catch (_) { /* ignore */ }
                renderMcpConfigForPlatform();
            });
        }
        if (elements.copyMcpConfigBtn) {
            elements.copyMcpConfigBtn.addEventListener('click', copyMcpConfig);
        }
        if (elements.showKeyModal) {
            // 'close' fires for every close path (button, Escape, backdrop
            // click), so this is the single place to scrub the plaintext
            // key out of the DOM. Keeps the visible close button handler
            // simple and still covers the other paths the modal exposes.
            elements.showKeyModal.addEventListener('close', () => {
                currentNewKey = null;
                currentNewDatabaseName = null;
                if (elements.newApiKey) {
                    elements.newApiKey.textContent = '';
                }
                if (elements.mcpConfigExample) {
                    elements.mcpConfigExample.textContent = '';
                }
            });
        }
        if (elements.closeShowKeyModal) {
            elements.closeShowKeyModal.addEventListener('click', (e) => {
                e.preventDefault();
                closeShowKeyModal();
            });
        }
        if (elements.cancelRevokeKey) {
            elements.cancelRevokeKey.addEventListener('click', (e) => {
                e.preventDefault();
                closeRevokeModal();
            });
        }
        if (elements.confirmRevokeKey) {
            elements.confirmRevokeKey.addEventListener('click', handleRevokeKey);
        }
        if (elements.dbRefreshBtn) {
            elements.dbRefreshBtn.addEventListener('click', (e) => {
                e.preventDefault();
                loadDiscoveredDatabases();
            });
        }

        // Close modals on backdrop click (only for show key and revoke modals)
        [elements.showKeyModal, elements.revokeKeyModal].forEach(modal => {
            if (modal) {
                modal.addEventListener('click', (e) => {
                    // Only close if clicking the backdrop (the dialog itself), not its content
                    if (e.target === modal) {
                        modal.close();
                    }
                });
                // Also handle Escape key
                modal.addEventListener('cancel', (e) => {
                    // Allow default cancel behavior (Escape key)
                });
            }
        });

        isInitialized = true;

        // Load API keys
        loadApiKeys();

        // Poll database statuses every 30s so the health badges stay
        // current without a full page reload. The poll only hits the
        // lightweight discovery endpoint and re-renders — it does NOT
        // re-fetch the key list itself.
        startStatusPoll();
    }

    function startStatusPoll() {
        if (statusPollTimer) return;
        statusPollTimer = setInterval(refreshDatabaseStatuses, STATUS_POLL_INTERVAL_MS);
    }

    // Lightweight refresh: re-fetch only the database status map and
    // re-render the existing key list against the updated statuses.
    async function refreshDatabaseStatuses() {
        try {
            const statusMap = await fetchDatabaseStatuses();
            databaseStatusMap = statusMap || {};
            if (apiKeys.length > 0 && elements.apiKeysList) {
                rerenderStatusBadges();
            }
        } catch (err) {
            console.warn('Status poll failed (soft):', err);
        }
    }

    // Update just the badge on each key card without rebuilding the
    // entire list (preserves scroll position, doesn't rebind listeners).
    function rerenderStatusBadges() {
        const cards = elements.apiKeysList.querySelectorAll('.api-key-item');
        cards.forEach((card, i) => {
            const key = apiKeys[i];
            if (!key) return;
            const badge = card.querySelector('.key-status');
            if (!badge || !key.is_active) return;
            const info = describeKeyHealth(key);
            badge.className = `key-status badge ${info.cls}`;
            badge.textContent = info.label;
        });
    }

    // Load API keys from server. Kicks off discovery in parallel so the
    // per-key database status badges render with a single paint — the two
    // requests are independent and the key list is the blocking path.
    async function loadApiKeys() {
        if (!window.auth || !elements.apiKeysList) return;

        const keysRequest = (async () => {
            const response = await window.auth.fetch(`${API_BASE}/api-keys`);
            if (!response.ok) {
                let errorMsg = `Failed to load API keys: ${response.status}`;
                try {
                    const errorData = await response.json();
                    if (errorData.detail?.error?.message) {
                        errorMsg = errorData.detail.error.message;
                    }
                } catch (e) {
                    // Response wasn't JSON
                }
                throw new Error(errorMsg);
            }
            return response.json();
        })();

        // Discovery failures are soft — the key list should still render,
        // the status badge just falls back to "Unknown". We don't want a
        // local-node outage to hide the user's key management UI.
        const statusRequest = fetchDatabaseStatuses().catch(err => {
            console.warn('Database status discovery failed:', err);
            return {};
        });

        try {
            const [data, statusMap] = await Promise.all([keysRequest, statusRequest]);
            apiKeys = data.keys;
            databaseStatusMap = statusMap || {};
            renderApiKeys(data);
        } catch (err) {
            console.error('Failed to load API keys:', err);
            if (elements.apiKeysList) {
                elements.apiKeysList.innerHTML = `<p class="error-text">${escapeHtml(err.message)}</p>`;
            }
        }
    }

    // Pull the live database inventory from the user's local node and
    // flatten it into a {database_id: status} map. Same endpoint the
    // create-key sidebar uses; cached at the request level — the sidebar
    // fetch still runs independently since it also needs the names/labels.
    async function fetchDatabaseStatuses() {
        if (!window.auth) return {};
        const response = await window.auth.fetch(`${API_BASE}/database-targets/discover`);
        if (!response.ok) {
            throw new Error(`Discovery failed: ${response.status}`);
        }
        const data = await response.json();
        const map = {};
        for (const entry of data.databases || []) {
            if (entry && entry.database_id) {
                map[entry.database_id] = entry.status || 'unknown';
            }
        }
        return map;
    }

    // Derive a single health badge for a key card that folds key status
    // and live database reachability into one at-a-glance signal.
    //   Active + online   → green  "Connected"
    //   Active + offline  → yellow "Disconnected"
    //   Active + unknown  → yellow "Checking…"
    //   Active + unbound  → muted  "No database"
    //   Revoked / Expired → their own badge (unchanged)
    function describeKeyHealth(key) {
        if (key.is_revoked) return { cls: 'badge-revoked', label: 'Revoked' };
        if (!key.is_active) return { cls: 'badge-expired', label: 'Expired' };
        const db = key.database;
        if (!db) return { cls: 'badge-expired', label: 'No database' };
        const status = databaseStatusMap[db.database_id];
        if (status === 'online') return { cls: 'badge-success', label: 'Connected' };
        if (status === 'offline') return { cls: 'badge-warning', label: 'Disconnected' };
        return { cls: 'badge-warning', label: 'Checking\u2026' };
    }

    // Render API keys list
    function renderApiKeys(data) {
        if (!elements.apiKeysList) return;

        // Update count
        if (elements.keyCount) {
            elements.keyCount.textContent = `${data.count} / ${data.max_keys} keys`;
        }

        // Disable create button if at limit
        if (elements.createApiKeyBtn) {
            const activeKeys = data.keys.filter(k => k.is_active).length;
            elements.createApiKeyBtn.disabled = activeKeys >= data.max_keys;
        }

        if (data.keys.length === 0) {
            elements.apiKeysList.innerHTML = `
                <div class="empty-state">
                    <p>No API keys yet</p>
                    <p class="hint">Create an API key to connect MCP clients</p>
                </div>
            `;
            return;
        }

        const keysHtml = data.keys.map(key => {
            const statusClass = key.is_active ? 'active' : (key.is_revoked ? 'revoked' : 'expired');
            const createdDate = new Date(key.created_at).toLocaleDateString();
            const expiresDate = new Date(key.expires_at).toLocaleDateString();
            const db = key.database;
            const dbLabel = db
                ? escapeHtml(db.name || db.database_id)
                : 'No database bound';
            const health = describeKeyHealth(key);

            return `
                <div class="api-key-item ${statusClass}">
                    <div class="key-info">
                        <span class="key-name">${escapeHtml(key.name)}</span>
                        <span class="key-status badge ${health.cls}">${health.label}</span>
                    </div>
                    <div class="key-database">
                        <span class="key-database-label">Database:</span>
                        <span class="key-database-name">${dbLabel}</span>
                    </div>
                    <div class="key-meta">
                        <span>Created: ${createdDate}</span>
                        <span>Expires: ${expiresDate}</span>
                    </div>
                    ${key.is_active ? `
                        <button class="btn btn-small btn-danger revoke-btn" data-key-id="${key.key_id}" data-key-name="${escapeHtml(key.name)}">
                            Revoke
                        </button>
                    ` : ''}
                </div>
            `;
        }).join('');

        elements.apiKeysList.innerHTML = keysHtml;

        // Bind revoke buttons
        elements.apiKeysList.querySelectorAll('.revoke-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                openRevokeModal(btn.dataset.keyId, btn.dataset.keyName);
            });
        });
    }

    // Open create sidebar
    function openCreateSidebar() {
        if (elements.createKeyForm) {
            elements.createKeyForm.reset();
        }
        if (elements.createKeySidebar) {
            elements.createKeySidebar.classList.add('open');
        }
        if (elements.sidebarOverlay) {
            elements.sidebarOverlay.classList.add('show');
        }
        // Kick off discovery every time the sidebar opens so the list
        // reflects any databases the user has added or renamed since their
        // last visit. The endpoint is cheap and fails soft.
        loadDiscoveredDatabases();
    }

    // Fetch the user's local-node database inventory over Tailnet and
    // populate the dropdown. Degrades gracefully: if the local node is
    // offline or unreachable, the dropdown falls back to "Default" only and
    // the help text surfaces the failure so the user knows the local
    // node needs attention.
    async function loadDiscoveredDatabases() {
        if (!elements.dbTargetSelect || !window.auth) return;
        const status = elements.dbDiscoveryStatus;
        const select = elements.dbTargetSelect;
        const previousValue = select.value;

        select.disabled = true;
        if (status) {
            status.textContent = 'Discovering databases on your local node…';
            status.classList.remove('error-text');
        }

        try {
            const response = await window.auth.fetch(`${API_BASE}/database-targets/discover`);
            if (!response.ok) {
                let message = `Discovery failed: ${response.status}`;
                let code = null;
                try {
                    const body = await response.json();
                    if (body.detail?.error?.message) {
                        message = body.detail.error.message;
                    }
                    if (body.detail?.error?.code) {
                        code = body.detail.error.code;
                    }
                } catch (_) {
                    /* ignore */
                }
                const err = new Error(message);
                err.code = code;
                throw err;
            }
            const data = await response.json();
            renderDiscoveredDatabases(data.databases || [], previousValue);
            if (status) {
                const count = data.count || 0;
                status.textContent = count > 0
                    ? `Found ${count} database${count === 1 ? '' : 's'} on your local node.`
                    : 'Your local node reported no databases. Create one from your local dashboard first.';
            }
        } catch (err) {
            console.warn('Database discovery failed:', err);
            renderDiscoveredDatabases([], previousValue);
            if (status) {
                // Classify the upstream error so the user gets an actionable
                // message instead of "couldn't reach your local node" for
                // every failure mode. `LOCAL_SYNC_NOT_CONFIGURED` means the
                // cloud deployment itself is missing the LOCAL_SYNC_TOKEN
                // secret — not a local-side problem the user can fix.
                let msg;
                switch (err.code) {
                    case 'LOCAL_SYNC_NOT_CONFIGURED':
                        msg = 'BYODB sync is not configured on this deployment. Contact your administrator to set LOCAL_SYNC_TOKEN.';
                        break;
                    case 'LOCAL_SYNC_UNAUTHORIZED':
                        msg = 'Your local node rejected the cloud sync token. Re-run the install command from your dashboard to refresh the shared token.';
                        break;
                    case 'LOCAL_SYNC_UNREACHABLE':
                        msg = 'Your local node is offline or unreachable over Tailnet.';
                        break;
                    default:
                        msg = `Couldn't reach your local node (${err.message || 'unknown error'}).`;
                }
                status.textContent = msg;
                status.classList.add('error-text');
            }
        } finally {
            select.disabled = false;
        }
    }

    // Rebuild the <select> options from a discovery response.
    function renderDiscoveredDatabases(databases, preservedValue) {
        const select = elements.dbTargetSelect;
        if (!select) return;
        // Keep the neutral "Default" option at the top — it's the fallback
        // when the user doesn't want to bind to a specific database.
        select.innerHTML = '<option value="">Default (no binding)</option>';
        for (const entry of databases) {
            if (!entry || !entry.database_id) continue;
            const option = document.createElement('option');
            option.value = entry.database_id;
            const statusLabel = entry.status && entry.status !== 'online' ? ` (${entry.status})` : '';
            option.textContent = `${entry.name || entry.database_id}${statusLabel}`;
            option.dataset.status = entry.status || 'unknown';
            select.appendChild(option);
        }
        // Preserve the user's previous selection if it's still in the list.
        if (preservedValue && [...select.options].some(o => o.value === preservedValue)) {
            select.value = preservedValue;
        }
    }

    // Close create sidebar
    function closeCreateSidebar() {
        if (elements.createKeySidebar) {
            elements.createKeySidebar.classList.remove('open');
        }
        if (elements.sidebarOverlay) {
            elements.sidebarOverlay.classList.remove('show');
        }
    }

    // Handle create key form submission
    async function handleCreateKey(e) {
        e.preventDefault();

        const form = e.target;
        const submitBtn = form.querySelector('button[type="submit"]');
        const originalText = submitBtn.textContent;
        
        submitBtn.disabled = true;
        submitBtn.textContent = 'Creating...';

        try {
            // The only supported creation path binds to a database that was
            // discovered from the user's local dashboard. The cloud never
            // holds database credentials, so there is no manual-override fallback.
            const selectedDatabaseId = elements.dbTargetSelect?.value?.trim() || '';
            const databaseSyncPayload = selectedDatabaseId
                ? { database_id: selectedDatabaseId, sync_mode: 'tailnet_pull' }
                : null;
            const response = await window.auth.fetch(`${API_BASE}/api-keys`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    name: form.name.value,
                    expires_days: parseInt(form.expires_days.value, 10),
                    database_sync: databaseSyncPayload,
                }),
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail?.error?.message || 'Failed to create API key');
            }

            const data = await response.json();
            
            // Close create sidebar
            closeCreateSidebar();
            
            // Show the new key
            showNewKey(data);
            
            // Refresh the list
            loadApiKeys();

        } catch (err) {
            console.error('Failed to create API key:', err);
            alert(err.message || 'Failed to create API key');
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
        }
    }

    // Show the newly created key
    function showNewKey(data) {
        if (elements.newApiKey) {
            elements.newApiKey.textContent = data.api_key;
        }

        // Hold the plaintext key so the platform picker can re-render the
        // snippet on each change without re-fetching. Cleared in
        // closeShowKeyModal.
        currentNewKey = data.api_key;
        currentNewDatabaseName = data.database?.name || null;
        renderMcpConfigForPlatform();

        if (elements.showKeyModal) {
            elements.showKeyModal.showModal();
        }
    }

    // Suggested MCP server name. Suffixed with the database target name so
    // users registering keys for multiple databases get distinct entries
    // (otherwise re-running `claude mcp add loreholm ...` would clobber
    // the previous one). Slug rules: lowercase, [a-z0-9-] only, collapsed
    // and trimmed dashes — keeps the name safe for every MCP client's
    // config key syntax.
    function buildMcpServerName(databaseName) {
        if (!databaseName) return 'loreholm';
        const slug = String(databaseName)
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '');
        return slug ? `loreholm-${slug}` : 'loreholm';
    }

    // Build the MCP install command / config snippet for whichever platform
    // the user picked in the dropdown. The MCP endpoint is the same across
    // platforms — all that differs is the wrapper format (CLI command,
    // stdio-proxied JSON for Claude Desktop, direct HTTP JSON for Cursor /
    // VS Code, or raw URL+header for anything else).
    function renderMcpConfigForPlatform() {
        if (!elements.mcpConfigExample) return;
        const platform = elements.mcpPlatformSelect?.value || 'claude-code';
        const key = currentNewKey || '<YOUR_API_KEY>';
        const mcpUrl = `${API_BASE.replace('/api', '')}/mcp/v1/`;
        const serverName = buildMcpServerName(currentNewDatabaseName);

        let instructions = '';
        let snippet = '';

        switch (platform) {
            case 'claude-code':
                instructions = 'Run this in your terminal (requires the Claude Code CLI):';
                snippet = `claude mcp add --transport http ${serverName} ${mcpUrl} --header "X-API-Key: ${key}"`;
                break;
            case 'codex':
                // Codex CLI's config is TOML at ~/.codex/config.toml with a
                // [mcp_servers.<name>] table. Codex is stdio-only, so the
                // entry runs mcp-remote (npx must be on PATH) to proxy the
                // HTTP transport over stdio, same approach as Claude Desktop.
                instructions = 'Add this to ~/.codex/config.toml, then restart Codex. Codex is stdio-only, so the entry runs mcp-remote to proxy the HTTP transport (npx must be on PATH):';
                snippet = `[mcp_servers.${serverName}]\ncommand = "npx"\nargs = ["-y", "mcp-remote", "${mcpUrl}", "--header", "X-API-Key:${key}"]`;
                break;
            case 'claude-desktop':
                instructions = 'Open Claude Desktop → Settings → Developer → Edit Config, then merge this into claude_desktop_config.json. Claude Desktop uses stdio, so the config proxies through mcp-remote (npx must be on PATH):';
                snippet = JSON.stringify({
                    mcpServers: {
                        [serverName]: {
                            command: "npx",
                            args: [
                                "-y",
                                "mcp-remote",
                                mcpUrl,
                                "--header",
                                `X-API-Key:${key}`
                            ]
                        }
                    }
                }, null, 2);
                break;
            case 'cursor':
                instructions = 'Add this to ~/.cursor/mcp.json (global) or <project>/.cursor/mcp.json (per-project), then reload Cursor:';
                snippet = JSON.stringify({
                    mcpServers: {
                        [serverName]: {
                            url: mcpUrl,
                            headers: {
                                "X-API-Key": key
                            }
                        }
                    }
                }, null, 2);
                break;
            case 'vscode':
                instructions = 'Add this to .vscode/mcp.json (per-workspace) or your user-level mcp.json, then enable the server in the Copilot MCP view:';
                snippet = JSON.stringify({
                    servers: {
                        [serverName]: {
                            type: "http",
                            url: mcpUrl,
                            headers: {
                                "X-API-Key": key
                            }
                        }
                    }
                }, null, 2);
                break;
            case 'generic':
            default:
                instructions = 'Point any HTTP-capable MCP client at this URL and send the key in the X-API-Key header:';
                snippet = `URL:    ${mcpUrl}\nHeader: X-API-Key: ${key}`;
                break;
        }

        elements.mcpConfigExample.textContent = snippet;
        if (elements.mcpPlatformInstructions) {
            elements.mcpPlatformInstructions.textContent = instructions;
        }
    }

    // Copy the rendered MCP config snippet (command or JSON) to the clipboard.
    async function copyMcpConfig() {
        const snippet = elements.mcpConfigExample?.textContent;
        if (!snippet || !elements.copyMcpConfigBtn) return;
        try {
            await navigator.clipboard.writeText(snippet);
            const btn = elements.copyMcpConfigBtn;
            const original = btn.textContent;
            btn.textContent = '✓ Copied!';
            setTimeout(() => { btn.textContent = original; }, 2000);
        } catch (err) {
            console.error('Failed to copy MCP config:', err);
        }
    }

    // Copy new key to clipboard
    async function copyNewKey() {
        const key = elements.newApiKey?.textContent;
        if (!key) return;

        try {
            await navigator.clipboard.writeText(key);
            elements.copyNewKeyBtn.textContent = '✓ Copied!';
            setTimeout(() => {
                elements.copyNewKeyBtn.textContent = '📋 Copy';
            }, 2000);
        } catch (err) {
            console.error('Failed to copy:', err);
        }
    }

    // Close show key modal — the dialog's 'close' event handler (wired in
    // init) does the actual state cleanup, so every close path (button,
    // Escape, backdrop click) scrubs the plaintext key uniformly.
    function closeShowKeyModal() {
        if (elements.showKeyModal) {
            elements.showKeyModal.close();
        }
    }

    // Open revoke confirmation modal
    function openRevokeModal(keyId, keyName) {
        keyToRevoke = keyId;
        if (elements.revokeKeyName) {
            elements.revokeKeyName.textContent = keyName;
        }
        if (elements.revokeKeyModal) {
            elements.revokeKeyModal.showModal();
        }
    }

    // Close revoke modal
    function closeRevokeModal() {
        keyToRevoke = null;
        if (elements.revokeKeyModal) {
            elements.revokeKeyModal.close();
        }
    }

    // Handle revoke key confirmation
    async function handleRevokeKey() {
        if (!keyToRevoke) return;

        const confirmBtn = elements.confirmRevokeKey;
        const originalText = confirmBtn.textContent;
        
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Revoking...';

        try {
            const response = await window.auth.fetch(`${API_BASE}/api-keys/${keyToRevoke}`, {
                method: 'DELETE',
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail?.error?.message || 'Failed to revoke API key');
            }

            // Close modal and refresh list
            closeRevokeModal();
            loadApiKeys();

        } catch (err) {
            console.error('Failed to revoke API key:', err);
            alert(err.message || 'Failed to revoke API key');
        } finally {
            confirmBtn.disabled = false;
            confirmBtn.textContent = originalText;
        }
    }

    // Escape HTML to prevent XSS
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Expose for external use - dashboard.js will call init after auth is ready
    window.apiKeys = {
        init: init,
        load: loadApiKeys,
        refresh: loadApiKeys,
    };
})();
