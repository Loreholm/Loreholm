# Security Policy

loreholm is a trust product: the entire value proposition is that your
memory data stays on machines you control, reachable by the cloud only
through a narrow, auditable path. Security reports are therefore the
highest-priority work in the project. The trust model — what installing
loreholm does and does not let the cloud reach — is documented in
[docs/13_SecurityModel.md](docs/13_SecurityModel.md); claims in that
document are explicitly in scope for review.

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
- Assessment and a fix plan as fast as severity warrants — issues that
  affect the trust boundary (the Headscale ACL, the `:8081` endpoint shim,
  sync-token auth, the netns layout) jump every other queue.
- Credit in the changelog and release notes, unless you prefer otherwise.
- No legal threats for good-faith research. Testing against your own
  install is encouraged; testing against the hosted service or other
  users' machines is not authorized.

## Scope notes

- The local stack binds ArcadeDB (`:2480`) and Bifrost (`:8080`) to the
  Docker bridge only; the dashboard (`:4466`) binds to the LAN by user
  choice at install time. Reports that these are reachable from the
  *Tailnet* are vulnerabilities; reports that they are reachable from the
  user's own LAN reflect configuration, not a flaw — but reports that the
  defaults are foot-guns are still welcome.
- Secrets in install state (`~/.loreholm/*.token`) are `chmod 600` by
  design; local-privilege-escalation reports about a user's own machine
  are out of scope.
