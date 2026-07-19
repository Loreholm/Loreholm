# Security Policy

loreholm is a trust product: captured context and mined knowledge belong on
instances users control. Security reports are therefore the highest-priority
work in the project. The current trust model — including its loopback-only
base stack and front-door/Tailscale deployment — is documented in
[docs/13_SecurityModel.md](docs/13_SecurityModel.md); claims in that document
are explicitly in scope for review.

## Reporting a vulnerability

**Please do not open a public issue for anything you believe is a
vulnerability.**

Preferred: **GitHub private vulnerability reporting** on this repository
(Security → Report a vulnerability), which keeps the report private and
tracked.

Alternative: email **kevin.dowling@kevindowling.dev** with "SECURITY" in
the subject line.

## What to expect

This project has a single maintainer with a day job, so the commitments
below are honest rather than impressive:

- **Acknowledgment within 7 days** of a report.
- Assessment and a fix plan as fast as severity warrants — issues that affect
  device/admin/sync authentication, the `:8081` endpoint shim, token
  exchange, or the Compose network boundary jump every other queue.
- Credit in the changelog and release notes, unless you prefer otherwise.
- No legal threats for good-faith research. Testing against your own
  install is encouraged; testing against the hosted service or other
  users' machines is not authorized.

## Scope notes

- The Loreholm instance API (`:8082`) and Bifrost management proxy (`:8083`) bind to
  loopback by default. ArcadeDB and Bifrost inference remain private to the
  Compose bridge. Intentional LAN rebinding is an operator-controlled risk,
  but reports that defaults or warnings are unsafe are welcome.
- In the current tunnel implementation, the Tailnet-facing `:8081` shim must
  expose only health and `/api/chat/*`. Any new route requires an explicit Loreholm
  application contract and security review. Direct reachability of ArcadeDB,
  Bifrost, Docker, or the host through that shim is in scope.
- Loreholm secrets live in the mode-0600
  `~/.local/share/loreholm-v2/state/instance.env`; local privilege escalation
  on a user's already-compromised host is out of scope.
