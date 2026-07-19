# Loreholm documentation

These guides document Loreholm's current capture foundation and the deeper
knowledge-building system it is growing into. Raw context and derived knowledge
remain inside the user's instance.

## Current Loreholm documentation

| Document | Purpose |
|---|---|
| [Project README](../Readme.md) | Current milestone, installation, and smoke test |
| [Loreholm architecture](01_Architecture.md) | Implemented capture foundation and planned mining pipeline |
| [Loreholm networking](02_Networking.md) | Retained front-door, Headscale/Tailscale, and container topology |
| [Capture API](03_CaptureAPI.md) | Implemented envelope, policy, receipt, idempotency, and quarantine contract |
| [Instance operations](04_InstanceOperations.md) | Installation, configuration, health, updates, and removal |
| [Policy and models](05_PolicyAndModels.md) | Capture policy, model-egress modes, Bifrost, and planned budgets |
| [Clients and spine](06_ClientsAndSpine.md) | Planned adapter boundary, offline queue, policy enforcement, and revocation |
| [Mining and knowledge](07_MiningAndKnowledge.md) | Planned mining stages, identity, provenance, schema, and surfacing |
| [Data lifecycle](08_DataLifecycle.md) | Planned retention, deletion, backup, restore, and sharing |
| [Browser chat](09_Chat.md) | Implemented OIDC, tunnel, streaming, and transcript-capture path |
| [Loreholm development stack](10_Development.md) | Local stack, model development, and tunnel overlay |
| [Loreholm security model](13_SecurityModel.md) | Local and front-door/tunnel trust boundaries |

The architecture page uses explicit **Implemented** and **Planned** labels.
That distinction is important in the current foundation milestone: capture
ingestion and durable staging work today, while mining, graph commit, and graph
query/surfacing remain design work.

Pages covering mixed milestones begin with a status statement and label
unimplemented controls directly. The numbered guides are the operational and
conceptual view intended for implementers and users.

The technical guides include exact installer names and API paths where they
are needed. The product release documented here is Loreholm 1.0.
