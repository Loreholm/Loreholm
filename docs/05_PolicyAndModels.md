# Loreholm policy, model routing, and budgets

This page separates the controls implemented in the foundation from the mining
controls accepted for later milestones.

## Egress visibility requirement

Loreholm is intended to show more than the prompt a person typed. A complete
model-egress view should identify application-added context, retrieved history,
source capture classes, transformations or redactions, the selected endpoint,
the policy decision, and the effective payload or a clearly documented safer
audit representation.

This universal egress view is not implemented. Browser chat captures the user
message and completed assistant response, and Bifrost is the only permitted
model route, but the foundation does not yet present an exact-request preflight
or durable egress ledger across connected tools. See
[why Loreholm exists](00_Principles.md).

## Instance policy

**Status:** Persistence, API access, dashboard editing, and server-side capture
enforcement are implemented. Client-side spine and mining-egress enforcement
are planned.

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

For a capture ID that is not already stored, the server rejects a disabled
class before storage and returns a `policy_blocked` receipt for that item. An
already-stored ID remains a truthful `duplicate` even when current policy now
disables its class. The planned spine will enforce the current decision before
upload so blocked content ordinarily remains on the originating device. The
mining pipeline independently checks current capture and egress policy before
calling a model.

### Remote-processing modes

| Mode | Meaning | Enforcement status |
|---|---|---|
| `local_only` | No representation leaves the instance for model processing | Enforced; remote extraction fails closed |
| `sanitized_remote` | Only a deterministic sanitized representation may leave | Sanitizer planned; remote extraction currently fails closed |
| `derived_only` | Only locally derived artifacts may leave | Transformation planned; remote extraction currently fails closed |
| `unrestricted` | Raw content may reach the configured endpoint | Enforced by the extraction worker |

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

The policy exposes `active` and `paused`, defaults to `paused`, and migrates the
retired `unavailable_not_implemented` value to `paused`. Session assembly,
salience, and durable mining-work creation continue while paused, but the
mining worker claims nothing and performs no model egress. Active mining runs
structured extraction and entity resolution; it does not activate claim or
Evidence commit.

## Bifrost model boundary

**Status:** Gateway deployment, endpoint configuration, health probing, chat
inference, and structured mining extraction are implemented. Tiered role maps,
budgets, and quality checks are planned.

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
  "allow_private_network": true,
  "processing_location": "local"
}
```

`processing_location` is an operator assertion used by the egress gate; use
`local` only when the model runs inside the Loreholm instance boundary. The
endpoint must expose an OpenAI-compatible model list and chat-completions
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

The field console configures the inference provider and selects a logical
Bifrost provider/model name for embeddings. Operators own the embedding
provider's actual endpoint and credentials in Bifrost. Loreholm needs only its
route name, declared local/remote location, and output width for policy and
storage enforcement. The embedding width must match the instance's fixed
vector region. Pipeline stages are designed to declare capability tiers rather
than provider names:

- extraction: high capability;
- entity-resolution middle-band judge: low capability (implemented through the
  currently selected inference model; tier mapping remains planned);
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

The deterministic salience admission threshold and entity-resolution judge
band are implemented as versioned code configuration. Future cost controls
include an operator policy surface for those thresholds and the tier-to-model
map.

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
