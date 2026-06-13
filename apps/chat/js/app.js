// loreholm chat SPA
(function () {
  'use strict';

  const API = window.APP_CONFIG?.api?.baseUrl || '';

  const state = {
    databases: [],
    selectedDb: null,
    conversations: [],
    activeConversation: null,
    messages: [],
    streaming: false,
    models: [],
    favoriteModel: '',
  };

  // DOM refs
  const $ = (id) => document.getElementById(id);
  const el = {
    loadingGate: $('loadingGate'),
    loginGate: $('loginGate'),
    mainApp: $('mainApp'),
    loginBtn: $('loginBtn'),
    logoutBtn: $('logoutBtn'),
    userName: $('userName'),
    dbSelect: $('dbSelect'),
    newChatBtn: $('newChatBtn'),
    conversationList: $('conversationList'),
    usageSummary: $('usageSummary'),
    chatTitle: $('chatTitle'),
    modelSelect: $('modelSelect'),
    messages: $('messages'),
    emptyState: $('emptyState'),
    messageInput: $('messageInput'),
    sendBtn: $('sendBtn'),
    composer: $('composer'),
    sidebarToggle: $('sidebarToggle'),
    sidebar: $('sidebar'),
    sidebarBackdrop: $('sidebarBackdrop'),
    systemPromptBtn: $('systemPromptBtn'),
    systemPromptModal: $('systemPromptModal'),
    systemPromptInput: $('systemPromptInput'),
    systemPromptStatus: $('systemPromptStatus'),
    systemPromptSave: $('systemPromptSave'),
    systemPromptCancel: $('systemPromptCancel'),
    systemPromptClose: $('systemPromptClose'),
    favoriteModelBtn: $('favoriteModelBtn'),
    promptHelperInput: $('promptHelperInput'),
    promptHelperDraft: $('promptHelperDraft'),
    promptHelperRefine: $('promptHelperRefine'),
    promptHelperStatus: $('promptHelperStatus'),
  };

  // ── Auth ──────────────────────────────────────────────────────────

  async function boot() {
    // Show loading spinner while Auth0 resolves (SSO iframe exchange)
    el.loadingGate.hidden = false;
    el.loginGate.hidden = true;
    el.mainApp.hidden = true;

    const authenticated = await window.auth.init();

    el.loadingGate.hidden = true;
    if (authenticated) {
      el.mainApp.hidden = false;
      const user = await window.auth.getUser();
      el.userName.textContent = user?.name || user?.email || '';
      await loadPreferences();
      await loadModels();
      await loadDatabases();
    } else {
      el.loginGate.hidden = false;
    }
  }

  // ── Preferences ──────────────────────────────────────────────────

  async function loadPreferences() {
    try {
      const data = await apiJson('/preferences');
      state.favoriteModel = String(data?.favorite_model || '');
    } catch (err) {
      console.error('Failed to load preferences:', err);
      state.favoriteModel = '';
    }
  }

  async function saveFavoriteModel(modelId) {
    try {
      const data = await apiJson('/preferences', {
        method: 'PUT',
        body: JSON.stringify({ favorite_model: modelId }),
      });
      state.favoriteModel = String(data?.favorite_model || '');
      renderFavoriteButton();
    } catch (err) {
      console.error('Failed to save favorite model:', err);
      alert(`Failed to save favorite model: ${err.message}`);
    }
  }

  function renderFavoriteButton() {
    const current = el.modelSelect.value;
    const hasModel = !!current;
    el.favoriteModelBtn.disabled = !hasModel;
    const isFav = hasModel && state.favoriteModel === current;
    el.favoriteModelBtn.classList.toggle('is-favorite', isFav);
    el.favoriteModelBtn.textContent = isFav ? '\u2605' : '\u2606';
    el.favoriteModelBtn.title = !hasModel
      ? 'No model available'
      : isFav
      ? 'Unfavorite this model'
      : 'Favorite this model (default across wizard and chat)';
  }

  // ── Models ───────────────────────────────────────────────────────

  async function loadModels() {
    let models = [];
    try {
      const data = await apiJson('/models');
      models = Array.isArray(data.models) ? data.models : [];
    } catch (err) {
      console.error('Failed to load models:', err);
    }
    state.models = models;
    el.modelSelect.innerHTML = '';
    if (models.length === 0) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No models available';
      el.modelSelect.appendChild(opt);
      renderFavoriteButton();
      return;
    }
    for (const m of models) {
      const opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      el.modelSelect.appendChild(opt);
    }
    // Default to favorite if present in the list, else the first model.
    const defaultChoice =
      state.favoriteModel && models.includes(state.favoriteModel)
        ? state.favoriteModel
        : models[0];
    el.modelSelect.value = defaultChoice;
    renderFavoriteButton();
  }

  function setActiveModel(modelId) {
    // Sync the dropdown to the model that actually served the response.
    if (!modelId) return;
    if (!state.models.includes(modelId)) {
      // Model returned by the backend but not in our list — add it so the
      // dropdown always shows a concrete value.
      state.models.push(modelId);
      const opt = document.createElement('option');
      opt.value = modelId;
      opt.textContent = modelId;
      el.modelSelect.appendChild(opt);
    }
    if (el.modelSelect.value !== modelId) {
      el.modelSelect.value = modelId;
    }
    renderFavoriteButton();
  }

  // ── API helpers ──────────────────────────────────────────────────

  async function api(path, opts = {}) {
    return window.auth.fetch(`${API}/chat${path}`, opts);
  }

  async function apiJson(path, opts = {}) {
    const res = await api(path, opts);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const msg = body?.error?.message || body?.detail?.error?.message || `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return res.json();
  }

  // ── Databases ────────────────────────────────────────────────────

  async function loadDatabases() {
    try {
      const res = await window.auth.fetch(`${API}/database-targets/discover`);
      if (res.ok) {
        const body = await res.json();
        state.databases = body.databases || [];
      } else {
        state.databases = [];
      }
    } catch (err) {
      console.error('Failed to load databases:', err);
      state.databases = [];
    }
    renderDatabaseSelect();
    if (state.databases.length > 0) {
      state.selectedDb = state.databases[0].database_id;
      el.dbSelect.value = state.selectedDb;
      el.systemPromptBtn.disabled = false;
      await loadConversations();
    } else {
      el.systemPromptBtn.disabled = true;
    }
  }

  function renderDatabaseSelect() {
    el.dbSelect.innerHTML = '';
    if (state.databases.length === 0) {
      el.dbSelect.innerHTML = '<option value="">No databases found</option>';
      return;
    }
    for (const db of state.databases) {
      const opt = document.createElement('option');
      opt.value = db.database_id;
      opt.textContent = db.name || db.database_id;
      if (db.status && db.status !== 'online') {
        opt.textContent += ` (${db.status})`;
      }
      el.dbSelect.appendChild(opt);
    }
  }

  // ── Conversations ────────────────────────────────────────────────

  async function loadConversations() {
    if (!state.selectedDb) return;
    try {
      const data = await apiJson(`/conversations?source=chat&database_id=${encodeURIComponent(state.selectedDb)}&limit=50`);
      state.conversations = data.conversations || [];
    } catch (err) {
      console.error('Failed to load conversations:', err);
      state.conversations = [];
    }
    renderConversationList();
    // Always land on a ready-to-type state: open the most recent
    // conversation for this database, or create a fresh one if none exist.
    if (state.conversations.length > 0) {
      await openConversation(state.conversations[0]);
    } else {
      await createNewChat();
    }
  }

  function renderConversationList() {
    el.conversationList.innerHTML = '';
    if (state.conversations.length === 0) {
      el.conversationList.innerHTML = '<div class="conv-empty">No conversations yet</div>';
      return;
    }
    for (const conv of state.conversations) {
      const div = document.createElement('div');
      div.className = 'conv-item' + (conv.id === state.activeConversation?.id ? ' active' : '');
      div.dataset.id = conv.id;

      const title = document.createElement('span');
      title.className = 'conv-title';
      title.textContent = conv.title || 'Untitled';
      div.appendChild(title);

      const del = document.createElement('button');
      del.className = 'conv-delete';
      del.textContent = '\u00d7';
      del.title = 'Delete';
      del.onclick = (e) => { e.stopPropagation(); deleteConv(conv.id); };
      div.appendChild(del);

      div.onclick = () => openConversation(conv);
      el.conversationList.appendChild(div);
    }
  }

  async function openConversation(conv) {
    state.activeConversation = conv;
    el.chatTitle.textContent = conv.title || conv.database_id;
    el.messageInput.disabled = false;
    el.sendBtn.disabled = false;
    el.emptyState.hidden = true;
    renderConversationList();

    try {
      const data = await apiJson(`/conversations/${conv.id}`);
      state.messages = (data.messages || []).filter((m) => m.role !== 'system');
      renderMessages();
    } catch (err) {
      console.error('Failed to load conversation:', err);
    }
    await loadUsage();
  }

  async function createNewChat() {
    if (!state.selectedDb) return;
    try {
      const conv = await apiJson('/conversations', {
        method: 'POST',
        body: JSON.stringify({ database_id: state.selectedDb, title: 'New chat' }),
      });
      state.conversations.unshift(conv);
      renderConversationList();
      await openConversation(conv);
    } catch (err) {
      console.error('Failed to create conversation:', err);
    }
  }

  async function deleteConv(id) {
    try {
      await api(`/conversations/${id}`, { method: 'DELETE' });
      state.conversations = state.conversations.filter((c) => c.id !== id);
      if (state.activeConversation?.id === id) {
        state.activeConversation = null;
        state.messages = [];
        el.chatTitle.textContent = 'Select a conversation';
        el.messageInput.disabled = true;
        el.sendBtn.disabled = true;
        el.emptyState.hidden = false;
        renderMessages();
        loadUsage();
      }
      renderConversationList();
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
  }

  // ── Messages ─────────────────────────────────────────────────────

  function renderMessages() {
    el.messages.innerHTML = '';
    if (state.messages.length === 0 && !state.activeConversation) {
      el.emptyState.hidden = false;
      el.messages.appendChild(el.emptyState);
      return;
    }
    el.emptyState.hidden = true;
    for (const msg of state.messages) {
      if (msg.role === 'tool') continue; // Don't render raw tool results
      appendMessageBubble(msg.role, msg.content, msg.tool_calls);
    }
    scrollToBottom();
  }

  function appendMessageBubble(role, content, toolCalls) {
    const div = document.createElement('div');
    div.className = `message message-${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = content || '';
    div.appendChild(bubble);

    if (toolCalls && toolCalls.length > 0) {
      const tc = document.createElement('div');
      tc.className = 'tool-calls';
      for (const call of toolCalls) {
        const fn = call.function || {};
        const chip = document.createElement('span');
        chip.className = 'tool-chip';
        chip.textContent = fn.name || 'tool';
        tc.appendChild(chip);
      }
      div.appendChild(tc);
    }

    el.messages.appendChild(div);
    return bubble;
  }

  function appendToolEvent(type, data) {
    const div = document.createElement('div');
    div.className = 'message message-tool-event';
    const chip = document.createElement('span');
    chip.className = 'tool-chip ' + (type === 'tool_end' ? (data.ok ? 'tool-ok' : 'tool-err') : '');
    if (type === 'tool_start') {
      chip.textContent = `\u25b6 ${data.tool}`;
    } else if (type === 'tool_end') {
      chip.textContent = `${data.ok ? '\u2713' : '\u2717'} ${data.tool}`;
    }
    div.appendChild(chip);
    el.messages.appendChild(div);
    scrollToBottom();
  }

  function scrollToBottom() {
    el.messages.scrollTop = el.messages.scrollHeight;
  }

  // ── Streaming ────────────────────────────────────────────────────

  async function sendMessage() {
    if (state.streaming || !state.activeConversation) return;
    const text = el.messageInput.value.trim();
    if (!text) return;

    el.messageInput.value = '';
    autoResizeInput();
    state.streaming = true;
    el.sendBtn.disabled = true;
    el.messageInput.disabled = true;

    // Show user message
    appendMessageBubble('user', text);
    scrollToBottom();

    // Create assistant bubble for streaming
    const assistantDiv = document.createElement('div');
    assistantDiv.className = 'message message-assistant';
    const assistantBubble = document.createElement('div');
    assistantBubble.className = 'bubble streaming';
    assistantDiv.appendChild(assistantBubble);
    el.messages.appendChild(assistantDiv);

    const model = el.modelSelect.value || undefined;

    try {
      const token = await window.auth.getAccessToken();
      const res = await fetch(`${API}/chat/stream`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          conversation_id: state.activeConversation.id,
          messages: [{ role: 'user', content: text }],
          model: model,
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const msg = body?.error?.message || `HTTP ${res.status}`;
        assistantBubble.textContent = `Error: ${msg}`;
        assistantBubble.classList.remove('streaming');
        assistantBubble.classList.add('error');
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let fullText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop(); // keep incomplete line

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            const eventType = line.slice(7).trim();
            // Next data line
            continue;
          }
          if (line.startsWith('data: ')) {
            const raw = line.slice(6);
            let parsed;
            try {
              parsed = JSON.parse(raw);
            } catch {
              continue;
            }

            // Determine event type from the data shape
            if (parsed.content !== undefined) {
              // text_delta
              fullText += parsed.content;
              assistantBubble.textContent = fullText;
              scrollToBottom();
            } else if (parsed.tool !== undefined && parsed.arguments !== undefined) {
              // tool_start
              appendToolEvent('tool_start', parsed);
            } else if (parsed.tool !== undefined && parsed.ok !== undefined) {
              // tool_end
              appendToolEvent('tool_end', parsed);
            } else if (parsed.model !== undefined && !parsed.content && !parsed.tool) {
              // done
              assistantBubble.classList.remove('streaming');
              setActiveModel(parsed.model);
            } else if (parsed.message !== undefined && !parsed.content) {
              // error
              assistantBubble.textContent = fullText || `Error: ${parsed.message}`;
              assistantBubble.classList.remove('streaming');
              if (!fullText) assistantBubble.classList.add('error');
            }
          }
        }
      }

      assistantBubble.classList.remove('streaming');
      if (!fullText) {
        assistantBubble.textContent = '(No response)';
      }

      // Persist locally
      state.messages.push({ role: 'user', content: text });
      state.messages.push({ role: 'assistant', content: fullText });

    } catch (err) {
      console.error('Stream error:', err);
      assistantBubble.textContent = `Error: ${err.message}`;
      assistantBubble.classList.remove('streaming');
      assistantBubble.classList.add('error');
    } finally {
      state.streaming = false;
      el.sendBtn.disabled = false;
      el.messageInput.disabled = false;
      el.messageInput.focus();
      loadUsage();
    }
  }

  // We need to parse SSE properly — the event type comes on the event: line
  // Let me fix the SSE parsing to track event types correctly.

  // ── System prompt ────────────────────────────────────────────────

  async function openSystemPromptModal() {
    if (!state.selectedDb) return;
    el.systemPromptStatus.textContent = 'Loading...';
    el.systemPromptStatus.className = 'modal-status';
    el.systemPromptInput.value = '';
    el.promptHelperInput.value = '';
    el.promptHelperStatus.textContent = '';
    el.promptHelperStatus.className = 'modal-status';
    el.systemPromptModal.hidden = false;
    try {
      const data = await apiJson(`/databases/${encodeURIComponent(state.selectedDb)}/system-prompt`);
      el.systemPromptInput.value = data.system_prompt || '';
      el.systemPromptStatus.textContent = '';
    } catch (err) {
      el.systemPromptStatus.textContent = `Error loading: ${err.message}`;
      el.systemPromptStatus.className = 'modal-status error';
    }
    el.systemPromptInput.focus();
  }

  async function runPromptHelper(mode) {
    if (!state.selectedDb) return;
    const instruction = el.promptHelperInput.value.trim();
    if (!instruction) {
      el.promptHelperStatus.textContent = 'Describe the agent you want.';
      el.promptHelperStatus.className = 'modal-status error';
      el.promptHelperInput.focus();
      return;
    }
    el.promptHelperStatus.textContent = mode === 'refine' ? 'Refining...' : 'Drafting...';
    el.promptHelperStatus.className = 'modal-status';
    el.promptHelperDraft.disabled = true;
    el.promptHelperRefine.disabled = true;
    try {
      const model = el.modelSelect.value || undefined;
      const data = await apiJson(
        `/databases/${encodeURIComponent(state.selectedDb)}/system-prompt/draft`,
        {
          method: 'POST',
          body: JSON.stringify({
            instruction,
            current: el.systemPromptInput.value,
            mode,
            model,
          }),
        },
      );
      if (data?.prompt) {
        el.systemPromptInput.value = data.prompt;
        el.promptHelperStatus.textContent = `Draft from ${data.model || 'model'}. Edit and Save when ready.`;
        el.promptHelperStatus.className = 'modal-status ok';
      } else {
        el.promptHelperStatus.textContent = 'No draft returned.';
        el.promptHelperStatus.className = 'modal-status error';
      }
    } catch (err) {
      el.promptHelperStatus.textContent = `Error: ${err.message}`;
      el.promptHelperStatus.className = 'modal-status error';
    } finally {
      el.promptHelperDraft.disabled = false;
      el.promptHelperRefine.disabled = false;
    }
  }

  function closeSystemPromptModal() {
    el.systemPromptModal.hidden = true;
  }

  async function saveSystemPrompt() {
    if (!state.selectedDb) return;
    el.systemPromptStatus.textContent = 'Saving...';
    el.systemPromptStatus.className = 'modal-status';
    el.systemPromptSave.disabled = true;
    try {
      await apiJson(`/databases/${encodeURIComponent(state.selectedDb)}/system-prompt`, {
        method: 'PUT',
        body: JSON.stringify({ system_prompt: el.systemPromptInput.value }),
      });
      el.systemPromptStatus.textContent = 'Saved. Applies to new messages in any conversation for this database.';
      el.systemPromptStatus.className = 'modal-status ok';
      setTimeout(closeSystemPromptModal, 900);
    } catch (err) {
      el.systemPromptStatus.textContent = `Error: ${err.message}`;
      el.systemPromptStatus.className = 'modal-status error';
    } finally {
      el.systemPromptSave.disabled = false;
    }
  }

  // ── Usage ────────────────────────────────────────────────────────

  async function loadUsage() {
    const convId = state.activeConversation?.id;
    if (!convId) {
      el.usageSummary.textContent = '';
      return;
    }
    try {
      const data = await apiJson(`/usage?conversation_id=${encodeURIComponent(convId)}`);
      el.usageSummary.textContent = `Tokens: ${(data.total_tokens || 0).toLocaleString()}`;
    } catch {
      el.usageSummary.textContent = '';
    }
  }

  // ── Input handling ───────────────────────────────────────────────

  function autoResizeInput() {
    el.messageInput.style.height = 'auto';
    el.messageInput.style.height = Math.min(el.messageInput.scrollHeight, 200) + 'px';
  }

  // ── Mobile sidebar ───────────────────────────────────────────────

  function toggleSidebar() {
    el.sidebar.classList.toggle('mobile-open');
    el.sidebarBackdrop.hidden = !el.sidebar.classList.contains('mobile-open');
  }

  // ── Event listeners ──────────────────────────────────────────────

  el.loginBtn.addEventListener('click', () => window.auth.login());
  el.logoutBtn.addEventListener('click', () => window.auth.logout());
  el.newChatBtn.addEventListener('click', createNewChat);
  el.sendBtn.addEventListener('click', sendMessage);
  el.sidebarToggle.addEventListener('click', toggleSidebar);
  el.sidebarBackdrop.addEventListener('click', toggleSidebar);
  el.modelSelect.addEventListener('change', renderFavoriteButton);
  el.favoriteModelBtn.addEventListener('click', () => {
    const current = el.modelSelect.value;
    if (!current) return;
    const next = state.favoriteModel === current ? '' : current;
    saveFavoriteModel(next);
  });
  el.systemPromptBtn.addEventListener('click', openSystemPromptModal);
  el.systemPromptSave.addEventListener('click', saveSystemPrompt);
  el.systemPromptCancel.addEventListener('click', closeSystemPromptModal);
  el.systemPromptClose.addEventListener('click', closeSystemPromptModal);
  el.promptHelperDraft.addEventListener('click', () => runPromptHelper('draft'));
  el.promptHelperRefine.addEventListener('click', () => runPromptHelper('refine'));
  el.systemPromptModal.addEventListener('click', (e) => {
    if (e.target === el.systemPromptModal) closeSystemPromptModal();
  });

  el.dbSelect.addEventListener('change', async () => {
    state.selectedDb = el.dbSelect.value;
    state.activeConversation = null;
    state.messages = [];
    el.chatTitle.textContent = 'Select a conversation';
    el.messageInput.disabled = true;
    el.sendBtn.disabled = true;
    el.emptyState.hidden = false;
    renderMessages();
    loadUsage();
    await loadConversations();
  });

  el.messageInput.addEventListener('input', autoResizeInput);
  el.messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // ── Boot ─────────────────────────────────────────────────────────
  boot();
})();
