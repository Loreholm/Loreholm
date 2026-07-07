# Dev scripts

Helpers for iterating on the **local dashboard** without doing a full BYODB
deploy. These are for contributors working in this repo — the end-user install
lives in `web/` and is documented in the platform docs (`docs/`).

| Script | What it does |
|--------|--------------|
| `dev-local-dashboard.sh` / `.ps1` | Bring up the dev loop: bootstrap `./venv`, seed `.dev-state/`, start ArcadeDB + Bifrost (`deploy/docker-compose.dev.yml`), and run the dashboard under `uvicorn --reload`. |
| `clean-local-dashboard.sh` / `.ps1` | Tear it all back down: dev containers + volumes, wizard-created `loreholm-arcadedb-*` containers, and `.dev-state/`. Leaves `./venv` in place. |

```bash
scripts/dev-local-dashboard.sh          # Linux / macOS
scripts\dev-local-dashboard.ps1         # Windows (PowerShell)
# then open http://127.0.0.1:4466/dev/login once to set the session cookie
```

## The dev loop mirrors a real first install

The script seeds an **empty** database registry (`{"version":1,"databases":[]}`),
exactly like `web/install.sh` ships to a real user. Nothing is pre-created for
you, so the first thing you do is the same thing a real user does: **create a
database through the dashboard**. That's deliberate — the dev loop doubles as a
walk-through of the product's first-run experience.

Creating a database from the dashboard is a single coupled operation: it writes
the record into `databases.json` **and** runs `CREATE DATABASE` against ArcadeDB
(plus schema/index bootstrap). Both halves always happen together.


## Skipping the click (scripted database create)

If you're re-running the loop a lot and don't want to click through the UI each
time, create the database by calling the same endpoint the dashboard uses. It's
the *coupled* create (registry + ArcadeDB + bootstrap), so it stays consistent —
don't shortcut it with a raw ArcadeDB `CREATE DATABASE`, which skips the schema
bootstrap and re-introduces registry/DB skew.

The endpoint requires a dashboard **session cookie**. In dev mode that cookie is
the pre-seeded `dev-session`, activated by hitting `/dev/login` once:

```bash
# 1. Activate the dev session and capture the cookie
curl -s -c /tmp/loreholm-dev.cookies http://127.0.0.1:4466/dev/login >/dev/null

# 2. Create the database (registry + ArcadeDB + bootstrap, in one call)
curl -s -b /tmp/loreholm-dev.cookies \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:4466/api/databases \
  -d '{"database_id":"dev","name":"Dev"}'
```

`database_id` must match `^[a-z0-9][a-z0-9_-]{0,99}$`; `name` is the display
label. The call is safe to repeat — a second attempt returns `409
DATABASE_ALREADY_EXISTS` rather than duplicating anything.

If a registry and ArcadeDB ever *do* drift (e.g. a create that half-failed), the
dashboard exposes `POST /api/databases/rebuild`, which rebuilds the registry from
the databases that actually exist on the server.
