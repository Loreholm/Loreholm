# Contributing to loreholm

Thanks for your interest. Before anything else, one honest paragraph about
what you're contributing to:

> **Maintenance policy.** loreholm is maintained by one person with a
> full-time job and a family. Issues and PRs are triaged in batches,
> typically on weekends. There is no SLA and no promised roadmap. Security
> reports get priority — see [SECURITY.md](SECURITY.md). If your issue sits
> for a week, it isn't being ignored; it's in the queue. Kind patience is
> the price of admission, and it's appreciated more than you know.

## The deal

- All contributions require agreeing to the [CLA](CLA.md) — a bot will ask
  on your first pull request, once, and it covers everything after.
- Server-side code (`api/`) is AGPL-3.0; client-side code (`web/`,
  `apps/chat/`) is MIT. See the README's Licensing section for why.
- The licenses govern code, never data. Memory data belongs to users alone.

## Getting started

```bash
python3.11 -m venv venv && source venv/bin/activate
pip install -r api/requirements.txt -r api/requirements-dev.txt
PYTHONPATH=api pytest api/tests   # the same command CI runs
```

The V2 stack is Docker Compose — see `docs/01_Architecture.md` for the
implemented and planned topology and `docs/V2-Development.md` for the local
development workflow. V1 operational documentation lives in Git history, not
on this branch.

## Pull requests

- Contributions must use the fork workflow: fork `Loreholm/Loreholm`, create
  the change branch in your fork, and open a pull request back to this
  repository. Contributor branches cannot be created or updated in the
  upstream repository.
- Do not request or expect upstream write access for ordinary contributions.
  Repository rules enforce the fork boundary even if write access is granted
  accidentally.
- Keep PRs focused: one change, one PR.
- CI must pass and the repository owner must approve before merge.
- Match the style of the code you're touching; there is no linter on
  purpose — read the room instead.
- Changes to the trust boundary (the V2 front-door/tunnel path, the `:8081`
  shim, Headscale/Tailscale ACL, token exchange, Compose network layout, or
  anything in `docs/13_SecurityModel.md`) get extra
  scrutiny and may take longer. That's the most load-bearing part of the
  project.
- Update `CHANGES.md` with a short entry describing what actually changed.

## What's most useful

- Bug reports with the loreholm version, OS/arch, and `docker logs` output.
- Reproductions for anything in capture ingestion, policy sync, staging, or
  the V2 install path.
- Docs corrections — drift is the enemy.
- Hardening reviews of the security model (see SECURITY.md for how to
  report anything sensitive).
