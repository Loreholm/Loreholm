from __future__ import annotations

import os  # kept at module level for tests that monkeypatch os.replace

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .core.config import (
    LOCAL_DASHBOARD_REGISTRY_FILE,
    LOCAL_DASHBOARD_STATIC_DIR,
    LOCAL_DASHBOARD_TOKEN_FILE,
    LOCAL_SYNC_TOKEN_FILE,
)
from .db.registry import _backfill_registry_if_needed, _save_registry
from .db.docker_ops import (
    _ensure_tailscale_container_running,
    _get_docker_client,
)
from .core.auth import _verify_sync_bearer_token
from .ai.bifrost import _bifrost_models, _bifrost_probe
from .ai.embeddings import get_embedding_service
from .core.models import (
    CreateDatabaseRequest,
    SyncResolveRequest,
    WizardChatRequest,
    WizardMessage,
)
from .routes.databases import create_database
from .routes.sync import sync_resolve_database_target
from .routes.wizard import (
    wizard_bifrost_status,
    wizard_chat,
    wizard_recommendation,
)
from .metrics import router as metrics_router
from .reconciler import get_supervisor as _get_reconciler_supervisor
from .routes import api_router, home_router

app = FastAPI(
    title="loreholm local dashboard",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)


@app.on_event("startup")
def _local_dashboard_startup() -> None:
    # Phase 1 back-fill: ensures every existing registry record has a schema
    # block and a derived profile_hash, and rebuilds from Docker state if the
    # registry file went missing. Idempotent; cheap on steady-state boots.
    try:
        _backfill_registry_if_needed()
    except Exception as exc:  # pragma: no cover - best-effort startup work
        print(f"[local-dashboard] startup back-fill failed: {exc}", flush=True)

    # Warm the embedding model before accepting traffic. On first container
    # start the HF cache is empty and `from_pretrained` will fetch ~300 MB
    # (Harrier) or ~80 MB (MiniLM) — blocking here means readiness implies
    # the embed path is actually usable, so the first `/api/sync/query`
    # doesn't pay the download latency inline and risk a cloud timeout.
    # Subsequent starts hit the cache and this returns in seconds.
    try:
        service = get_embedding_service()
        print(
            f"[local-dashboard] warming embedding model {service.hf_id} "
            f"({service.dimensions} dim)",
            flush=True,
        )
        service.embed("warmup")
    except Exception as exc:  # pragma: no cover - best-effort startup work
        # Don't take the dashboard down if the warm-up fails — the lazy
        # path will retry on the next embed call and surface the error to
        # the caller. Surface the root cause in logs so operators can see
        # offline-install or model-rename failures.
        print(
            f"[local-dashboard] embedding warm-up failed: {exc}",
            flush=True,
        )

    try:
        _get_reconciler_supervisor().start()
    except Exception as exc:  # pragma: no cover - best-effort startup work
        print(
            f"[local-dashboard] reconciler start failed: {exc}", flush=True
        )


@app.on_event("shutdown")
async def _local_dashboard_shutdown() -> None:
    try:
        await _get_reconciler_supervisor().stop()
    except Exception as exc:  # pragma: no cover
        print(
            f"[local-dashboard] reconciler stop failed: {exc}", flush=True
        )


app.mount(
    "/assets",
    StaticFiles(directory=str(LOCAL_DASHBOARD_STATIC_DIR)),
    name="local_dashboard_assets",
)
app.include_router(home_router)
app.include_router(api_router)
# Prometheus scrape endpoint at the root `/metrics` — no `/api` prefix so
# standard scrape configs work without rewriting.
app.include_router(metrics_router)
