from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
import os
from pathlib import Path

from app.mcp.router import router as mcp_router
from app.mcp.mcp_server import mcp_router as mcp_jsonrpc_router
from app.onboarding.router import router as onboarding_router
from app.api_keys.router import router as api_keys_router
from app.database_targets.router import router as database_targets_router
from app.chat.router import router as chat_router
from app.llm.router import router as llm_router
from app.services.redis_client import close_redis


def _is_dev_environment() -> bool:
    env = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    return env in {"dev", "development", "local", "test"}


def _debug_routes_enabled() -> bool:
    explicit = os.getenv("ENABLE_DEBUG_ENDPOINTS")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return _is_dev_environment()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    # Startup
    yield
    # Shutdown: close Redis connection
    await close_redis()


# Main app - internal endpoints hidden from docs
app = FastAPI(
    title="loreholm API",
    description="Memory layer for LLM conversations via Model Context Protocol (MCP)",
    version="1.0.0",
    docs_url=None,  # Disable default docs
    redoc_url=None,  # Disable default redoc
    lifespan=lifespan,
)

# Path to install script (can be overridden by env var)
INSTALL_SCRIPT_PATH = os.getenv(
    "INSTALL_SCRIPT_PATH",
    str(Path(__file__).parent.parent.parent.parent / "web" / "install.sh")
)
INSTALL_LEGACY_SCRIPT_PATH = os.getenv(
    "INSTALL_LEGACY_SCRIPT_PATH",
    str(Path(__file__).parent.parent.parent.parent / "web" / "install-legacy.sh")
)
INSTALL_PS1_SCRIPT_PATH = os.getenv(
    "INSTALL_PS1_SCRIPT_PATH",
    str(Path(__file__).parent.parent.parent.parent / "web" / "install.ps1")
)
INSTALL_LEGACY_PS1_SCRIPT_PATH = os.getenv(
    "INSTALL_LEGACY_PS1_SCRIPT_PATH",
    str(Path(__file__).parent.parent.parent.parent / "web" / "install-legacy.ps1")
)
UPDATE_SCRIPT_PATH = os.getenv(
    "UPDATE_SCRIPT_PATH",
    str(Path(__file__).parent.parent.parent.parent / "web" / "update.sh")
)
UPDATE_LEGACY_SCRIPT_PATH = os.getenv(
    "UPDATE_LEGACY_SCRIPT_PATH",
    str(Path(__file__).parent.parent.parent.parent / "web" / "update-legacy.sh")
)
UPDATE_PS1_SCRIPT_PATH = os.getenv(
    "UPDATE_PS1_SCRIPT_PATH",
    str(Path(__file__).parent.parent.parent.parent / "web" / "update.ps1")
)
UPDATE_LEGACY_PS1_SCRIPT_PATH = os.getenv(
    "UPDATE_LEGACY_PS1_SCRIPT_PATH",
    str(Path(__file__).parent.parent.parent.parent / "web" / "update-legacy.ps1")
)
UNINSTALL_SCRIPT_PATH = os.getenv(
    "UNINSTALL_SCRIPT_PATH",
    str(Path(__file__).parent.parent.parent.parent / "web" / "uninstall.sh")
)
UNINSTALL_PS1_SCRIPT_PATH = os.getenv(
    "UNINSTALL_PS1_SCRIPT_PATH",
    str(Path(__file__).parent.parent.parent.parent / "web" / "uninstall.ps1")
)

# Add CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://loreholm.com",
        "https://www.loreholm.com",
        "https://api.loreholm.com",
        "https://chat.loreholm.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include MCP router with tag for filtering
app.include_router(mcp_router, tags=["MCP Tools"])

# Include MCP JSON-RPC server (official MCP protocol)
app.include_router(mcp_jsonrpc_router, tags=["MCP Protocol"])

# Include onboarding router - hidden from public docs
app.include_router(onboarding_router, include_in_schema=False)

# Include API keys router - hidden from public docs (dashboard only)
app.include_router(api_keys_router, include_in_schema=False)

# Include database targets router - hidden from public docs (dashboard only)
app.include_router(database_targets_router, include_in_schema=False)

# Include LLM proxy router - hidden from public docs (chat app)
app.include_router(llm_router, include_in_schema=False)

# Include chat proxy router - hidden from public docs (chat.loreholm.com)
app.include_router(chat_router, include_in_schema=False)


# Custom OpenAPI schema that only includes MCP endpoints
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title="loreholm MCP API",
        version="1.0.0",
        description="""
## Memory Layer for LLM Conversations

loreholm provides structured memory storage for your LLM applications via the Model Context Protocol (MCP).

### Authentication
All endpoints require authentication via one of:

**API Key** (recommended for MCP clients):
```
X-API-Key: <your-api-key>
```

**JWT Token** (for dashboard/web access):
```
Authorization: Bearer <your-jwt-token>
```

Create API keys from your dashboard at loreholm.com

### Usage
These tools are designed to be called by LLM applications to store and retrieve memories about entities, projects, and conversations.
        """,
        routes=app.routes,
    )
    
    # Filter to only MCP paths
    filtered_paths = {}
    for path, path_item in openapi_schema.get("paths", {}).items():
        if path.startswith("/mcp"):
            filtered_paths[path] = path_item
    
    openapi_schema["paths"] = filtered_paths
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


# Mount Swagger UI manually at /docs
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="loreholm MCP API",
    )


@app.get("/redoc", include_in_schema=False)
async def custom_redoc():
    return get_redoc_html(
        openapi_url="/openapi.json",
        title="loreholm MCP API",
    )


@app.get("/openapi.json", include_in_schema=False)
async def get_openapi_json():
    return app.openapi()


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"ok": True}


@app.get("/routes", include_in_schema=False)
def list_routes() -> dict:
    """Debug endpoint to list all registered routes."""
    if not _debug_routes_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            routes.append({"path": route.path, "methods": list(route.methods)})
    return {"routes": routes}


@app.get("/", response_class=PlainTextResponse, include_in_schema=False)
async def root() -> str:
    return "mcp-api: ok\n"


@app.get("/install.sh", response_class=PlainTextResponse, include_in_schema=False)
def get_install_script() -> Response:
    """Serve the BYODB install script."""
    # Try to read from file system first
    if os.path.exists(INSTALL_SCRIPT_PATH):
        with open(INSTALL_SCRIPT_PATH, "r") as f:
            script_content = f.read()
    else:
        # Fallback: embedded minimal script that redirects
        script_content = """#!/usr/bin/env bash
echo "Error: Install script not found. Please download from https://loreholm.com/install.sh"
exit 1
"""
    
    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": "inline; filename=install.sh",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        }
    )


@app.get("/install.ps1", response_class=PlainTextResponse, include_in_schema=False)
def get_install_ps1_script() -> Response:
    """Serve the BYODB install script for Windows."""
    if os.path.exists(INSTALL_PS1_SCRIPT_PATH):
        with open(INSTALL_PS1_SCRIPT_PATH, "r") as f:
            script_content = f.read()
    else:
        script_content = """Write-Host \"Error: Install script not found. Please download from https://loreholm.com/install.ps1\"
exit 1
"""

    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": "inline; filename=install.ps1",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/install-legacy.sh", response_class=PlainTextResponse, include_in_schema=False)
def get_install_legacy_script() -> Response:
    """Serve the legacy BYODB install script."""
    if os.path.exists(INSTALL_LEGACY_SCRIPT_PATH):
        with open(INSTALL_LEGACY_SCRIPT_PATH, "r") as f:
            script_content = f.read()
    else:
        script_content = """#!/usr/bin/env bash
echo "Error: Legacy install script not found. Please download from https://loreholm.com/install-legacy.sh"
exit 1
"""

    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": "inline; filename=install-legacy.sh",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/install-legacy.ps1", response_class=PlainTextResponse, include_in_schema=False)
def get_install_legacy_ps1_script() -> Response:
    """Serve the legacy BYODB install script for Windows."""
    if os.path.exists(INSTALL_LEGACY_PS1_SCRIPT_PATH):
        with open(INSTALL_LEGACY_PS1_SCRIPT_PATH, "r") as f:
            script_content = f.read()
    else:
        script_content = """Write-Host \"Error: Legacy install script not found. Please download from https://loreholm.com/install-legacy.ps1\"
exit 1
"""

    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": "inline; filename=install-legacy.ps1",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/update.sh", response_class=PlainTextResponse, include_in_schema=False)
def get_update_script() -> Response:
    """Serve the BYODB update script."""
    if os.path.exists(UPDATE_SCRIPT_PATH):
        with open(UPDATE_SCRIPT_PATH, "r") as f:
            script_content = f.read()
    else:
        script_content = """#!/usr/bin/env bash
echo "Error: Update script not found. Please download from https://loreholm.com/update.sh"
exit 1
"""

    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": "inline; filename=update.sh",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/update.ps1", response_class=PlainTextResponse, include_in_schema=False)
def get_update_ps1_script() -> Response:
    """Serve the BYODB update script for Windows."""
    if os.path.exists(UPDATE_PS1_SCRIPT_PATH):
        with open(UPDATE_PS1_SCRIPT_PATH, "r") as f:
            script_content = f.read()
    else:
        script_content = """Write-Host \"Error: Update script not found. Please download from https://loreholm.com/update.ps1\"
exit 1
"""

    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": "inline; filename=update.ps1",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/update-legacy.sh", response_class=PlainTextResponse, include_in_schema=False)
def get_update_legacy_script() -> Response:
    """Serve the legacy BYODB update script."""
    if os.path.exists(UPDATE_LEGACY_SCRIPT_PATH):
        with open(UPDATE_LEGACY_SCRIPT_PATH, "r") as f:
            script_content = f.read()
    else:
        script_content = """#!/usr/bin/env bash
echo "Error: Legacy update script not found. Please download from https://loreholm.com/update-legacy.sh"
exit 1
"""

    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": "inline; filename=update-legacy.sh",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/update-legacy.ps1", response_class=PlainTextResponse, include_in_schema=False)
def get_update_legacy_ps1_script() -> Response:
    """Serve the legacy BYODB update script for Windows."""
    if os.path.exists(UPDATE_LEGACY_PS1_SCRIPT_PATH):
        with open(UPDATE_LEGACY_PS1_SCRIPT_PATH, "r") as f:
            script_content = f.read()
    else:
        script_content = """Write-Host \"Error: Legacy update script not found. Please download from https://loreholm.com/update-legacy.ps1\"
exit 1
"""

    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": "inline; filename=update-legacy.ps1",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/uninstall.sh", response_class=PlainTextResponse, include_in_schema=False)
def get_uninstall_script() -> Response:
    """Serve the BYODB uninstall script."""
    if os.path.exists(UNINSTALL_SCRIPT_PATH):
        with open(UNINSTALL_SCRIPT_PATH, "r") as f:
            script_content = f.read()
    else:
        script_content = """#!/usr/bin/env bash
echo "Error: Uninstall script not found. Please download from https://loreholm.com/uninstall.sh"
exit 1
"""

    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": "inline; filename=uninstall.sh",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/uninstall.ps1", response_class=PlainTextResponse, include_in_schema=False)
def get_uninstall_ps1_script() -> Response:
    """Serve the BYODB uninstall script for Windows."""
    if os.path.exists(UNINSTALL_PS1_SCRIPT_PATH):
        with open(UNINSTALL_PS1_SCRIPT_PATH, "r") as f:
            script_content = f.read()
    else:
        script_content = """Write-Host \"Error: Uninstall script not found. Please download from https://loreholm.com/uninstall.ps1\"
exit 1
"""

    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": "inline; filename=uninstall.ps1",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )
