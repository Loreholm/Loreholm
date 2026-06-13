from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from ..core.config import LOCAL_DASHBOARD_TAILSCALE_CONTAINER

try:
    import docker
    from docker.errors import DockerException
    from docker.errors import NotFound as DockerNotFound
except ImportError:  # pragma: no cover - optional in non-local-dashboard runtimes
    docker = None  # type: ignore[assignment]
    DockerException = Exception  # type: ignore[assignment]
    DockerNotFound = Exception  # type: ignore[assignment]


def _get_docker_client():
    if docker is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "DOCKER_UNAVAILABLE",
                    "message": "Docker SDK is not available in local dashboard runtime.",
                }
            },
        )
    try:
        client = docker.from_env()
        client.ping()
        return client
    except DockerException as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "DOCKER_UNAVAILABLE",
                    "message": f"Could not connect to Docker Engine: {exc}",
                }
            },
        ) from exc


def _ensure_tailscale_container_running(client: Any) -> None:
    try:
        tailscale = client.containers.get(LOCAL_DASHBOARD_TAILSCALE_CONTAINER)
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "TAILSCALE_CONTAINER_UNAVAILABLE",
                    "message": (
                        f"Required container '{LOCAL_DASHBOARD_TAILSCALE_CONTAINER}' "
                        "was not found."
                    ),
                }
            },
        ) from exc
    except DockerException as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "DOCKER_UNAVAILABLE",
                    "message": f"Could not inspect Tailscale container: {exc}",
                }
            },
        ) from exc

    try:
        tailscale.reload()
    except DockerException:
        pass
    status = (
        tailscale.attrs.get("State", {}).get("Status")
        if isinstance(getattr(tailscale, "attrs", None), dict)
        else None
    ) or getattr(tailscale, "status", None)
    if status != "running":
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "TAILSCALE_CONTAINER_UNAVAILABLE",
                    "message": (
                        f"Required container '{LOCAL_DASHBOARD_TAILSCALE_CONTAINER}' "
                        "is not running."
                    ),
                }
            },
        )


def _http_exception_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        error = detail.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = detail.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return f"HTTP {exc.status_code}"
