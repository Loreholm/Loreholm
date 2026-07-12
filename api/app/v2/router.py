from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status

from .models import CaptureBatch, CaptureBatchReceipt, InstancePolicy
from .service import CaptureService


router = APIRouter(prefix="/v2", tags=["V2 instance"])


def get_capture_service() -> CaptureService:
    raise RuntimeError("V2 capture service dependency is not configured")


def require_device_token(authorization: str | None = Header(default=None)) -> None:
    # Concrete credential validation is installed by the V2 application.
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="device credential required")


@router.get("/policy", response_model=InstancePolicy)
def read_policy(
    _: None = Depends(require_device_token),
    service: CaptureService = Depends(get_capture_service),
) -> InstancePolicy:
    return service.policy


@router.post("/captures", response_model=CaptureBatchReceipt, status_code=202)
def ingest_captures(
    batch: CaptureBatch,
    _: None = Depends(require_device_token),
    service: CaptureService = Depends(get_capture_service),
) -> CaptureBatchReceipt:
    return CaptureBatchReceipt(receipts=[service.ingest(capture) for capture in batch.captures])
