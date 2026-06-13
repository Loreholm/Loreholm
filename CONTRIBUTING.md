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

The full local stack is Docker Compose — see `docs/01_Architecture.md` for
the topology and `docs/07_BYODB.md` for how the pieces talk to each other.

## Pull requests

- Keep PRs focused: one change, one PR.
- CI (tests on Python 3.11) must pass; there are no other gates.
- Match the style of the code you're touching; there is no linter on
  purpose — read the room instead.
- Changes to the trust boundary (the Headscale ACL, the `:8081` shim, the
  compose netns layout, anything in `docs/13_SecurityModel.md`) get extra
  scrutiny and may take longer. That's the most load-bearing part of the
  project.
- Update `CHANGES.md` with a short entry describing what actually changed.

## What's most useful

- Bug reports with the loreholm version, OS/arch, and `docker logs` output.
- Reproductions for anything in the sync, reconciler, or install path.
- Docs corrections — drift is the enemy.
- Hardening reviews of the security model (see SECURITY.md for how to
  report anything sensitive).
