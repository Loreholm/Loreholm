// Dashboard functionality
(function() {
    'use strict';

    const API_BASE = window.APP_CONFIG.api.baseUrl;
    const DEVICES_POLL_INTERVAL = 5000;
    const COMMAND_PLATFORM_STORAGE_KEY = 'loreholm-dashboard-command-platform';
    const SETUP_MODE_STORAGE_KEY = 'loreholm-dashboard-setup-mode';

    let currentState = {
        user: null,
        devices: [],
        deviceCap: 3,
        deviceTier: 'free',
        pollInterval: null,
        isLoadingDevices: false,
        lastOnlineCount: 0,
    };

    const elements = {
        loading: document.getElementById('loading'),
        initialized: document.getElementById('initialized'),
        error: document.getElementById('error'),
        errorMessage: document.getElementById('errorMessage'),
        userEmail: document.getElementById('userEmail'),
        logoutBtn: document.getElementById('logoutBtn'),
        retryBtn: document.getElementById('retryBtn'),

        // Devices card
        devicesCap: document.getElementById('devicesCap'),
        devicesEmpty: document.getElementById('devicesEmpty'),
        devicesTableWrapper: document.getElementById('devicesTableWrapper'),
        devicesTableBody: document.getElementById('devicesTableBody'),
        addDeviceBtn: document.getElementById('addDeviceBtn'),

        // Maintenance card
        commandPlatformSelect: document.getElementById('commandPlatformSelect'),
        setupModeSelect: document.getElementById('setupModeSelect'),
        updateCommand: document.getElementById('updateCommand'),
        previewBtn: document.getElementById('previewBtn'),
        copyBtn: document.getElementById('copyBtn'),
        uninstallCommand: document.getElementById('uninstallCommand'),
        previewUninstallBtn: document.getElementById('previewUninstallBtn'),
        copyUninstallBtn: document.getElementById('copyUninstallBtn'),

        // Add Device modal
        addDeviceModal: document.getElementById('addDeviceModal'),
        addDeviceForm: document.getElementById('addDeviceForm'),
        addDeviceName: document.getElementById('addDeviceName'),
        addDevicePlatformSelect: document.getElementById('addDevicePlatformSelect'),
        addDeviceCommandWrapper: document.getElementById('addDeviceCommandWrapper'),
        addDeviceCommand: document.getElementById('addDeviceCommand'),
        addDeviceCommandHint: document.getElementById('addDeviceCommandHint'),
        copyAddDeviceCmdBtn: document.getElementById('copyAddDeviceCmdBtn'),
        cancelAddDevice: document.getElementById('cancelAddDevice'),
        generateAddDeviceCmd: document.getElementById('generateAddDeviceCmd'),

        // Rename modal
        renameDeviceModal: document.getElementById('renameDeviceModal'),
        renameDeviceForm: document.getElementById('renameDeviceForm'),
        renameDeviceId: document.getElementById('renameDeviceId'),
        renameDeviceName: document.getElementById('renameDeviceName'),
        cancelRenameDevice: document.getElementById('cancelRenameDevice'),

        // Remove modal
        removeDeviceModal: document.getElementById('removeDeviceModal'),
        removeDeviceName: document.getElementById('removeDeviceName'),
        removeDeviceWarning: document.getElementById('removeDeviceWarning'),
        confirmRemoveDevice: document.getElementById('confirmRemoveDevice'),
        cancelRemoveDevice: document.getElementById('cancelRemoveDevice'),

        // Access section
        apiDocsLink: document.getElementById('apiDocsLink'),
        localDashboardLink: document.getElementById('localDashboardLink'),
    };

    let pendingRemoveNode = null;

    function getPublicHost() {
        return API_BASE.replace(/\/api\/?$/, '');
    }

    function resolvePlatform(selectValue) {
        return selectValue === 'windows' ? 'windows' : 'unix';
    }

    function getSelectedCommandPlatform() {
        return resolvePlatform(elements.commandPlatformSelect?.value);
    }

    function getSelectedSetupMode() {
        return elements.setupModeSelect?.value === 'legacy' ? 'legacy' : 'recommended';
    }

    function buildUpdateCommand() {
        const publicHost = getPublicHost();
        const platform = getSelectedCommandPlatform();
        const setupMode = getSelectedSetupMode();

        if (platform === 'windows') {
            const updateScript = setupMode === 'legacy' ? 'update-legacy.ps1' : 'update.ps1';
            return `Invoke-WebRequest -Uri ${publicHost}/${updateScript} -OutFile update.ps1; .\\update.ps1`;
        }
        const updateScript = setupMode === 'legacy' ? 'update-legacy.sh' : 'update.sh';
        return `curl -fsSL ${publicHost}/${updateScript} | bash`;
    }

    function buildUninstallCommand() {
        const publicHost = getPublicHost();
        const platform = getSelectedCommandPlatform();
        if (platform === 'windows') {
            return `Invoke-WebRequest -Uri ${publicHost}/uninstall.ps1 -OutFile uninstall.ps1; .\\uninstall.ps1`;
        }
        return `curl -fsSL ${publicHost}/uninstall.sh | bash`;
    }

    function refreshMaintenanceCommands() {
        if (elements.updateCommand) {
            elements.updateCommand.textContent = buildUpdateCommand();
        }
        if (elements.uninstallCommand) {
            elements.uninstallCommand.textContent = buildUninstallCommand();
        }
    }

    function initializePlatformSelection() {
        if (!elements.commandPlatformSelect) return;

        const saved = localStorage.getItem(COMMAND_PLATFORM_STORAGE_KEY);
        const valid = new Set(['unix', 'windows']);
        elements.commandPlatformSelect.value = valid.has(saved) ? saved : 'unix';

        elements.commandPlatformSelect.addEventListener('change', () => {
            localStorage.setItem(COMMAND_PLATFORM_STORAGE_KEY, elements.commandPlatformSelect.value);
            refreshMaintenanceCommands();
        });
    }

    function initializeSetupModeSelection() {
        if (!elements.setupModeSelect) return;
        const saved = localStorage.getItem(SETUP_MODE_STORAGE_KEY);
        const valid = new Set(['recommended', 'legacy']);
        elements.setupModeSelect.value = valid.has(saved) ? saved : 'recommended';

        elements.setupModeSelect.addEventListener('change', () => {
            localStorage.setItem(SETUP_MODE_STORAGE_KEY, elements.setupModeSelect.value);
            refreshMaintenanceCommands();
        });
    }

    function showView(view) {
        elements.loading.style.display = 'none';
        elements.initialized.style.display = 'none';
        elements.error.style.display = 'none';
        if (elements[view]) elements[view].style.display = 'block';
    }

    function showError(message) {
        elements.errorMessage.textContent = message;
        showView('error');
    }

    async function extractApiErrorMessage(response, fallbackMessage) {
        let payload = {};
        try {
            payload = await response.clone().json();
        } catch (err) {
            payload = {};
        }
        const detail = payload?.detail;
        if (typeof detail === 'string' && detail.trim()) return detail;
        if (detail?.error?.message) return detail.error.message;
        if (payload?.error?.message) return payload.error.message;
        return fallbackMessage || `Request failed (${response.status})`;
    }

    async function loadUser() {
        try {
            currentState.user = await window.auth.getUser();
            elements.userEmail.textContent = currentState.user.email || 'User';
        } catch (err) {
            console.error('Failed to load user:', err);
        }
    }

    function formatLastSeen(value) {
        if (!value) return 'Never';
        const dt = new Date(value);
        if (Number.isNaN(dt.getTime())) return 'Never';
        const now = Date.now();
        const diffMs = now - dt.getTime();
        if (diffMs < 60_000) return 'Just now';
        if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}m ago`;
        if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)}h ago`;
        if (diffMs < 30 * 86_400_000) return `${Math.floor(diffMs / 86_400_000)}d ago`;
        return dt.toLocaleDateString();
    }

    function escapeHtml(value) {
        const text = String(value ?? '');
        return text.replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[ch]));
    }

    function renderDevicesTable() {
        const { devices, deviceCap } = currentState;

        if (elements.devicesCap) {
            elements.devicesCap.textContent = `${devices.length} / ${deviceCap}`;
        }

        if (!devices.length) {
            elements.devicesTableWrapper.style.display = 'none';
            elements.devicesEmpty.style.display = 'block';
        } else {
            elements.devicesEmpty.style.display = 'none';
            elements.devicesTableWrapper.style.display = 'block';

            const rows = devices.map((node) => {
                const name = escapeHtml(node.name || '(unnamed)');
                const badgeClass = node.online ? 'badge badge-success' : 'badge badge-error';
                const badgeText = node.online ? 'Connected' : 'Disconnected';
                const lastSeen = node.online ? 'Now' : formatLastSeen(node.last_seen);
                return `
                    <tr data-node-id="${escapeHtml(node.id)}">
                        <td class="device-name-cell">${name}</td>
                        <td><span class="${badgeClass}">${badgeText}</span></td>
                        <td>${escapeHtml(lastSeen)}</td>
                        <td class="actions-col">
                            <button type="button" class="btn btn-small btn-secondary device-rename-btn" data-node-id="${escapeHtml(node.id)}">Rename</button>
                            <button type="button" class="btn btn-small btn-danger device-remove-btn" data-node-id="${escapeHtml(node.id)}">Remove</button>
                        </td>
                    </tr>
                `;
            }).join('');
            elements.devicesTableBody.innerHTML = rows;
        }

        const atCap = devices.length >= deviceCap;
        elements.addDeviceBtn.disabled = atCap;
        elements.addDeviceBtn.title = atCap
            ? `${deviceCap}/${deviceCap} — ${currentState.deviceTier} tier limit`
            : '';
    }

    async function loadDevices() {
        if (currentState.isLoadingDevices) return;
        currentState.isLoadingDevices = true;
        try {
            const response = await window.auth.fetch(`${API_BASE}/onboarding/nodes`);
            if (!response.ok) {
                const message = await extractApiErrorMessage(response, 'Failed to load devices.');
                console.error('loadDevices error:', message);
                return;
            }
            const data = await response.json();
            currentState.devices = Array.isArray(data.nodes) ? data.nodes : [];
            currentState.deviceCap = data.cap ?? 3;
            currentState.deviceTier = data.tier || 'free';

            const onlineCount = currentState.devices.filter((n) => n.online).length;
            if (onlineCount !== currentState.lastOnlineCount) {
                currentState.lastOnlineCount = onlineCount;
                if (window.apiKeys && window.apiKeys.refresh) {
                    window.apiKeys.refresh();
                }
            }

            renderDevicesTable();
        } catch (err) {
            console.error('loadDevices threw:', err);
        } finally {
            currentState.isLoadingDevices = false;
        }
    }

    function startDevicesPolling() {
        stopDevicesPolling();
        currentState.pollInterval = setInterval(loadDevices, DEVICES_POLL_INTERVAL);
    }

    function stopDevicesPolling() {
        if (currentState.pollInterval) {
            clearInterval(currentState.pollInterval);
            currentState.pollInterval = null;
        }
    }

    function copyCommand(codeElement, buttonElement) {
        if (!codeElement || !buttonElement) return;
        const command = codeElement.textContent;
        navigator.clipboard.writeText(command).then(() => {
            const originalText = buttonElement.textContent;
            buttonElement.textContent = '✓ Copied!';
            setTimeout(() => { buttonElement.textContent = originalText; }, 2000);
        }).catch((err) => {
            console.error('Failed to copy:', err);
            alert('Failed to copy to clipboard. Please copy manually.');
        });
    }

    function previewMaintenanceUpdate() {
        const publicHost = getPublicHost();
        const platform = getSelectedCommandPlatform();
        const setupMode = getSelectedSetupMode();
        const script = setupMode === 'legacy'
            ? (platform === 'windows' ? 'update-legacy.ps1' : 'update-legacy.sh')
            : (platform === 'windows' ? 'update.ps1' : 'update.sh');
        window.open(`${publicHost}/${script}`, '_blank');
    }

    function previewUninstallScript() {
        const platform = getSelectedCommandPlatform();
        const scriptName = platform === 'windows' ? 'uninstall.ps1' : 'uninstall.sh';
        window.open(`${getPublicHost()}/${scriptName}`, '_blank');
    }

    async function openLocalDashboard(event) {
        if (event) event.preventDefault();
        if (!elements.localDashboardLink) return;

        const link = elements.localDashboardLink;
        const originalLabel = link.textContent;
        link.textContent = 'Resolving...';
        link.classList.add('disabled');
        link.setAttribute('aria-disabled', 'true');

        try {
            const response = await window.auth.fetch(`${API_BASE}/onboarding/local-dashboard/resolve`);
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                const detail = typeof payload?.detail === 'string' ? payload.detail : `Resolver request failed (${response.status})`;
                throw new Error(detail);
            }
            const targetUrl = payload?.local_admin_url || payload?.url;
            if (!targetUrl) throw new Error('Resolver did not return a URL.');
            window.location.assign(targetUrl);
        } catch (err) {
            console.error('Local dashboard resolve failed:', err);
            alert(`Could not open local dashboard: ${err.message || 'Unknown error'}`);
        } finally {
            link.textContent = originalLabel;
            link.classList.remove('disabled');
            link.removeAttribute('aria-disabled');
        }
    }

    function buildAddDeviceCommand(rawCommand, platformSelect) {
        const platform = resolvePlatform(platformSelect?.value);
        if (platform !== 'windows') return rawCommand;

        const publicHost = getPublicHost();
        const keyMatch = rawCommand.match(/--key\s+(\S+)/);
        const tokenMatch = rawCommand.match(/--sync-token\s+(\S+)/);
        const nameMatch = rawCommand.match(/--name\s+(\S+)/);
        if (!keyMatch) return rawCommand;

        const args = [`-Key "${keyMatch[1]}"`];
        if (tokenMatch) args.push(`-SyncToken "${tokenMatch[1]}"`);
        if (nameMatch) args.push(`-Name "${nameMatch[1]}"`);
        return `Invoke-WebRequest -Uri ${publicHost}/install.ps1 -OutFile install.ps1; .\\install.ps1 ${args.join(' ')}`;
    }

    function resetAddDeviceModal() {
        elements.addDeviceForm.reset();
        elements.addDeviceCommandWrapper.style.display = 'none';
        elements.addDeviceCommand.textContent = '';
        elements.generateAddDeviceCmd.disabled = false;
        elements.generateAddDeviceCmd.textContent = 'Generate Command';
    }

    function openAddDeviceModal() {
        resetAddDeviceModal();
        elements.addDevicePlatformSelect.value = elements.commandPlatformSelect.value;
        elements.addDeviceModal.showModal();
    }

    async function submitAddDeviceForm(event) {
        event.preventDefault();
        const name = elements.addDeviceName.value.trim().toLowerCase();
        if (!name) return;

        elements.generateAddDeviceCmd.disabled = true;
        elements.generateAddDeviceCmd.textContent = 'Generating...';

        try {
            const response = await window.auth.fetch(`${API_BASE}/onboarding/nodes/preauth`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ node_name: name }),
            });
            if (!response.ok) {
                const message = await extractApiErrorMessage(response, 'Failed to generate command.');
                alert(message);
                return;
            }
            const data = await response.json();
            const finalCommand = buildAddDeviceCommand(data.install_command, elements.addDevicePlatformSelect);
            elements.addDeviceCommand.textContent = finalCommand;
            elements.addDeviceCommandWrapper.style.display = 'block';
            elements.generateAddDeviceCmd.textContent = 'Regenerate';
        } catch (err) {
            console.error('Add device failed:', err);
            alert(`Failed to generate command: ${err.message || 'Unknown error'}`);
        } finally {
            elements.generateAddDeviceCmd.disabled = false;
        }
    }

    function openRenameModal(node) {
        elements.renameDeviceId.value = node.id;
        elements.renameDeviceName.value = node.name || '';
        elements.renameDeviceModal.showModal();
    }

    async function submitRenameForm(event) {
        event.preventDefault();
        const nodeId = elements.renameDeviceId.value;
        const newName = elements.renameDeviceName.value.trim().toLowerCase();
        if (!nodeId || !newName) return;

        try {
            const response = await window.auth.fetch(`${API_BASE}/onboarding/nodes/${encodeURIComponent(nodeId)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName }),
            });
            if (!response.ok) {
                const message = await extractApiErrorMessage(response, 'Rename failed.');
                alert(message);
                return;
            }
            elements.renameDeviceModal.close();
            await loadDevices();
        } catch (err) {
            console.error('Rename failed:', err);
            alert(`Rename failed: ${err.message || 'Unknown error'}`);
        }
    }

    function openRemoveModal(node) {
        pendingRemoveNode = node;
        elements.removeDeviceName.textContent = node.name || '(unnamed)';
        if (node.online) {
            elements.removeDeviceWarning.textContent = 'This device is currently connected. Removing it will disconnect it from your network and you will need to re-install to add it back.';
        } else {
            elements.removeDeviceWarning.textContent = 'This action cannot be undone. The device will need to re-install if you want to add it back.';
        }
        elements.removeDeviceModal.showModal();
    }

    async function confirmRemove() {
        if (!pendingRemoveNode) return;
        const nodeId = pendingRemoveNode.id;
        elements.confirmRemoveDevice.disabled = true;
        try {
            const response = await window.auth.fetch(`${API_BASE}/onboarding/nodes/${encodeURIComponent(nodeId)}`, {
                method: 'DELETE',
            });
            if (!response.ok && response.status !== 204) {
                const message = await extractApiErrorMessage(response, 'Remove failed.');
                alert(message);
                return;
            }
            elements.removeDeviceModal.close();
            pendingRemoveNode = null;
            await loadDevices();
        } catch (err) {
            console.error('Remove failed:', err);
            alert(`Remove failed: ${err.message || 'Unknown error'}`);
        } finally {
            elements.confirmRemoveDevice.disabled = false;
        }
    }

    function handleDevicesTableClick(event) {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const nodeId = target.getAttribute('data-node-id');
        if (!nodeId) return;
        const node = currentState.devices.find((n) => n.id === nodeId);
        if (!node) return;

        if (target.classList.contains('device-rename-btn')) {
            openRenameModal(node);
        } else if (target.classList.contains('device-remove-btn')) {
            openRemoveModal(node);
        }
    }

    elements.logoutBtn.addEventListener('click', () => window.auth.logout());
    elements.retryBtn.addEventListener('click', () => loadDevices());
    elements.copyBtn.addEventListener('click', () => copyCommand(elements.updateCommand, elements.copyBtn));
    elements.previewBtn.addEventListener('click', previewMaintenanceUpdate);
    if (elements.copyUninstallBtn) {
        elements.copyUninstallBtn.addEventListener('click', () => copyCommand(elements.uninstallCommand, elements.copyUninstallBtn));
    }
    if (elements.previewUninstallBtn) {
        elements.previewUninstallBtn.addEventListener('click', previewUninstallScript);
    }
    if (elements.localDashboardLink) {
        elements.localDashboardLink.addEventListener('click', openLocalDashboard);
    }

    elements.addDeviceBtn.addEventListener('click', openAddDeviceModal);
    elements.cancelAddDevice.addEventListener('click', () => elements.addDeviceModal.close());
    elements.addDeviceForm.addEventListener('submit', submitAddDeviceForm);
    elements.addDevicePlatformSelect.addEventListener('change', () => {
        // If a command is already shown, regenerate the rendered form for the new platform
        // without minting a new key (we just re-shape the existing one).
        const existing = elements.addDeviceCommand.textContent;
        if (existing && existing.includes('--key')) {
            const platform = resolvePlatform(elements.addDevicePlatformSelect.value);
            if (platform === 'windows' && !existing.startsWith('Invoke-WebRequest')) {
                elements.addDeviceCommand.textContent = buildAddDeviceCommand(existing, elements.addDevicePlatformSelect);
            } else if (platform !== 'windows' && existing.startsWith('Invoke-WebRequest')) {
                // The unix form is what the API returns natively; we need to refetch.
                // Simpler: hide the command and ask the user to regenerate.
                elements.addDeviceCommandWrapper.style.display = 'none';
                elements.generateAddDeviceCmd.textContent = 'Generate Command';
            }
        }
    });
    elements.copyAddDeviceCmdBtn.addEventListener('click', () => copyCommand(elements.addDeviceCommand, elements.copyAddDeviceCmdBtn));

    elements.renameDeviceForm.addEventListener('submit', submitRenameForm);
    elements.cancelRenameDevice.addEventListener('click', () => elements.renameDeviceModal.close());

    elements.confirmRemoveDevice.addEventListener('click', confirmRemove);
    elements.cancelRemoveDevice.addEventListener('click', () => {
        pendingRemoveNode = null;
        elements.removeDeviceModal.close();
    });

    if (elements.devicesTableBody) {
        elements.devicesTableBody.addEventListener('click', handleDevicesTableClick);
    }

    async function init() {
        try {
            initializePlatformSelection();
            initializeSetupModeSelection();
            refreshMaintenanceCommands();

            await window.auth.init();
            await loadUser();

            const apiDocsUrl = window.location.origin.replace('://', '://api.') + '/docs';
            elements.apiDocsLink.href = apiDocsUrl;

            showView('initialized');
            await loadDevices();
            startDevicesPolling();

            if (window.apiKeys && window.apiKeys.init) {
                window.apiKeys.init();
            }
        } catch (err) {
            console.error('Dashboard initialization failed:', err);
            showError('Failed to initialize dashboard. Please refresh the page.');
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
