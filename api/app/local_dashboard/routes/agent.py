from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..core.auth import _now_iso, require_agent_auth, require_sync_auth
from ..ai.bifrost import _bifrost_error_message, _bifrost_models
from ..core.config import (
    LOCAL_API_KEY_FILE,
    LOCAL_DASHBOARD_BIFROST_CONFIG_FILE,
    LOCAL_DASHBOARD_BIFROST_CONTAINER,
    LOCAL_DASHBOARD_REGISTRY_FILE,
    LOCAL_DASHBOARD_TAILSCALE_CONTAINER,
    LOCAL_DASHBOARD_TOKEN_FILE,
    LOCAL_SYNC_TOKEN_FILE,
)
from ..db.docker_ops import _get_docker_client, _http_exception_message
from ..db.graph import _database_summary
from ..db.registry import _load_registry
from ..services import bifrost_client

try:
    from docker.errors import NotFound as DockerNotFound
except ImportError:  # pragma: no cover
    DockerNotFound = Exception  # type: ignore[assignment]

router = APIRouter()


@router.get("/sync/healthz")
def sync_healthz(_: None = Depends(require_sync_auth)) -> dict[str, Any]:
    return {"ok": True, "timestamp": _now_iso(), "service": "local-sync"}


@router.get("/agent/status")
def agent_status(_: None = Depends(require_agent_auth)) -> dict[str, Any]:
    result: dict[str, Any] = {
        "timestamp": _now_iso(),
        "bifrost": {},
        "databases": [],
        "containers": [],
        "config_files": {},
    }

    # Bifrost
    try:
        models = _bifrost_models()
        try:
            providers = sorted(bifrost_client.list_providers().keys())
        except bifrost_client.BifrostClientError:
            providers = []
        result["bifrost"] = {
            "reachable": True,
            "ready": True,
            "models": models,
            "model_count": len(models),
            "providers": providers,
        }
    except HTTPException as exc:
        result["bifrost"] = {
            "reachable": False,
            "ready": False,
            "models": [],
            "model_count": 0,
            "error": _bifrost_error_message(exc, "Bifrost unavailable."),
        }

    # Databases
    try:
        registry = _load_registry()
        result["databases"] = [_database_summary(r) for r in registry.get("databases", [])]
    except HTTPException:
        pass

    # Containers
    known_containers = [
        LOCAL_DASHBOARD_BIFROST_CONTAINER,
        LOCAL_DASHBOARD_TAILSCALE_CONTAINER,
        "loreholm-local-dashboard",
        "loreholm-local-dashboard-endpoint",
    ]
    try:
        client = _get_docker_client()
        containers = []
        for name in known_containers:
            try:
                c = client.containers.get(name)
                containers.append({"name": name, "status": c.status})
            except DockerNotFound:
                containers.append({"name": name, "status": "not_found"})
        result["containers"] = containers
    except HTTPException as exc:
        result["containers"] = [{"error": _http_exception_message(exc)}]

    # Config files
    result["config_files"] = {
        "bifrost_config": LOCAL_DASHBOARD_BIFROST_CONFIG_FILE.exists(),
        "dashboard_token": LOCAL_DASHBOARD_TOKEN_FILE.exists(),
        "sync_token": LOCAL_SYNC_TOKEN_FILE.exists(),
        "api_key": LOCAL_API_KEY_FILE.exists(),
        "registry": LOCAL_DASHBOARD_REGISTRY_FILE.exists(),
    }

    return result
