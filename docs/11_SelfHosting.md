# Host Loreholm on your own server

Yes. Loreholm can run on infrastructure you control. You can host only the
private instance that holds your memory, or you can operate the public-facing
server plane as well. Using Loreholm does not require making Loreholm's
maintainers the permanent custodian of your data or your network.

## Two things you can host

Loreholm separates the place where memory lives from the front door used to
reach it:

```text
server plane                         private instance
  sign-in and browser UI               instance API
  front-door API                       ArcadeDB data
  Headscale coordination   tunnel      model gateway
  encrypted route endpoint  ------->   captured context and knowledge
```

The **private instance** is the important ownership boundary. It stores raw
captures, future mined knowledge, policy, and model configuration. It can run
on a Linux workstation, a home server, or a private Linux VM with Docker. The
database and model gateway stay behind its private container network.

The **server plane** is the internet-facing entrance. It provides the browser
surface, OIDC sign-in, API, Headscale coordination, TLS termination, and the
Tailnet identity used to reach private instances. It relays authorized requests
but is not designed to become the durable memory store.

## Choose how much to operate

### Host your private instance

This is the smallest self-hosted arrangement. Run the instance stack on a
machine you control and keep its API bound to loopback by default. The standard
Loreholm front door can provide remote access through the private tunnel while
your lasting data remains on your machine.

See [instance operations](04_InstanceOperations.md) for installation and
maintenance, and [Loreholm networking](02_Networking.md) for the remote route.

### Host the whole Loreholm service

Operators who want control of the public entrance can run the server plane too.
The repository includes `deploy/server-plane/docker-compose.yml`, which defines:

| Service | Responsibility |
|---|---|
| Caddy | Public HTTPS and certificate management |
| Chat | Browser conversation surface |
| API | Authentication, onboarding, and front-door routing |
| Headscale | Coordination for the private Tailscale network |
| Tailscale API sidecar | Server-plane identity inside that network |
| Redis | Ephemeral API-key and service state |

Each user's private instance remains a separate deployment. Hosting the server
plane does not merge user databases into the public server or expose ArcadeDB
through it.

### Stay local

An instance can also be used from its host without a public front door. Its API
binds to `127.0.0.1` by default. If you make it reachable on a LAN or another
private network, you become responsible for authenticated TLS, firewall rules,
and access to that network. Do not publish ArcadeDB or Bifrost directly.

## What a server-plane operator needs

Running the complete service is ordinary infrastructure work, not a one-click
consumer install. The current server-plane bundle expects:

- a public Linux host with Docker Engine and Docker Compose;
- three DNS names for chat, API, and Headscale traffic;
- inbound ports `80` and `443` for Caddy and certificate issuance;
- an OIDC provider, client, and API audience;
- high-entropy synchronization, API-key, and Redis secrets;
- a Headscale configuration whose public server URL matches the mesh DNS name;
- persistent Docker volumes, monitoring, upgrades, and recovery procedures.

The browser and API container images are published through the repository's
image workflows. The Compose bundle accepts alternate image references, so an
operator can build and pin their own images instead.

## Trust changes when you host it all

Self-hosting the server plane gives you control over authentication, TLS,
Headscale, and the software relaying live requests. It does not remove every
external dependency automatically: your chosen OIDC provider, DNS provider,
certificate authority, and any remote model endpoint still have their own trust
boundaries.

The server plane can handle request content while relaying a live operation,
but durable captured context and knowledge remain in the private instance by
design. The tunnel is an application route, not database access.

## Current support boundary

The private instance installer and the server-plane Compose definition exist
today. The repository does not yet provide a one-command server-plane installer,
managed backups, automated disaster recovery, or a complete production
operations runbook. Operators should review and pin images, replace deployment
placeholders, protect secrets, test restores, and verify the security invariants
before inviting other users.

The non-negotiable boundaries are documented in the
[security model](13_SecurityModel.md): only explicit application routes cross
the tunnel, public identity credentials stay at the front door, and databases
and model gateways are never published directly.

Teams planning a shared deployment should also read
[using Loreholm in an organization](12_Organizations.md) for pilot scope,
licensing, operational ownership, and production-adoption gates.
