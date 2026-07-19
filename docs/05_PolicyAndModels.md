# Loreholm policy, model routing, and budgets

This page separates the controls implemented in the foundation from the mining
controls accepted for later milestones.

## Instance policy

**Status:** Persistence, API access, and dashboard editing are implemented.
Client-side spine enforcement and mining enforcement are planned.

Policy is instance-owned and stored in ArcadeDB as `V2InstanceConfig`. It is
not owned by the public front door. Clients authenticate to `GET /v2/policy`
and will eventually cache the latest policy for offline enforcement.

### Capture classes

The default class registry is:

- `transcript.message`
- `push.document`
- `push.screen`

Each class has two current controls:

| Field | Values | Meaning |
|---|---|---|
| `capture` | Boolean | Whether an authorized adapter may upload this class |
| `remote_processing` | See below | Which representation a non-local model endpoint may receive |

The implemented server currently advertises and persists these controls. It
does not yet reject capture ingestion based on `capture: false`; enforcement
belongs in the planned spine and must also be defended by the eventual mining
pipeline. Until those pieces exist, the toggle is policy state rather than a
complete end-to-end guarantee.

### Remote-processing modes

| Mode | Meaning | Enforcement status |
|---|---|---|
| `local_only` | No representation leaves the instance for model processing | Planned mining enforcement |
| `sanitized_remote` | Only a deterministic sanitized representation may leave | Planned sanitizer and enforcement |
| `derived_only` | Only locally derived artifacts may leave | Planned mining enforcement |
| `unrestricted` | Raw content may reach the configured endpoint | Planned mining enforcement |

Permission to capture and permission to send content to a model are separate.
Changing model egress must not silently enable a disabled capture class.

### Policy revisions

`PUT /v2/admin/policy` requires the administrator token. The service increments
the submitted policy version before persisting it, so the stored version is the
server's next revision rather than a client-selected value.

The future spine should include the policy version used at capture time, refresh
before replaying an offline queue, and keep captures local when newer policy no
longer allows upload.

## Mining status

The current policy schema advertises:

- `active`
- `paused_budget_exhausted`
- `paused_gateway_unavailable`

The dashboard can edit this field, but no miner currently consumes it. In the
accepted design, capture continues during either pause while new inference
waits in the instance queue.

## Bifrost model boundary

**Status:** Gateway deployment, endpoint configuration, health probing, and
chat inference are implemented. Tiered mining roles and quality checks are
planned.

All instance model calls go through Bifrost. A miner must never fall back to a
provider directly because that would bypass endpoint trust, egress policy,
logging, and budget enforcement.

Configure the current endpoint through the dashboard or:

```http
PUT /v2/admin/model
Authorization: Bearer <LOREHOLM_V2_ADMIN_TOKEN>
Content-Type: application/json

{
  "base_url": "http://vllm:8000",
  "model_name": "loreholm-local",
  "provider_name": "vllm-local",
  "allow_private_network": true
}
```

The endpoint must expose an OpenAI-compatible model list and chat-completions
surface. The foundation configures it as a keyless Bifrost provider. Provider
credentials and richer provider-specific configuration should be managed in
Bifrost rather than added to capture clients.

### Development model

The supported development route is:

```text
instance -> Bifrost -> vLLM -> loreholm-local
```

The GPU development overlay defaults to cached `Qwen/Qwen3-8B` weights served
by NVIDIA vLLM. It sets Hugging Face offline mode and provides no cloud or
Ollama fallback.

## Planned mining roles

Pipeline stages declare capability tiers rather than provider names:

- extraction: high capability;
- entity-resolution middle-band judge: low capability;
- episode summary: medium capability;
- schema maintainer: high capability.

An instance maps tiers and named roles to models. Every tier may use the same
model; separate assignments are controls, not requirements. Schema proposer
and reviewer remain separately assignable even if a user intentionally maps
them to one model.

Model assignment will run a small advisory self-test for structured output and
required signals. Failure warns rather than blocks, and records the test-suite,
model, and configuration versions.

## Planned budget enforcement

Budget ownership stays with the instance, not the provider or front door.

- Budgets are monetary in an instance-configured currency.
- The miner reserves estimated cost before a call and reconciles actual cost
  afterward.
- Provider-reported cost is preferred; configured price tables are fallback.
- Local models with no monetary inference charge consume zero budget.
- Exhaustion stops new inference without stopping capture or silently changing
  models.
- Manual, calendar, and rolling replenishment policies are accepted design.

Other cost controls are the salience admission threshold, tier-to-model map,
and width of the entity-resolution LLM judge band.

None of the budget ledger, reservation, replenishment, model tiers, role map,
or quality self-test is implemented in the foundation milestone.

## Security implications

- `allow_private_network` lets Bifrost call private addresses; enable it only
  for endpoints the instance operator trusts.
- Browser clients never receive Bifrost credentials or direct inference
  network access.
- Bifrost's management proxy is authenticated and blocks `/v1/*` on its host
  port; instance inference stays on the Compose bridge.
- Raw request/response storage is disabled in the provider configuration the
  Loreholm dashboard creates.
