const $ = (selector) => document.querySelector(selector);
let token = sessionStorage.getItem('lh-admin') || '';
let state = null;
let recentClaims = [];
let recentEvidence = [];
let maintenanceNotices = [];
let reminingRequests = [];
let extensionRelations = [];
let knowledgeSchema = {relations: {}};
let miningRuns = [];

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function notify(message, bad = false) {
  const element = $('#notice');
  element.textContent = message;
  element.style.color = bad ? 'var(--rust)' : 'var(--blue)';
}

function modeLabel(value) {
  return ({
    local_only: 'Local only',
    sanitized_remote: 'Sanitized remote',
    derived_only: 'Derived only',
    unrestricted: 'Unrestricted',
  })[value] || value;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
}

function shortId(value) {
  return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : '—';
}

function renderMaintenance() {
  const openNotices = maintenanceNotices.filter((item) => item.status === 'open');
  $('#openNoticeCount').textContent = openNotices.length;
  $('#extensionCount').textContent = extensionRelations.filter((item) => item.status !== 'reverted').length;
  $('#maintenanceNotices').innerHTML = maintenanceNotices.length
    ? maintenanceNotices.slice(0, 8).map((item) => `
      <article class="ledger-entry">
        <header><strong>${escapeHtml(item.kind.replaceAll('_', ' '))}</strong><em>${escapeHtml(item.status)}</em></header>
        <p>${escapeHtml(item.claim_id ? `Claim ${shortId(item.claim_id)}` : `Run ${shortId(item.run_id)}`)}</p>
      </article>`).join('')
    : '<p>No maintenance notices.</p>';

  const successfulRuns = miningRuns.filter((item) => item.status === 'succeeded');
  $('#reminingRun').innerHTML = successfulRuns.length
    ? successfulRuns.map((item) => `<option value="${escapeHtml(item.run_id)}">${escapeHtml(shortId(item.run_id))} · scope ${escapeHtml(shortId(item.scope_id))}</option>`).join('')
    : '<option value="">No successful runs</option>';
  $('#reminingRequests').innerHTML = reminingRequests.length
    ? reminingRequests.slice(0, 5).map((item) => `
      <article class="ledger-entry">
        <header><strong>${escapeHtml(shortId(item.source_run_id))}</strong><em>${escapeHtml(item.status)}</em></header>
        <p>${escapeHtml(item.reason)}</p>
      </article>`).join('')
    : '<p>No re-mining requests.</p>';

  const coreOptions = Object.keys(knowledgeSchema.relations || {}).map(
    (name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`,
  ).join('');
  $('#extensionRelations').innerHTML = extensionRelations.length
    ? extensionRelations.map((item) => `
      <article class="ledger-entry extension-entry" data-relation="${escapeHtml(item.relation)}">
        <header><strong>${escapeHtml(item.relation)}</strong><em>${escapeHtml(item.status)}</em></header>
        <p>${escapeHtml(item.definition.description)} · ${escapeHtml(item.schema_version)}</p>
        ${item.status === 'reverted' ? '' : `<div class="migration-controls">
          <select class="promotion-target" aria-label="Core promotion target">${coreOptions}</select>
          <input class="schema-commit" aria-label="Schema Git commit" placeholder="Git commit hash" minlength="7" maxlength="64">
          <button class="promote-extension" type="button">Promote</button>
          <button class="revert revert-extension" type="button">Revert</button>
        </div>`}
      </article>`).join('')
    : '<p>No admitted extensions.</p>';
}

function render() {
  const policy = state.policy;
  const model = state.model_endpoint;
  $('#instanceState').textContent = 'ONLINE';
  $('#instanceState').className = 'good';
  $('#bifrostState').textContent = state.bifrost_ok ? 'ONLINE' : 'OFFLINE';
  $('#bifrostState').className = state.bifrost_ok ? 'good' : '';
  $('#modelState').textContent = state.vllm_ok ? 'READY' : 'UNROUTED';
  $('#modelState').className = state.vllm_ok ? 'good' : '';
  $('#policyVersion').textContent = String(policy.version).padStart(3, '0');
  $('#miningStatus').value = policy.mining_status;
  if (model) {
    $('#modelUrl').value = model.base_url;
    $('#modelName').value = model.model_name;
    $('#embeddingProviderName').value = model.embedding_provider_name;
    $('#embeddingModelName').value = model.embedding_model_name;
    $('#embeddingDimensions').value = model.embedding_dimensions;
    $('#embeddingProcessingLocation').value = model.embedding_processing_location;
    $('#providerName').value = model.provider_name;
    $('#privateNetwork').checked = model.allow_private_network;
    $('#processingLocation').value = model.processing_location;
  }
  $('#classes').innerHTML = Object.entries(policy.classes).map(([name, config], index) => `
    <div class="class-row" data-name="${escapeHtml(name)}">
      <div class="class-name"><span class="glyph">${String(index + 1).padStart(2, '0')}</span><div>
        <h3>${escapeHtml(name)} <button class="tip" data-tip="A capture class groups one kind of source data so collection and model-egress rules can be controlled independently." aria-label="About ${escapeHtml(name)}">?</button></h3>
        <p>${config.capture ? 'Captured by permitted adapters' : 'Blocked before upload'} · ${modeLabel(config.remote_processing)}</p>
      </div></div>
      <div class="control-with-tip"><label class="toggle" aria-label="Capture ${escapeHtml(name)}"><input class="capture" type="checkbox" ${config.capture ? 'checked' : ''}><span></span></label><button class="tip" data-tip="When enabled, authorized adapters may upload this class. When disabled, clients keep disallowed queued captures local." aria-label="About capture permission">?</button></div>
      <div class="control-with-tip"><select class="mode" aria-label="Remote processing for ${escapeHtml(name)}"><option value="local_only">Local only</option><option value="sanitized_remote">Sanitized remote</option><option value="derived_only">Derived only</option><option value="unrestricted">Unrestricted</option></select><button class="tip" data-tip="Local only sends nothing out. Sanitized replaces sensitive values. Derived sends only local derivatives. Unrestricted permits raw remote processing." aria-label="About remote processing mode">?</button></div>
    </div>`).join('');
  document.querySelectorAll('.class-row').forEach((row) => {
    row.querySelector('.mode').value = policy.classes[row.dataset.name].remote_processing;
  });
  $('#claimCount').textContent = recentClaims.length;
  $('#evidenceCount').textContent = recentEvidence.length;
  $('#recentClaims').innerHTML = recentClaims.length
    ? recentClaims.slice(0, 5).map((claim) => `<article><span>${escapeHtml(claim.relation)}</span><strong>${escapeHtml(claim.object_entity_id || claim.object_literal)}</strong><small>${escapeHtml(claim.schema_version)} · ${escapeHtml(claim.lifecycle)}</small></article>`).join('')
    : '<p>No committed claims yet.</p>';
  renderMaintenance();
  $('#unlock').hidden = true;
  $('#console').hidden = false;
  $('#overallLight').style.background = 'var(--acid)';
  $('#overallText').textContent = policy.mining_status === 'active' ? 'MINING ACTIVE' : 'SYNCHRONIZED';
}

function setBifrostLinks() {
  $('#bifrostDashboardLink').href = state.bifrost_dashboard_url;
  $('#bifrostConfigureLink').href = state.bifrost_dashboard_url;
}

async function load() {
  [state, recentClaims, recentEvidence, maintenanceNotices, reminingRequests, extensionRelations, knowledgeSchema, miningRuns] = await Promise.all([
    api('/v2/admin/status'),
    api('/v2/admin/knowledge/claims?limit=25'),
    api('/v2/admin/knowledge/evidence?limit=25'),
    api('/v2/admin/maintenance/notices?limit=25'),
    api('/v2/admin/maintenance/remining?limit=25'),
    api('/v2/admin/knowledge/extensions?limit=100'),
    api('/v2/admin/knowledge/schema'),
    api('/v2/admin/mining/runs?limit=100'),
  ]);
  setBifrostLinks();
  render();
}

$('#unlockForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  token = $('#token').value.trim();
  try {
    await load();
    sessionStorage.setItem('lh-admin', token);
    $('#unlockError').textContent = '';
  } catch (error) {
    $('#unlockError').textContent = error.message;
    token = '';
  }
});

$('#savePolicy').addEventListener('click', async () => {
  try {
    document.querySelectorAll('.class-row').forEach((row) => {
      state.policy.classes[row.dataset.name] = {
        capture: row.querySelector('.capture').checked,
        remote_processing: row.querySelector('.mode').value,
      };
    });
    state.policy.mining_status = $('#miningStatus').value;
    state.policy = await api('/v2/admin/policy', {method: 'PUT', body: JSON.stringify(state.policy)});
    render();
    notify(`Policy revision ${state.policy.version} committed.`);
  } catch (error) {
    notify(error.message, true);
  }
});

$('#modelForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/v2/admin/model', {method: 'PUT', body: JSON.stringify({
      base_url: $('#modelUrl').value,
      model_name: $('#modelName').value,
      provider_name: $('#providerName').value,
      allow_private_network: $('#privateNetwork').checked,
      processing_location: $('#processingLocation').value,
      embedding_provider_name: $('#embeddingProviderName').value,
      embedding_model_name: $('#embeddingModelName').value,
      embedding_dimensions: Number($('#embeddingDimensions').value),
      embedding_processing_location: $('#embeddingProcessingLocation').value,
    })});
    notify('Bifrost routes selected. Checking signal…');
    setTimeout(async () => {
      state = await api('/v2/admin/status');
      render();
      notify(state.vllm_ok ? 'Selected inference route is ready.' : 'Routes saved; inference endpoint has not answered yet.', !state.vllm_ok);
    }, 800);
  } catch (error) {
    notify(error.message, true);
  }
});

$('#reminingForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/v2/admin/maintenance/remining', {method: 'POST', body: JSON.stringify({
      source_run_id: $('#reminingRun').value,
      reason: $('#reminingReason').value,
    })});
    $('#reminingReason').value = '';
    await load();
    notify('Safe replacement queued. Existing knowledge remains active until commit.');
  } catch (error) {
    notify(error.message, true);
  }
});

$('#extensionRelations').addEventListener('click', async (event) => {
  const entry = event.target.closest('.extension-entry');
  if (!entry || (!event.target.matches('.promote-extension') && !event.target.matches('.revert-extension'))) return;
  const relation = encodeURIComponent(entry.dataset.relation);
  const schemaCommit = entry.querySelector('.schema-commit').value.trim();
  const action = event.target.matches('.promote-extension') ? 'promote' : 'revert';
  const payload = action === 'promote'
    ? {core_relation: entry.querySelector('.promotion-target').value, schema_commit: schemaCommit}
    : {schema_commit: schemaCommit};
  try {
    await api(`/v2/admin/knowledge/extensions/${relation}/${action}`, {method: 'POST', body: JSON.stringify(payload)});
    await load();
    notify(`Extension ${entry.dataset.relation} ${action === 'promote' ? 'promoted' : 'reverted'} with a compensating migration record.`);
  } catch (error) {
    notify(error.message, true);
  }
});

if (token) load().catch(() => sessionStorage.removeItem('lh-admin'));
