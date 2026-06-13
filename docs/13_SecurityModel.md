# Trust Model & Security (4 min read)

Installing loreholm means running networking software on your machine that
connects to a control server you don't operate. That's a reasonable thing to
be cautious about, especially on a personal computer that has other things on
it. This page explains exactly what that connection can and cannot do, and how
you can verify every claim yourself.

**The short version:** your computer does not join our network. A single,
isolated Docker container does. From the outside, exactly one port is
reachable, only by the loreholm API, and only to run read-only queries you
control. Everything else is denied by default.

## 1. Your computer never joins the mesh — a container does

The Tailscale client does **not** run on your host operating system. It runs
inside the `loreholm-tailscale` Docker container, which has its own isolated
network namespace. That means:

- The Tailnet interface (`tailscale0`), the Tailnet IP, and the mesh routing
  table live **inside that container** — not on your host.
- Your host machine has **no Tailnet IP**, is **not addressable** by our
  cloud or by any other node, and its own networking, DNS, and routes are
  untouched.
- Only one other container — the `:8081` endpoint shim — opts into that
  container's network namespace. The database (`:2480`), the LLM gateway
  (`:8080`), and the dashboard (`:4466`) all live on a normal Docker bridge
  and are never placed on the Tailnet at all.

So "connecting to a stranger's server" is really "running an isolated
networking sandbox in a container." Stopping that one container
(`docker stop loreholm-tailscale`) removes your machine from the mesh entirely,
without touching your data.

## 2. The cloud can reach exactly one port — everything else is denied

Reachability is enforced by the Headscale ACL
(`deploy/headscale-acl.hujson`), which is short enough to read in full:

```hujson
"acls": [
  {
    "action": "accept",
    "src": ["group:api"],
    "dst": ["*:8081"]
  }
  // IMPLICIT DENY: all other communication is blocked
]
```

The cloud API may reach `:8081` and nothing else. Every other port, and every
machine-to-machine path, is blocked by the implicit-deny rule. Other users'
machines cannot reach yours, and yours cannot reach theirs.

This is defended in **three independent layers**, so a regression in any one
still leaves the other two standing:

1. **The ACL** permits only `:8081`.
2. **The network namespace** — only the shim is on the Tailnet, so `:2480`,
   `:8080`, and `:4466` aren't even listening on the Tailnet interface.
3. **The shim's own routing** — it forwards only `/api/sync/*` (queries) and
   `/api/chat/*` (the optional chat app) and returns 404 for anything else.

## 3. The cloud can only pull, and only read

- **Pull-only.** The loreholm application on your machine never initiates an
  outbound connection to our cloud API. All cloud↔local communication is
  started by the cloud, inbound, over the mesh. (The only outbound traffic is
  the Tailscale client maintaining its standard mesh connection to the control
  plane — coordination, not your data.)
- **Read-first, with a local firewall.** Every query the cloud sends arrives
  at the local dashboard's `POST /api/sync/query`, where it passes a policy
  hook — read-only enforcement, per-key rate limits, a Cypher language guard,
  and any policy rules you author — **before** it is allowed to touch the
  database. Writes do not commit directly; they land as staging proposals that
  the local reconciler decides on.

## 4. Your data and credentials stay on your machine

- Your memories live only in the local ArcadeDB server's Docker volumes on
  your machine. The cloud never stores your data.
- Database credentials (the ArcadeDB root password, sync/API tokens) live in
  files under `~/.loreholm/` on your machine. The cloud does **not** keep a
  copy of your database host, port, or credentials — it routes to your machine
  by Tailnet IP and lets the local dashboard hold the credentials.

## 5. Hardened by default

The Tailscale container is configured to minimize what the control server and
the container can do:

- **No `--accept-routes`.** A leaf node never needs subnet routes advertised
  to it, so this is disabled — the control server cannot push routes to steer
  your node's traffic.
- **Minimal Linux capabilities.** The container runs with `NET_ADMIN` only
  (needed to create the VPN interface). It does **not** get `SYS_MODULE`,
  which would let a container load kernel modules into your host.
- **No host networking.** The container is never run with `network_mode: host`.

## What you're trusting (the honest part)

No system is trust-free; here is the full list of what installing loreholm
asks you to trust, so there are no surprises:

- **The container images** you pull (`tailscale/tailscale`,
  `arcadedata/arcadedb`, `maximhq/bifrost`, and the loreholm dashboard
  image). They run on your machine.
- **The install script**, which you can read before running — it only writes
  to `~/.loreholm/` and starts the containers described above.
- **The Headscale control plane** for *coordination* of the mesh. The layers
  above are specifically designed so that even a misbehaving control plane
  cannot reach past `:8081` or push routes to your node.

What loreholm **cannot** do: reach any port on your machine other than the
shim's `:8081`, see or route your other network traffic, read files outside
the paths you mount, or have your node initiate data uploads to our cloud.

## Verify it yourself

```bash
# Your HOST has no Tailnet interface — this returns nothing:
ip addr | grep tailscale

# Only the container is on the mesh; see its single node identity:
docker exec loreholm-tailscale tailscale status

# See what is running and which ports are published to your machine:
docker ps

# Read the ACL that governs what the cloud can reach (in this repo):
cat deploy/headscale-acl.hujson
```

## Pause, disconnect, or remove

```bash
# Drop OFF the mesh (cloud can no longer reach you); data is preserved:
docker stop loreholm-tailscale

# Stop everything (dashboard, database, mesh); data is preserved:
cd ~/.loreholm && docker compose down

# Remove the node from the mesh and delete everything:
cd ~/.loreholm && docker compose down
docker volume ls | grep loreholm | awk '{print $2}' | xargs docker volume rm
rm -rf ~/.loreholm
```

## Related

- [01_Architecture.md](01_Architecture.md) — the diagram and the
  cloud/Tailnet trust boundary.
- [07_BYODB.md](07_BYODB.md) — the query-proxy topology and sync protocol.
- [09_HeadscaleSetup.md](09_HeadscaleSetup.md) — the private networking setup.
- [07_NextSteps.md](07_NextSteps.md) — planned trust features, including the
  in-dashboard **Connection & Security** panel.
