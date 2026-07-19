# Loreholm documentation

This branch documents **Loreholm V2**. V2 captures raw context and will mine
knowledge inside the user's instance; it does not use V1's client-directed MCP
write contract.

## Current V2 documentation

| Document | Purpose |
|---|---|
| [Project README](../Readme.md) | Current milestone, installation, and smoke test |
| [V2 architecture](01_Architecture.md) | Implemented capture foundation and planned mining pipeline |
| [V2 networking](02_Networking.md) | Retained front-door, Headscale/Tailscale, and container topology |
| [Capture API](03_CaptureAPI.md) | Implemented envelope, policy, receipt, idempotency, and quarantine contract |
| [Instance operations](04_InstanceOperations.md) | Installation, configuration, health, updates, and removal |
| [Policy and models](05_PolicyAndModels.md) | Capture policy, model-egress modes, Bifrost, and planned budgets |
| [Clients and spine](06_ClientsAndSpine.md) | Planned adapter boundary, offline queue, policy enforcement, and revocation |
| [Mining and knowledge](07_MiningAndKnowledge.md) | Planned mining stages, identity, provenance, schema, and surfacing |
| [Data lifecycle](08_DataLifecycle.md) | Planned retention, deletion, backup, restore, and sharing |
| [Browser chat](09_Chat.md) | Implemented OIDC, tunnel, streaming, and transcript-capture path |
| [V2 development stack](V2-Development.md) | Local stack, model development, and tunnel overlay |
| [V2 security model](13_SecurityModel.md) | Local and front-door/tunnel trust boundaries |
| [Architecture decisions](../notes/Architecture-Decisions.md) | Accepted product and data-model decisions, including planned work |

The architecture page uses explicit **Implemented** and **Planned** labels.
That distinction is important in the current foundation milestone: capture
ingestion and durable staging work today, while mining, graph commit, and graph
query/surfacing remain design work.

Pages covering mixed milestones begin with a status statement and label
unimplemented controls directly. `notes/Architecture-Decisions.md` remains the
full design record; the numbered guides are the operational and conceptual
view intended for implementers and users.

## Version boundary

V1 operational guides are intentionally absent from this branch. Git history
preserves them with the V1 implementation; keeping them in the V2 docs tree
would make retired MCP tools, multi-database deployment, and V1 application
contracts look like supported V2 behavior. The network and container security
model are intentionally retained and documented for V2.

V2 uses `web/install-v2.sh`, the `/v2/*` capture contract, and a self-contained
one-database instance.
