# Loreholm V2 networking

V2 preserves Loreholm's front-door and private-tunnel architecture. The major
V2 change is what the local instance does with context, not where user data
lives or how remote clients reach it.

## Topology

```text
Internet
   |
   | HTTPS
   v
front door server
   |- OIDC authentication
   |- user-to-node resolution through Headscale
   `- public API and browser surfaces
            |
            | encrypted Tailscale tunnel
            | cloud identity -> one user's :8081 endpoint
            v
user machine
   |- tailscale container (only Tailnet identity)
   |- endpoint container (shares Tailscale network namespace)
   `- private Compose bridge
        |- V2 instance API
        |- ArcadeDB
        `- Bifrost
```

The public server is the front door; it is not the memory store. Raw captures,
future mined graph data, model configuration, and instance credentials remain
with the user's containerized instance.

## Retained invariants

These properties carry forward into V2:

1. **One public front door.** Browsers and remote clients authenticate to the
   public service rather than dialing a database or arbitrary home-network
   port.
2. **Headscale coordinates the private network.** It controls Tailscale node
   registration and lets the front door resolve the user's current Tailnet
   address.
3. **The host does not join the Tailnet.** Tailscale runs in a container with
   its own network namespace.
4. **One narrow Tailnet ingress.** A small endpoint container shares the
   Tailscale namespace and listens on `:8081`; the application, database, and
   model gateway do not.
5. **Application access, not database access.** The endpoint forwards an
   explicit allow-list of V2 routes to the instance over the private Docker
   bridge. It never forwards raw ArcadeDB or Bifrost ports.
6. **User isolation.** The Headscale/Tailscale ACL allows the front-door
   identity to reach user endpoint nodes and denies user-to-user paths and
   other ports by default.
7. **Local persistence.** The tunnel carries authorized requests and responses;
   durable context and knowledge remain in the user's ArcadeDB volumes.
8. **Credential separation.** Public OIDC credentials terminate at the front
   door. It substitutes a per-instance synchronization credential when dialing
   the private endpoint.

## What changes in V2

V1 used the tunnel primarily for MCP-generated memory and graph operations.
V2 uses the same protected route to reach an instance that captures raw
context and, once implemented, mines and surfaces its own knowledge graph.

The network must not reintroduce V1's authority model. Passing through the
front door does not authorize a client to submit interpreted entities or graph
facts. Remote capture inputs remain raw capture envelopes, and graph mutation
remains an instance-owned mining responsibility.

## Current implementation

The executable V2 repository contains both halves of the retained path:

- The cloud chat proxy authenticates an OIDC user, resolves their Tailscale IP,
  derives the user's sync credential, and dials port `8081`.
- `deploy/docker-compose.v2.remote.yml` runs the Tailscale and endpoint
  containers.
- `deploy/v2-endpoint-shim.py` forwards `/api/chat/*` and returns 404 for other
  application routes.
- The V2 instance validates the sync credential, proxies inference through
  Bifrost, and stores both sides of the conversation as raw captures.

This means the retained topology is implemented end to end for V2 browser
chat. It is not yet connected to every future V2 capability. The capture spine,
remote policy synchronization, mined-graph queries, and sharing will extend the
route set only when their application contracts and authorization rules exist.

## Container boundaries

| Container | Network position | Intended exposure |
|---|---|---|
| Front-door API sidecar | Cloud Tailnet identity | Outbound to user endpoint `:8081` |
| User Tailscale sidecar | User Tailnet identity | Tailnet coordination only |
| User endpoint shim | Shares user Tailscale namespace | Allow-listed V2 routes on `:8081` |
| V2 instance | Private Compose bridge | Endpoint shim and explicitly bound local/LAN access |
| ArcadeDB | Private Compose bridge | V2 instance only |
| Bifrost | Private Compose bridge | V2 instance only |

## Rules for extending the tunnel

Every new remotely reachable V2 feature should answer these questions before
its route is added:

- Which authenticated front-door action requires it?
- Which per-instance credential reaches the local endpoint?
- Is the route narrowly allow-listed by method and path?
- Does the instance re-authorize the operation rather than trusting the shim?
- Can the feature work without exposing ArcadeDB, Bifrost, Docker, or the host?
- Does the response avoid turning the front door into durable memory storage?

Changes to the Headscale ACL, Tailscale namespaces, `:8081` shim, credential
exchange, or container bridge are changes to the security boundary.
