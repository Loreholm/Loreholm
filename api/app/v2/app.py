from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import httpx

from .models import (
    AdminStatus,
    CaptureEnvelope,
    ChatStreamRequest,
    ClaimView,
    EntityView,
    EvidenceView,
    ExtensionPromotion,
    ExtensionRelationView,
    ExtensionRevert,
    GroundedQueryRequest,
    GroundedQueryResponse,
    InstancePolicy,
    MaintenanceNoticeView,
    MiningRunView,
    ModelEndpointConfig,
    ReminingCreate,
    ReminingView,
    ResolvedMentionView,
)
from .claims import ClaimCommitter, extraction_schema_prompt, load_relation_schema
from .maintenance import KnowledgeMaintainer
from .mining import BifrostExtractionGateway, MiningWorker
from .resolution import BifrostResolutionGateway, EntityResolver
from .query import BifrostQueryGateway, GroundedQueryService
from .router import get_capture_service, require_device_token, router
from .salience import SalienceWorker
from .service import ArcadeCaptureStore, CaptureService


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    required = {
        "ARCADEDB_URL": os.getenv("ARCADEDB_URL", ""),
        "ARCADEDB_DATABASE": os.getenv("ARCADEDB_DATABASE", ""),
        "ARCADEDB_USERNAME": os.getenv("ARCADEDB_USERNAME", ""),
        "ARCADEDB_PASSWORD": os.getenv("ARCADEDB_PASSWORD", ""),
    }
    embedding_dimensions = max(2, int(os.getenv("LOREHOLM_V2_EMBEDDING_DIMENSIONS", "384")))
    store = ArcadeCaptureStore(
        required["ARCADEDB_URL"], required["ARCADEDB_DATABASE"],
        required["ARCADEDB_USERNAME"], required["ARCADEDB_PASSWORD"],
        embedding_dimensions=embedding_dimensions,
    ) if all(required.values()) else None
    session_quiescence_seconds = max(
        1,
        int(os.getenv("LOREHOLM_V2_SESSION_QUIESCENCE_SECONDS", "300")),
    )
    service = CaptureService(
        store,
        InstancePolicy(),
        session_quiescence=timedelta(seconds=session_quiescence_seconds),
    ) if store else None
    salience_poll_seconds = max(1, int(os.getenv("LOREHOLM_V2_SALIENCE_POLL_SECONDS", "5")))
    salience_worker = SalienceWorker(
        store,
        worker_id=f"salience-{os.getpid()}-{secrets.token_hex(4)}",
    ) if store else None
    configured_digest = os.getenv("LOREHOLM_V2_DEVICE_TOKEN_SHA256", "").strip().lower()
    admin_digest = os.getenv("LOREHOLM_V2_ADMIN_TOKEN_SHA256", "").strip().lower()
    sync_digest = os.getenv("LOREHOLM_V2_SYNC_TOKEN_SHA256", "").strip().lower()
    bifrost_url = os.getenv("BIFROST_URL", "http://bifrost:8080").rstrip("/")
    bifrost_dashboard_url = os.getenv("BIFROST_PUBLIC_URL", "http://127.0.0.1:8083").rstrip("/")
    bifrost_auth = (
        os.getenv("BIFROST_ADMIN_USERNAME", ""),
        os.getenv("BIFROST_ADMIN_PASSWORD", ""),
    )
    mining_poll_seconds = max(1, int(os.getenv("LOREHOLM_V2_MINING_POLL_SECONDS", "5")))
    relation_schema = load_relation_schema()
    extraction_gateway = BifrostExtractionGateway(
        bifrost_url, bifrost_auth, extraction_schema_prompt(relation_schema)
    )
    resolution_gateway = BifrostResolutionGateway(bifrost_url, bifrost_auth)
    resolver = EntityResolver(store, resolution_gateway) if store else None
    committer = ClaimCommitter(store, relation_schema) if store else None
    maintainer = KnowledgeMaintainer(store, relation_schema) if store else None

    def selected_endpoint() -> ModelEndpointConfig:
        stored = service.store.get_config("model_endpoint") if service is not None else None
        if stored is None:
            return ModelEndpointConfig(embedding_dimensions=embedding_dimensions)
        # Older endpoint records did not state whether inference crossed the
        # instance boundary. Treat that ambiguity as remote until the operator
        # saves an explicit choice in the field console.
        if "processing_location" not in stored:
            stored = {**stored, "processing_location": "remote"}
        if "embedding_dimensions" not in stored:
            stored = {**stored, "embedding_dimensions": embedding_dimensions}
        return ModelEndpointConfig.model_validate(stored)

    mining_worker = MiningWorker(
        store,
        extraction_gateway,
        worker_id=f"mining-{os.getpid()}-{secrets.token_hex(4)}",
        policy=lambda: service.policy,
        endpoint=selected_endpoint,
        resolver=resolver,
        committer=committer,
    ) if store else None
    query_service = GroundedQueryService(
        store,
        BifrostQueryGateway(bifrost_url, bifrost_auth),
        relation_schema,
        policy=lambda: service.policy,
        endpoint=selected_endpoint,
    ) if store else None

    def bifrost_request(method: str, path: str, **kwargs) -> httpx.Response:
        return httpx.request(method, f"{bifrost_url}{path}", auth=bifrost_auth, **kwargs)

    def require_sync(authorization: str | None = Header(default=None)) -> None:
        if not sync_digest:
            raise HTTPException(status_code=503, detail="cloud sync authentication is not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="sync credential required")
        supplied = hashlib.sha256(authorization[7:].encode()).hexdigest()
        if not hmac.compare_digest(supplied, sync_digest):
            raise HTTPException(status_code=401, detail="invalid sync credential")

    def uuid7() -> str:
        millis = int(time.time() * 1000)
        value = (millis & ((1 << 48) - 1)) << 80
        value |= 0x7 << 76
        value |= secrets.randbits(12) << 64
        value |= 0b10 << 62
        value |= secrets.randbits(62)
        raw = f"{value:032x}"
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"

    def capture_chat_message(conversation_id: str, role: str, content: str) -> None:
        envelope = CaptureEnvelope.model_validate({
            "capture_id": uuid7(),
            "kind": "event",
            "class": "transcript.message",
            "surface": "loreholm.browser-chat",
            "session_ref": conversation_id,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "payload": {"role": role, "content": content},
            "refs": [],
            "hints": [],
            "meta": {
                "contract_version": "2.0", "spine_version": "2.0.0",
                "adapter_id": "browser-chat", "adapter_version": "2.0.0",
                "device_id": "cloud-chat", "user_id": "instance-owner",
                "queue_age_seconds": 0, "policy_version": service.policy.version,
            },
        })
        service.ingest(envelope)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop_salience = asyncio.Event()
        salience_task: asyncio.Task | None = None
        mining_task: asyncio.Task | None = None
        if store is not None:
            store.bootstrap()
            store.backfill_mining_work()
            service.load_policy()
            async def run_salience() -> None:
                while not stop_salience.is_set():
                    try:
                        await asyncio.to_thread(salience_worker.process_once)
                    except Exception:
                        logger.exception("salience worker sweep failed")
                    try:
                        await asyncio.wait_for(stop_salience.wait(), timeout=salience_poll_seconds)
                    except TimeoutError:
                        pass

            async def run_mining() -> None:
                while not stop_salience.is_set():
                    try:
                        await asyncio.to_thread(mining_worker.process_once)
                    except Exception:
                        logger.exception("mining worker sweep failed")
                    try:
                        await asyncio.wait_for(stop_salience.wait(), timeout=mining_poll_seconds)
                    except TimeoutError:
                        pass

            salience_task = asyncio.create_task(run_salience(), name="loreholm-v2-salience")
            mining_task = asyncio.create_task(run_mining(), name="loreholm-v2-mining")
        try:
            yield
        finally:
            stop_salience.set()
            if salience_task is not None:
                await salience_task
            if mining_task is not None:
                await mining_task

    app = FastAPI(title="Loreholm Instance", version="1.0.0", lifespan=lifespan)

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

    @app.post("/v2/query", response_model=GroundedQueryResponse)
    def grounded_query(
        payload: GroundedQueryRequest,
        _: None = Depends(require_device_token),
    ) -> GroundedQueryResponse:
        if query_service is None:
            raise HTTPException(status_code=503, detail="ArcadeDB storage is not configured")
        try:
            return query_service.query(payload)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

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
            response = bifrost_request("GET", "/api/providers", timeout=5)
            response.raise_for_status()
            bifrost_ok = True
            providers = response.json().get("providers", [])
            selected = service.store.get_config("model_endpoint") or {"provider_name": "vllm-local"}
            provider = next((item for item in providers if item.get("name") == selected["provider_name"]), None)
            if provider:
                base_url = provider.get("network_config", {}).get("base_url")
                if base_url:
                    vllm_ok = httpx.get(f"{base_url.rstrip('/')}/v1/models", timeout=5).is_success
        except (httpx.HTTPError, ValueError):
            pass
        return AdminStatus(
            policy=service.policy,
            bifrost_ok=bifrost_ok,
            model_provider=provider,
            model_endpoint=selected_endpoint(),
            vllm_ok=vllm_ok,
            bifrost_dashboard_url=bifrost_dashboard_url,
        )

    @app.put("/v2/admin/policy", response_model=InstancePolicy)
    def update_policy(policy: InstancePolicy, _: None = Depends(require_admin)) -> InstancePolicy:
        return service.update_policy(policy)

    @app.get("/v2/admin/mining/runs", response_model=list[MiningRunView])
    def mining_runs(
        limit: int = 50, _: None = Depends(require_admin)
    ) -> list[MiningRunView]:
        bounded = max(1, min(limit, 250))
        return [MiningRunView.model_validate(run.__dict__) for run in service.store.list_mining_runs(
            limit=bounded
        )]

    @app.get("/v2/admin/resolution/mentions", response_model=list[ResolvedMentionView])
    def resolved_mentions(
        limit: int = 50, _: None = Depends(require_admin)
    ) -> list[ResolvedMentionView]:
        return [
            ResolvedMentionView.model_validate(mention.__dict__)
            for mention in service.store.list_resolved_mentions(limit=limit)
        ]

    @app.get("/v2/admin/resolution/entities", response_model=list[EntityView])
    def entities(limit: int = 50, _: None = Depends(require_admin)) -> list[EntityView]:
        return [
            EntityView.model_validate(entity.__dict__)
            for entity in service.store.list_entities(limit=limit)
        ]

    @app.get("/v2/admin/knowledge/schema")
    def knowledge_schema(_: None = Depends(require_admin)) -> dict:
        return {
            **relation_schema.model_dump(mode="json"),
            "extensions": [item.__dict__ for item in service.store.list_extension_relations(limit=250)],
        }

    @app.get("/v2/admin/knowledge/claims", response_model=list[ClaimView])
    def claims(limit: int = 50, _: None = Depends(require_admin)) -> list[ClaimView]:
        return [
            ClaimView.model_validate(claim.__dict__)
            for claim in service.store.list_claims(limit=limit)
        ]

    @app.get("/v2/admin/knowledge/evidence", response_model=list[EvidenceView])
    def evidence(limit: int = 50, _: None = Depends(require_admin)) -> list[EvidenceView]:
        return [
            EvidenceView.model_validate(item.__dict__)
            for item in service.store.list_evidence(limit=limit)
        ]

    @app.get("/v2/admin/maintenance/notices", response_model=list[MaintenanceNoticeView])
    def maintenance_notices(
        limit: int = 50, _: None = Depends(require_admin)
    ) -> list[MaintenanceNoticeView]:
        return [
            MaintenanceNoticeView.model_validate(item.__dict__)
            for item in service.store.list_maintenance_notices(limit=limit)
        ]

    @app.get("/v2/admin/maintenance/remining", response_model=list[ReminingView])
    def remining_requests(
        limit: int = 50, _: None = Depends(require_admin)
    ) -> list[ReminingView]:
        return [
            ReminingView.model_validate(item.__dict__)
            for item in service.store.list_remining_requests(limit=limit)
        ]

    @app.post("/v2/admin/maintenance/remining", response_model=ReminingView)
    def request_remining(
        payload: ReminingCreate, _: None = Depends(require_admin)
    ) -> ReminingView:
        try:
            request = maintainer.request_remining(payload.source_run_id, payload.reason)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ReminingView.model_validate(request.__dict__)

    @app.get("/v2/admin/knowledge/extensions", response_model=list[ExtensionRelationView])
    def extension_relations(
        limit: int = 100, _: None = Depends(require_admin)
    ) -> list[ExtensionRelationView]:
        return [
            ExtensionRelationView.model_validate(item.__dict__)
            for item in service.store.list_extension_relations(limit=limit)
        ]

    @app.post(
        "/v2/admin/knowledge/extensions/{relation}/promote",
        response_model=ExtensionRelationView,
    )
    def promote_extension(
        relation: str, payload: ExtensionPromotion, _: None = Depends(require_admin)
    ) -> ExtensionRelationView:
        try:
            item = maintainer.promote_extension(
                relation, payload.core_relation, payload.schema_commit
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ExtensionRelationView.model_validate(item.__dict__)

    @app.post(
        "/v2/admin/knowledge/extensions/{relation}/revert",
        response_model=ExtensionRelationView,
    )
    def revert_extension(
        relation: str, payload: ExtensionRevert, _: None = Depends(require_admin)
    ) -> ExtensionRelationView:
        try:
            item = maintainer.revert_extension(relation, payload.schema_commit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ExtensionRelationView.model_validate(item.__dict__)

    @app.put("/v2/admin/model")
    def configure_model(config: ModelEndpointConfig, _: None = Depends(require_admin)) -> dict:
        if config.embedding_dimensions != embedding_dimensions:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"embedding_dimensions must match the instance vector region "
                    f"({embedding_dimensions})"
                ),
            )
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
                "allowed_requests": {
                    "list_models": True,
                    "chat_completion": True,
                    "chat_completion_stream": True,
                    "embedding": config.provider_name == config.embedding_provider_name,
                },
            },
        }
        existing = bifrost_request("GET", "/api/providers", timeout=10).json().get("providers", [])
        if any(item.get("name") == config.provider_name for item in existing):
            response = bifrost_request(
                "PUT", f"/api/providers/{config.provider_name}", json=provider, timeout=15
            )
        else:
            response = bifrost_request(
                "POST", "/api/providers",
                json={"provider": config.provider_name, **provider}, timeout=15,
            )
        if response.is_error:
            raise HTTPException(
                status_code=502,
                detail=f"Bifrost rejected {config.provider_name}: {response.text[:500]}",
            )
        service.store.set_config("model_endpoint", config.model_dump(mode="json"))
        return {
            "ok": True,
            "provider": config.provider_name,
            "model": config.model_name,
            "embedding_provider": config.embedding_provider_name,
            "embedding_model": config.embedding_model_name,
        }

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatStreamRequest, _: None = Depends(require_sync)) -> StreamingResponse:
        selected = service.store.get_config("model_endpoint") or {
            "provider_name": "vllm-local", "model_name": "loreholm-local",
        }
        model = f"{selected['provider_name']}/{selected['model_name']}"
        user_message = next((message for message in reversed(payload.messages) if message.role == "user"), None)
        if user_message is None:
            raise HTTPException(status_code=422, detail="at least one user message is required")
        capture_chat_message(payload.conversation_id, "user", user_message.content)

        async def events():
            assistant_parts: list[str] = []
            reasoning_buffer = ""
            hiding_reasoning: bool | None = None
            visible_started = False
            timeout = httpx.Timeout(180.0, connect=5.0)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST", f"{bifrost_url}/v1/chat/completions",
                        auth=bifrost_auth,
                        json={
                            "model": model,
                            "messages": [message.model_dump() for message in payload.messages],
                            "stream": True,
                            "chat_template_kwargs": {"enable_thinking": False},
                        },
                    ) as response:
                        if response.is_error:
                            body = (await response.aread()).decode(errors="replace")[:500]
                            yield f"data: {json.dumps({'type': 'error', 'message': body})}\n\n"
                            return
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            raw = line[6:]
                            if raw == "[DONE]":
                                break
                            try:
                                chunk = json.loads(raw)
                                text = chunk.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                            except (ValueError, IndexError, AttributeError):
                                continue
                            if text:
                                if hiding_reasoning is None:
                                    hiding_reasoning = text.lstrip().startswith("<think>")
                                if hiding_reasoning:
                                    reasoning_buffer += text
                                    if "</think>" not in reasoning_buffer:
                                        continue
                                    text = reasoning_buffer.split("</think>", 1)[1].lstrip("\r\n")
                                    reasoning_buffer = ""
                                    hiding_reasoning = False
                                    if not text:
                                        continue
                                if not visible_started:
                                    text = text.lstrip()
                                    if not text:
                                        continue
                                    visible_started = True
                                assistant_parts.append(text)
                                yield f"data: {json.dumps({'type': 'content', 'content': text})}\n\n"
                assistant = "".join(assistant_parts)
                if assistant:
                    capture_chat_message(payload.conversation_id, "assistant", assistant)
                yield f"data: {json.dumps({'type': 'done', 'model': model})}\n\n"
            except httpx.HTTPError as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    def health() -> dict[str, str | bool]:
        return {"ok": service is not None, "version": "1.0.0", "storage": "arcadedb" if service else "unconfigured"}

    static_dir = Path(__file__).with_name("static")
    app.mount("/dashboard/assets", StaticFiles(directory=static_dir), name="dashboard-assets")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
