from __future__ import annotations

import hashlib
import hmac
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import httpx

from .models import AdminStatus, InstancePolicy, ModelEndpointConfig
from .router import get_capture_service, require_device_token, router
from .service import ArcadeCaptureStore, CaptureService


def create_app() -> FastAPI:
    required = {
        "ARCADEDB_URL": os.getenv("ARCADEDB_URL", ""),
        "ARCADEDB_DATABASE": os.getenv("ARCADEDB_DATABASE", ""),
        "ARCADEDB_USERNAME": os.getenv("ARCADEDB_USERNAME", ""),
        "ARCADEDB_PASSWORD": os.getenv("ARCADEDB_PASSWORD", ""),
    }
    store = ArcadeCaptureStore(
        required["ARCADEDB_URL"], required["ARCADEDB_DATABASE"],
        required["ARCADEDB_USERNAME"], required["ARCADEDB_PASSWORD"],
    ) if all(required.values()) else None
    service = CaptureService(store, InstancePolicy()) if store else None
    configured_digest = os.getenv("LOREHOLM_V2_DEVICE_TOKEN_SHA256", "").strip().lower()
    admin_digest = os.getenv("LOREHOLM_V2_ADMIN_TOKEN_SHA256", "").strip().lower()
    bifrost_url = os.getenv("BIFROST_URL", "http://bifrost:8080").rstrip("/")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if store is not None:
            store.bootstrap()
            service.load_policy()
        yield

    app = FastAPI(title="Loreholm V2 Instance", version="2.0.0", lifespan=lifespan)

    def service_dependency() -> CaptureService:
        if service is None:
            raise HTTPException(status_code=503, detail="ArcadeDB storage is not configured")
        return service

    def auth_dependency(authorization: str | None = Header(default=None)) -> None:
        if not configured_digest:
            raise HTTPException(status_code=503, detail="device authentication is not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="device credential required")
        supplied = hashlib.sha256(authorization[7:].encode()).hexdigest()
        if not hmac.compare_digest(supplied, configured_digest):
            raise HTTPException(status_code=401, detail="invalid device credential")

    app.dependency_overrides[get_capture_service] = service_dependency
    app.dependency_overrides[require_device_token] = auth_dependency
    app.include_router(router)

    def require_admin(authorization: str | None = Header(default=None)) -> None:
        if not admin_digest:
            raise HTTPException(status_code=503, detail="dashboard authentication is not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="admin credential required")
        supplied = hashlib.sha256(authorization[7:].encode()).hexdigest()
        if not hmac.compare_digest(supplied, admin_digest):
            raise HTTPException(status_code=401, detail="invalid admin credential")

    @app.get("/v2/admin/status", response_model=AdminStatus)
    def admin_status(_: None = Depends(require_admin)) -> AdminStatus:
        provider = None
        bifrost_ok = False
        vllm_ok = False
        try:
            response = httpx.get(f"{bifrost_url}/api/providers", timeout=5)
            response.raise_for_status()
            bifrost_ok = True
            providers = response.json().get("providers", [])
            provider = next((item for item in providers if item.get("name") == "vllm-local"), None)
            if provider:
                base_url = provider.get("network_config", {}).get("base_url")
                if base_url:
                    vllm_ok = httpx.get(f"{base_url.rstrip('/')}/v1/models", timeout=5).is_success
        except (httpx.HTTPError, ValueError):
            pass
        return AdminStatus(policy=service.policy, bifrost_ok=bifrost_ok, model_provider=provider, vllm_ok=vllm_ok)

    @app.put("/v2/admin/policy", response_model=InstancePolicy)
    def update_policy(policy: InstancePolicy, _: None = Depends(require_admin)) -> InstancePolicy:
        return service.update_policy(policy)

    @app.put("/v2/admin/model")
    def configure_model(config: ModelEndpointConfig, _: None = Depends(require_admin)) -> dict:
        provider = {
            "network_config": {
                "base_url": config.base_url,
                "default_request_timeout_in_seconds": 120,
                "allow_private_network": config.allow_private_network,
            },
            "concurrency_and_buffer_size": {"concurrency": 4, "buffer_size": 16},
            "send_back_raw_request": False,
            "send_back_raw_response": False,
            "store_raw_request_response": False,
            "custom_provider_config": {
                "is_key_less": True,
                "base_provider_type": "openai",
                "allowed_requests": {"list_models": True, "chat_completion": True, "chat_completion_stream": True},
            },
        }
        existing = httpx.get(f"{bifrost_url}/api/providers", timeout=10).json().get("providers", [])
        if any(item.get("name") == config.provider_name for item in existing):
            response = httpx.put(f"{bifrost_url}/api/providers/{config.provider_name}", json=provider, timeout=15)
        else:
            response = httpx.post(f"{bifrost_url}/api/providers", json={"provider": config.provider_name, **provider}, timeout=15)
        if response.is_error:
            raise HTTPException(status_code=502, detail=f"Bifrost rejected model configuration: {response.text[:500]}")
        return {"ok": True, "provider": config.provider_name, "model": config.model_name}

    @app.get("/health")
    def health() -> dict[str, str | bool]:
        return {"ok": service is not None, "version": "2.0.0", "storage": "arcadedb" if service else "unconfigured"}

    static_dir = Path(__file__).with_name("static")
    app.mount("/dashboard/assets", StaticFiles(directory=static_dir), name="dashboard-assets")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
