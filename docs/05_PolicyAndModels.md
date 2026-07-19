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

The server rejects disabled classes before storage and returns a
`policy_blocked` receipt for that item. The planned spine will enforce the same
decision before upload so blocked content ordinarily remains on the originating
device. The eventual mining pipeline must independently defend the policy at
its own boundary.

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

The current policy schema advertises only
`unavailable_not_implemented`. The dashboard renders that state as sealed and
does not offer an inert activation control. Existing stored policies are
migrated to this value at startup.

Session assembly and the durable admission queue operate while mining is
unavailable, but nothing consumes those items or sends them to a model. The
future miner will add active and paused states when it can actually enforce
them. Capture will continue during a mining pause.

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
