from __future__ import annotations

from fastapi import APIRouter

from .agent import router as agent_router
from .auth import router as auth_router
from .chat import router as chat_router
from .databases import router as databases_router
from .home import home_router
from .reconciler import router as reconciler_router
from .sync import router as sync_router
from .wizard import router as wizard_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(wizard_router)
api_router.include_router(chat_router)
api_router.include_router(agent_router)
api_router.include_router(databases_router)
api_router.include_router(sync_router)
api_router.include_router(reconciler_router)

__all__ = ["api_router", "home_router"]
