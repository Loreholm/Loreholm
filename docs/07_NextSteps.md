# Next Steps & Roadmap

Planned work that is not yet built. Items here are intentionally forward-looking
— treat them as design intent, not as a description of shipped behavior. For
what exists today, see [01_Architecture.md](01_Architecture.md) and
[13_SecurityModel.md](13_SecurityModel.md).

## Connection & Security panel (local dashboard)

A first-class panel in the local dashboard that makes the
[trust model](13_SecurityModel.md) **visible and verifiable** from the UI,
instead of something a user has to take on faith or check from the terminal.
The goal is to convert the security guarantees that already hold into a sense
of security the user can see.

Planned contents:

- **Mesh status** — a live read of `tailscale status` for the
  `loreholm-tailscale` container: connected / paused, this node's name and
  Tailnet IP, and last handshake with the control plane.
- **Exposure summary** — plain-language statement of what is reachable from
  the mesh ("The cloud can reach port 8081 only") and what is not (`:2480`
  ArcadeDB, `:8080` Bifrost, `:4466` dashboard), rendered from the actual
  topology rather than hardcoded copy.
- **The applied ACL** — render `deploy/headscale-acl.hujson` (or the
  effective policy) inline so the user can see the `*:8081`-only +
  implicit-deny rule that governs their node.
- **Cloud-access audit log** — a local, append-only log of every
  cloud-originated `/api/sync/*` request the `:8081` shim handled: timestamp,
  API key, tool/query type, and policy outcome (allowed / denied). This is the
  highest-value item: "here is exactly what the cloud asked for, and when"
  is more convincing than any promise. Note the shim currently suppresses
  request logging (`log_message` is a no-op) — this work includes adding
  structured, retained logging behind the panel.
- **Pause / resume control** — a button that stops/starts the
  `loreholm-tailscale` container (drop off / rejoin the mesh) without disturbing
  data, with a clear Connected / Paused indicator. People trust what they can
  switch off.

Dependencies / notes:

- The audit log needs the shim (`endpoint_server.py`, embedded in the
  install/update scripts) to emit structured records, plus a retained sink the
  dashboard can read; users would pick up the shim change by re-running
  `update.sh` / `update.ps1`.
- Pause/resume needs the dashboard to control the Tailscale container
  lifecycle (Docker access), which is a privilege boundary to design carefully.

## Other planned work

These are known follow-ups already referenced elsewhere in the codebase/docs:

- **Live in-session tool-change propagation.** Today the MCP server advertises
  `{"listChanged": false}` and has no session registry, so schema/tool edits
  take effect on the client's next `tools/list` (usually a new session).
  In-session propagation requires an SSE stream, a session registry, and
  cross-instance pub/sub. See [07_BYODB.md](07_BYODB.md) §7.
- **Backup-node semantics.** `get_user_tailscale_ip` currently returns the
  first online node, which is non-deterministic when a user has 2+ online
  devices each carrying their own ArcadeDB. Design active/standby selection and
  a `database_target → node` binding (tracked as `TODO(backup-node)`).
- **Tier-from-claims.** `_get_user_node_cap` is a stub returning the free-tier
  cap; wire it to real tier information from the user's claims.
- **Restore `autogroup:member` in the ACL.** The ACL uses `*:8081` instead of
  `autogroup:member:8081` because Headscale 0.25.x's Policy v2 doesn't
  recognize `autogroup:member`. Switch back once the control plane is on
  0.27+.

## Quick links

- [13_SecurityModel.md](13_SecurityModel.md) — the trust model the panel surfaces.
- [01_Architecture.md](01_Architecture.md) — current architecture and diagram.
- [09_HeadscaleSetup.md](09_HeadscaleSetup.md) — networking and ACL setup.
