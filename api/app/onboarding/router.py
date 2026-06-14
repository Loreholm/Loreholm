from __future__ import annotations

import ipaddress
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
import httpx
import jwt
from jwt import PyJWK, PyJWTError

from app.onboarding.schemas import (
    OnboardingStatus,
    InitializeRequest,
    InitializeResponse,
    NodeSummary,
    NodesListResponse,
    CreateNodePreauthRequest,
    CreateNodePreauthResponse,
    RenameNodeRequest,
)
from app.services import verify_user_connection, get_user_tailscale_ip, user_id_to_namespace
from app.services.sync_auth import (
    SyncAuthNotConfiguredError,
    derive_user_sync_token,
)


router = APIRouter(prefix="/onboarding")

# In-memory store for MVP (replace with proper DB later)
_user_data: dict[str, dict] = {}

# Free-tier node cap. Sourced via _get_user_node_cap so this is the single
# seam to swap when tier-from-claims lands.
FREE_TIER_NODE_CAP = 3

# Headscale node names must be alphanumeric + hyphen, 1-63 chars (DNS label
# rules). Allow lowercase letters, digits, and hyphens; no leading/trailing
# hyphen. We also enforce a sane max length on the user-provided name.
_NODE_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

# Cache JWKS to avoid fetching on every request
_jwks_cache: dict = {"jwks": None, "expires": None}
# Cache the OIDC discovery document (keyed by issuer) the same way.
_oidc_discovery_cache: dict = {"issuer": None, "doc": None, "expires": None}


def _normalize_issuer(raw: str) -> str:
    """Return the issuer as an absolute URL without a trailing slash.

    Operators may set OIDC_ISSUER with or without a scheme; the discovery
    URL and the value we validate against are built from this.
    """
    value = (raw or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


LOCAL_DASHBOARD_RESOLVER_PORT = _env_int("LOCAL_DASHBOARD_RESOLVER_PORT", 8081)
LOCAL_DASHBOARD_RESOLVER_PATH = os.getenv("LOCAL_DASHBOARD_RESOLVER_PATH", "/local-dashboard.json")
LOCAL_DASHBOARD_IMAGE = os.getenv(
    "LOCAL_DASHBOARD_IMAGE",
    "ghcr.io/loreholm/mcp-local-dashboard:latest",
)
LOCAL_DASHBOARD_BIFROST_IMAGE = os.getenv(
    "LOCAL_DASHBOARD_BIFROST_IMAGE",
    os.getenv("BIFROST_IMAGE", "maximhq/bifrost:latest"),
)
def get_oidc_config() -> dict:
    """Get provider-neutral OIDC configuration from environment.

    Only three values are required — OIDC_ISSUER, OIDC_CLIENT_ID and
    OIDC_AUDIENCE. The issuer is an absolute URL used for discovery; the JWKS
    URL and canonical issuer string are resolved at verification time from the
    discovery document, never hardcoded.

    Two optional knobs exist for less common providers and are not normally
    set: OIDC_FRONTEND_AUDIENCE (when the browser must request a different
    audience than the API) and OIDC_AUDIENCE_CLAIM (when the API lives in a
    non-standard claim such as `azp`).
    """
    def _env(name: str) -> str:
        return (os.getenv(name, "") or "").strip()

    issuer = _normalize_issuer(_env("OIDC_ISSUER"))
    audience = _env("OIDC_AUDIENCE")
    audiences = [audience] if audience else []

    explicit_frontend_audience = _env("OIDC_FRONTEND_AUDIENCE")
    frontend_audience = explicit_frontend_audience or audience

    # Which claim carries the audience varies by provider: most use `aud`,
    # but some put the API/client in `azp`. Default to `aud`.
    audience_claim = _env("OIDC_AUDIENCE_CLAIM") or "aud"

    return {
        "issuer": issuer,
        "client_id": _env("OIDC_CLIENT_ID"),
        "audiences": audiences,
        "audience_claim": audience_claim,
        "frontend_audience": frontend_audience,
        "frontend_audience_explicit": bool(explicit_frontend_audience),
        "algorithms": ["RS256"],
    }


async def get_oidc_discovery(issuer: str) -> dict:
    """Fetch and cache the provider's OIDC discovery document.

    Given the configured issuer, returns the parsed
    ``/.well-known/openid-configuration`` document. The document's own
    ``issuer`` field is validated against the configured issuer to catch
    misconfiguration or a spoofed discovery endpoint.
    """
    issuer = _normalize_issuer(issuer)
    if not issuer:
        raise HTTPException(status_code=503, detail="OIDC issuer is not configured.")

    now = datetime.now(timezone.utc)
    cache = _oidc_discovery_cache
    if (
        cache["doc"]
        and cache["issuer"] == issuer
        and cache["expires"]
        and now < cache["expires"]
    ):
        return cache["doc"]

    discovery_url = f"{issuer}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            response = await client.get(discovery_url)
            response.raise_for_status()
            doc = response.json()
    except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        if cache["doc"] and cache["issuer"] == issuer:
            print(f"[auth] OIDC discovery fetch failed, using stale cache: {exc}")
            return cache["doc"]
        raise HTTPException(status_code=503, detail=f"Auth service unavailable: {exc}")

    doc_issuer = str(doc.get("issuer", "")).strip()
    if doc_issuer.rstrip("/") != issuer.rstrip("/"):
        raise HTTPException(
            status_code=503,
            detail=(
                "OIDC discovery issuer mismatch: document advertises "
                f"'{doc_issuer}' but OIDC_ISSUER is '{issuer}'."
            ),
        )
    if not doc.get("jwks_uri"):
        raise HTTPException(
            status_code=503,
            detail="OIDC discovery document is missing 'jwks_uri'.",
        )

    cache["issuer"] = issuer
    cache["doc"] = doc
    cache["expires"] = now + timedelta(hours=1)
    return doc


def _is_dev_environment() -> bool:
    env = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    return env in {"dev", "development", "local", "test"}


def _debug_routes_enabled() -> bool:
    explicit = os.getenv("ENABLE_DEBUG_ENDPOINTS")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return _is_dev_environment()


def _normalize_public_api_host(raw_host: str, *, prefer_https: bool) -> str:
    value = (raw_host or "").strip().rstrip("/")
    if not value:
        return ""

    if value.startswith("https://"):
        return value
    if value.startswith("http://"):
        if prefer_https:
            return "https://" + value[len("http://"):]
        return value

    scheme = "https" if prefer_https else "http"
    return f"{scheme}://{value}"


def _decode_unverified_dev_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        sub = str(payload.get("sub", "")).strip()
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid token: missing 'sub' claim")
        return {
            "sub": sub,
            "email": payload.get("email", sub),
        }
    except HTTPException:
        raise
    except PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc


async def get_current_user(request: Request) -> dict:
    """Validate JWT and extract user info."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    token = auth_header.removeprefix("Bearer ").strip()
    config = get_oidc_config()

    # Skip signature validation only in explicit dev environments when OIDC config is missing.
    if not config["issuer"]:
        if not _is_dev_environment():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "AUTH_NOT_CONFIGURED",
                        "message": "OIDC_ISSUER is required in non-dev environments.",
                    }
                },
            )
        return _decode_unverified_dev_token(token)

    if not config["audiences"]:
        if not _is_dev_environment():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "AUTH_NOT_CONFIGURED",
                        "message": "OIDC_AUDIENCE is required in non-dev environments.",
                    }
                },
            )
        return _decode_unverified_dev_token(token)

    try:
        # Resolve JWKS URL and canonical issuer from the discovery document
        # rather than constructing provider-specific URLs.
        discovery = await get_oidc_discovery(config["issuer"])
        jwks_url = discovery["jwks_uri"]
        expected_issuer = str(discovery.get("issuer", config["issuer"]))
        now = datetime.now(timezone.utc)

        # Use cached JWKS if available and not expired (cache for 1 hour)
        if _jwks_cache["jwks"] and _jwks_cache["expires"] and now < _jwks_cache["expires"]:
            jwks = _jwks_cache["jwks"]
        else:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                    jwks_response = await client.get(jwks_url)
                    jwks_response.raise_for_status()
                    jwks = jwks_response.json()
                    # Cache for 1 hour
                    _jwks_cache["jwks"] = jwks
                    _jwks_cache["expires"] = now + timedelta(hours=1)
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                # If we have stale cache, use it rather than failing
                if _jwks_cache["jwks"]:
                    print(f"[auth] JWKS fetch failed, using stale cache: {e}")
                    jwks = _jwks_cache["jwks"]
                else:
                    raise HTTPException(status_code=503, detail=f"Auth service unavailable: {e}")
        
        # Decode and verify token
        unverified_header = jwt.get_unverified_header(token)
        rsa_key = {}
        for key in jwks["keys"]:
            if key["kid"] == unverified_header["kid"]:
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"],
                }
                break
        
        if not rsa_key:
            raise HTTPException(status_code=401, detail="Invalid token key")
        
        signing_key = PyJWK.from_dict(rsa_key).key
        audience_claim = config["audience_claim"]
        payload = None
        last_error = None
        if audience_claim == "aud":
            # Standard path: let PyJWT validate the `aud` claim directly.
            for audience in config["audiences"]:
                try:
                    payload = jwt.decode(
                        token,
                        signing_key,
                        algorithms=config["algorithms"],
                        audience=audience,
                        issuer=expected_issuer,
                    )
                    break
                except PyJWTError as exc:
                    last_error = exc
        else:
            # Some providers carry the audience in a non-standard claim
            # (e.g. `azp`). Verify signature/issuer, then check it ourselves.
            try:
                decoded = jwt.decode(
                    token,
                    signing_key,
                    algorithms=config["algorithms"],
                    issuer=expected_issuer,
                    options={"verify_aud": False},
                )
                if str(decoded.get(audience_claim, "")) in config["audiences"]:
                    payload = decoded
                else:
                    last_error = PyJWTError(
                        f"token '{audience_claim}' claim does not match an accepted audience"
                    )
            except PyJWTError as exc:
                last_error = exc

        if payload is None:
            if last_error is not None:
                raise last_error
            raise PyJWTError("token audience validation failed")

        return {
            "sub": payload["sub"],
            "email": payload.get("email", payload.get("sub")),
        }
    except PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


@router.get("/auth/config")
async def get_public_auth_config(request: Request) -> dict:
    """Return runtime auth config for the web app (no secrets)."""
    config = get_oidc_config()
    missing = []
    if not config["issuer"]:
        missing.append("OIDC_ISSUER")
    if not config["client_id"]:
        missing.append("OIDC_CLIENT_ID")
    if not config["frontend_audience"]:
        missing.append("OIDC_AUDIENCE or OIDC_FRONTEND_AUDIENCE")

    if missing and not _is_dev_environment():
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "AUTH_NOT_CONFIGURED",
                    "message": f"Missing required auth settings: {', '.join(missing)}",
                }
            },
        )

    prefer_https = not _is_dev_environment()
    base_url = _normalize_public_api_host(
        os.getenv("PUBLIC_API_HOST", ""),
        prefer_https=prefer_https,
    )
    if not base_url:
        base_url = str(request.base_url).rstrip("/")
        if prefer_https and base_url.startswith("http://"):
            base_url = "https://" + base_url[len("http://"):]
    return {
        "oidc": {
            "issuer": config["issuer"],
            "clientId": config["client_id"],
            "audience": config["frontend_audience"],
            "frontendAudienceExplicit": config["frontend_audience_explicit"],
            "redirectUri": f"{base_url}/dashboard",
            "scope": "openid profile email",
        },
        "api": {"baseUrl": base_url},
    }


def get_headscale_config() -> dict:
    """Get Headscale configuration from environment."""
    return {
        "api_url": os.getenv("HEADSCALE_API_URL", "http://headscale:8080"),
        "api_key": os.getenv("HEADSCALE_API_KEY", ""),
    }


async def create_headscale_preauth_key(user_id: str) -> tuple[str, datetime]:
    """Create a pre-auth key in Headscale for the user."""
    config = get_headscale_config()
    
    # If no API key configured, return mock data for development
    if not config["api_key"]:
        mock_key = f"mock-preauth-key-{user_id[:8]}"
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        return mock_key, expires
    
    # Create user namespace if it doesn't exist - use same function as user_store
    namespace = user_id_to_namespace(user_id)
    
    # For internal Docker network communication, we may need to skip SSL verification
    # since the cert is for the public domain, not the internal container hostname
    api_url = config["api_url"]
    verify_ssl = not api_url.startswith("https://headscale:")  # Skip for internal Docker
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        headers = {"Authorization": f"Bearer {config['api_key']}"}
        
        # Try to create user (namespace) - ignore if already exists
        user_response = await client.post(
            f"{config['api_url']}/api/v1/user",
            headers=headers,
            json={"name": namespace},
        )
        
        # 200 = created, 409 = already exists (both are fine)
        if user_response.status_code not in (200, 409):
            print(f"[headscale] Unexpected response creating user {namespace}: {user_response.status_code}")
            # Continue anyway - user might still exist and we can create pre-auth keys
        
        # Create pre-auth key
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        response = await client.post(
            f"{config['api_url']}/api/v1/preauthkey",
            headers=headers,
            json={
                "user": namespace,
                "reusable": False,
                "ephemeral": False,
                "expiration": expires.isoformat(),
            },
        )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to create pre-auth key: {response.text}"
            )
        
        data = response.json()
        return data["preAuthKey"]["key"], expires


def generate_install_command(
    pre_auth_key: str,
    user_id: str,
    node_name: Optional[str] = None,
) -> str:
    """Generate the install command for the user.

    The `--sync-token` argument is derived per-user from the fleet-wide
    `LOCAL_SYNC_SIGNING_SECRET` + the user's OIDC sub, so every install
    receives a distinct bearer token. The local dashboard stores the
    derived value in `local-sync.token` and compares incoming sync
    requests against it byte-for-byte. See `app/services/sync_auth.py`.

    If the signing secret is not configured on this deployment, the
    command is emitted without `--sync-token` and the installer falls
    back to generating its own random local-only token — which means
    cloud→local sync features (discovery, atomic key-create sync, MCP
    query proxy) will 401 until the operator configures the secret and
    the user re-runs the install command.

    `node_name` is appended as `--name <name>` so the Tailscale client
    registers with the user-chosen device name on first connect.
    """
    public_host = os.getenv("PUBLIC_API_HOST", "http://localhost:8080")
    command = f"curl -fsSL {public_host}/install.sh | bash -s -- --key {pre_auth_key}"
    try:
        sync_token = derive_user_sync_token(user_id)
    except (SyncAuthNotConfiguredError, ValueError):
        sync_token = None
    if sync_token:
        command = f"{command} --sync-token {sync_token}"
    if node_name:
        command = f"{command} --name {node_name}"
    return command


def _get_user_node_cap(user: dict) -> tuple[int, str]:
    """Return (cap, tier_label) for the given user.

    Free tier today; later this will inspect `user` claims (org/plan).
    Centralized so there's one place to swap when tier-from-claims lands.
    """
    return FREE_TIER_NODE_CAP, "free"


def _validate_node_name(name: str) -> str:
    """Lowercase + validate a user-supplied device name. Raises 400 on bad input."""
    candidate = (name or "").strip().lower()
    if not _NODE_NAME_RE.match(candidate):
        raise HTTPException(
            status_code=400,
            detail=(
                "Device name must be 1-63 lowercase letters, digits, or "
                "hyphens, and cannot start or end with a hyphen."
            ),
        )
    return candidate


async def _list_user_nodes_from_headscale(user_id: str) -> list[dict]:
    """Return Headscale's view of the user's nodes (raw JSON dicts).

    Filtered to the requesting user's namespace. Returns [] in dev mode
    (no API key configured) and raises HTTPException(503) on Headscale
    transport failure so callers can surface a clean error.
    """
    config = get_headscale_config()
    if not config["api_key"]:
        return []

    namespace = user_id_to_namespace(user_id)
    api_url = config["api_url"]
    verify_ssl = not api_url.startswith("https://headscale:")

    try:
        async with httpx.AsyncClient(timeout=5.0, verify=verify_ssl) as client:
            response = await client.get(
                f"{api_url}/api/v1/node",
                headers={"Authorization": f"Bearer {config['api_key']}"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Headscale unreachable: {exc}",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Headscale API error {response.status_code}: {response.text[:200]}",
        )

    nodes = response.json().get("nodes", []) or []
    return [
        n for n in nodes
        if (n.get("user") or {}).get("name") == namespace
    ]


def _node_to_summary(node: dict) -> NodeSummary:
    """Project a Headscale node JSON into the NodeSummary shape the UI consumes."""
    last_seen_raw = node.get("lastSeen") or node.get("last_seen")
    last_seen: Optional[datetime] = None
    if last_seen_raw:
        try:
            last_seen = datetime.fromisoformat(str(last_seen_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            last_seen = None

    tailscale_ip: Optional[str] = None
    for ip in node.get("ipAddresses") or []:
        if isinstance(ip, str) and ip.startswith("100."):
            tailscale_ip = ip
            break

    return NodeSummary(
        id=str(node.get("id") or ""),
        name=str(node.get("name") or node.get("givenName") or ""),
        online=bool(node.get("online")),
        last_seen=last_seen,
        tailscale_ip=tailscale_ip,
    )


async def _get_user_node_or_404(user_id: str, node_id: str) -> dict:
    """Fetch a node by id and verify it belongs to the requesting user."""
    nodes = await _list_user_nodes_from_headscale(user_id)
    for node in nodes:
        if str(node.get("id") or "") == str(node_id):
            return node
    # 404 vs 403: returning 404 in either case avoids leaking whether the
    # node exists under a different user.
    raise HTTPException(status_code=404, detail="Device not found.")


def _is_valid_private_ipv4(value: str, *, allow_loopback: bool = False) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False

    if not isinstance(ip, ipaddress.IPv4Address):
        return False

    if ip.is_loopback:
        return allow_loopback

    if ip.is_multicast or ip.is_unspecified:
        return False

    return ip.is_private or ip.is_link_local


def _parse_dashboard_port(value: object, default: int = 3000) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default

    if 1 <= port <= 65535:
        return port

    return default


@router.get("/status", response_model=OnboardingStatus)
async def get_onboarding_status(user: dict = Depends(get_current_user)) -> OnboardingStatus:
    """Get the current onboarding status for the authenticated user.
    
    This is a FAST endpoint that returns cached status only.
    Use /node-status to check if node is registered in Headscale.
    Use /connection to actively check database connectivity.
    """
    user_id = user["sub"]
    
    # Return cached data only - no external API calls
    if user_id not in _user_data:
        raise HTTPException(status_code=404, detail="Not initialized")
    
    data = _user_data[user_id]
    
    # Generate update command if node is connected
    update_command = None
    if data.get("node_connected"):
        public_host = os.getenv("PUBLIC_API_HOST", "http://localhost:8080")
        update_command = f"curl -fsSL {public_host}/update.sh | bash"
    
    return OnboardingStatus(
        user_id=user_id,
        email=user.get("email", ""),
        initialized=True,
        node_name=data.get("node_name"),
        node_count=data.get("node_count", 0),
        last_seen=data.get("last_seen"),
        status=data.get("status", "pending"),
        install_command=data.get("install_command"),
        node_connected=data.get("node_connected", False),
        database_connected=data.get("database_connected", False),
        update_command=update_command,
    )


@router.post("/initialize", response_model=InitializeResponse)
async def initialize_onboarding(
    request: Optional[InitializeRequest] = None,
    user: dict = Depends(get_current_user),
) -> InitializeResponse:
    """Initialize onboarding for a new user - creates Headscale pre-auth key."""
    user_id = user["sub"]
    
    # Create pre-auth key
    pre_auth_key, expires = await create_headscale_preauth_key(user_id)
    install_command = generate_install_command(pre_auth_key, user_id)
    
    # Store user data
    _user_data[user_id] = {
        "email": user.get("email"),
        "node_name": request.node_name if request else None,
        "pre_auth_key": pre_auth_key,
        "install_command": install_command,
        "expires_at": expires,
        "created_at": datetime.now(timezone.utc),
        "status": "pending",
        "node_count": 0,
        "node_connected": False,
        "database_connected": False,
    }
    
    return InitializeResponse(
        user_id=user_id,
        pre_auth_key=pre_auth_key,
        install_command=install_command,
        expires_at=expires,
    )


@router.get("/node-status")
async def check_node_status(user: dict = Depends(get_current_user)) -> dict:
    """Check if user has a node registered in Headscale.
    
    This only checks Headscale API for node registration and online status.
    Does NOT attempt to connect to the database.
    
    Returns:
    - node_connected: True if node is registered and online in Headscale
    - tailscale_ip: The node's Tailscale IP if found
    """
    user_id = user["sub"]
    
    try:
        tailscale_ip = await get_user_tailscale_ip(user_id)
        
        if tailscale_ip:
            # Update cached data if we have it
            if user_id in _user_data:
                _user_data[user_id]["node_connected"] = True
                _user_data[user_id]["tailscale_ip"] = tailscale_ip
                _user_data[user_id]["node_count"] = 1
            else:
                # Create initial cached data
                _user_data[user_id] = {
                    "email": user.get("email"),
                    "status": "pending",
                    "last_seen": None,
                    "tailscale_ip": tailscale_ip,
                    "install_command": None,
                    "node_count": 1,
                    "node_connected": True,
                    "database_connected": False,
                }
            
            return {
                "user_id": user_id,
                "node_connected": True,
                "tailscale_ip": tailscale_ip,
                "namespace": user_id_to_namespace(user_id),
            }
        else:
            # Update cached data if we have it
            if user_id in _user_data:
                _user_data[user_id]["node_connected"] = False
            
            return {
                "user_id": user_id,
                "node_connected": False,
                "tailscale_ip": None,
                "namespace": user_id_to_namespace(user_id),
                "error": "No online nodes found in Headscale",
            }
    except Exception as e:
        print(f"[onboarding] Error checking node status: {e}")
        return {
            "user_id": user_id,
            "node_connected": False,
            "tailscale_ip": None,
            "namespace": user_id_to_namespace(user_id),
            "error": str(e),
        }


@router.get("/connection")
async def check_connection(user: dict = Depends(get_current_user)) -> dict:
    """Check if user's database is connected and accessible.
    
    This verifies:
    1. User has a node registered in Headscale
    2. The node is online
    3. We can connect to ArcadeDB on that node

    Returns connection status and details.
    """
    user_id = user["sub"]
    result = await verify_user_connection(user_id)
    
    # Update cached user data if we have it
    if user_id in _user_data:
        _user_data[user_id]["node_connected"] = result.get("node_connected", False)
        _user_data[user_id]["database_connected"] = result.get("database_connected", False)
        if result["connected"]:
            _user_data[user_id]["status"] = "connected"
            _user_data[user_id]["last_seen"] = datetime.now(timezone.utc)
            _user_data[user_id]["tailscale_ip"] = result.get("tailscale_ip")
        else:
            _user_data[user_id]["status"] = "disconnected"
    
    return {
        "user_id": user_id,
        "namespace": user_id_to_namespace(user_id),
        **result,
    }


@router.get("/nodes", response_model=NodesListResponse)
async def list_nodes(user: dict = Depends(get_current_user)) -> NodesListResponse:
    """List all Tailscale nodes registered under the requester's namespace.

    Powers the Devices table on the dashboard.
    """
    user_id = user["sub"]
    cap, tier = _get_user_node_cap(user)
    raw_nodes = await _list_user_nodes_from_headscale(user_id)
    return NodesListResponse(
        nodes=[_node_to_summary(n) for n in raw_nodes],
        cap=cap,
        tier=tier,
    )


@router.post("/nodes/preauth", response_model=CreateNodePreauthResponse)
async def create_node_preauth(
    request: CreateNodePreauthRequest,
    user: dict = Depends(get_current_user),
) -> CreateNodePreauthResponse:
    """Mint a fresh preauth key for adding a new node, gated by tier cap.

    The returned `install_command` already contains `--name <node_name>` so
    the new device registers with the user-chosen name.
    """
    user_id = user["sub"]
    cap, _tier = _get_user_node_cap(user)

    existing_nodes = await _list_user_nodes_from_headscale(user_id)
    if len(existing_nodes) >= cap:
        raise HTTPException(
            status_code=409,
            detail=(
                f"You've reached the {cap}-device limit for your tier. "
                "Remove an existing device before adding another."
            ),
        )

    node_name = _validate_node_name(request.node_name) if request.node_name else None

    pre_auth_key, expires = await create_headscale_preauth_key(user_id)
    install_command = generate_install_command(pre_auth_key, user_id, node_name=node_name)

    return CreateNodePreauthResponse(
        pre_auth_key=pre_auth_key,
        install_command=install_command,
        expires_at=expires,
    )


@router.patch("/nodes/{node_id}", response_model=NodeSummary)
async def rename_node(
    node_id: str,
    request: RenameNodeRequest,
    user: dict = Depends(get_current_user),
) -> NodeSummary:
    """Rename a Headscale node owned by the requester."""
    user_id = user["sub"]
    new_name = _validate_node_name(request.name)

    # Ownership check + retrieve current state.
    await _get_user_node_or_404(user_id, node_id)

    config = get_headscale_config()
    if not config["api_key"]:
        raise HTTPException(status_code=503, detail="Headscale not configured.")

    api_url = config["api_url"]
    verify_ssl = not api_url.startswith("https://headscale:")

    try:
        async with httpx.AsyncClient(timeout=5.0, verify=verify_ssl) as client:
            response = await client.post(
                f"{api_url}/api/v1/node/{node_id}/rename/{new_name}",
                headers={"Authorization": f"Bearer {config['api_key']}"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Headscale unreachable: {exc}") from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Headscale rename failed ({response.status_code}): {response.text[:200]}",
        )

    payload = response.json() or {}
    renamed = payload.get("node") or payload
    return _node_to_summary(renamed)


@router.delete("/nodes/{node_id}")
async def delete_node(
    node_id: str,
    user: dict = Depends(get_current_user),
) -> Response:
    """Remove a Headscale node owned by the requester. Irreversible."""
    user_id = user["sub"]

    # Ownership check.
    await _get_user_node_or_404(user_id, node_id)

    config = get_headscale_config()
    if not config["api_key"]:
        raise HTTPException(status_code=503, detail="Headscale not configured.")

    api_url = config["api_url"]
    verify_ssl = not api_url.startswith("https://headscale:")

    try:
        async with httpx.AsyncClient(timeout=5.0, verify=verify_ssl) as client:
            response = await client.delete(
                f"{api_url}/api/v1/node/{node_id}",
                headers={"Authorization": f"Bearer {config['api_key']}"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Headscale unreachable: {exc}") from exc

    if response.status_code not in (200, 204):
        raise HTTPException(
            status_code=502,
            detail=f"Headscale delete failed ({response.status_code}): {response.text[:200]}",
        )

    return Response(status_code=204)


@router.get("/local-dashboard/resolve")
async def resolve_local_dashboard(user: dict = Depends(get_current_user)) -> dict:
    """Resolve the user's LAN dashboard URL at click-time.

    Flow:
    1. Look up the user's node Tailscale IP
    2. Query a tiny endpoint running on the user's machine over Tailscale
    3. Return a validated LAN URL for browser redirect
    """
    user_id = user["sub"]
    tailscale_ip = await get_user_tailscale_ip(user_id)
    if not tailscale_ip:
        raise HTTPException(status_code=404, detail="No online BYODB node found.")

    resolver_path = LOCAL_DASHBOARD_RESOLVER_PATH
    if not resolver_path.startswith("/"):
        resolver_path = "/" + resolver_path

    resolver_url = f"http://{tailscale_ip}:{LOCAL_DASHBOARD_RESOLVER_PORT}{resolver_path}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=1.0)) as client:
            response = await client.get(resolver_url)
            response.raise_for_status()
            payload = response.json()
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not resolve local dashboard endpoint from your BYODB node. "
                "Run the latest install/update script and ensure your node is online."
            ),
        ) from e

    local_admin_access = str(payload.get("local_admin_access", "")).strip().lower()
    allow_loopback_admin_host = local_admin_access in {"localhost", "local", "loopback"}
    lan_ip = str(payload.get("lan_ip", "")).strip()
    if not _is_valid_private_ipv4(lan_ip, allow_loopback=allow_loopback_admin_host):
        raise HTTPException(status_code=502, detail="Resolver returned an invalid LAN IPv4 address.")

    local_admin_host = str(payload.get("local_admin_host", "")).strip() or lan_ip
    if not _is_valid_private_ipv4(local_admin_host, allow_loopback=allow_loopback_admin_host):
        raise HTTPException(status_code=502, detail="Resolver returned an invalid local admin host.")

    port = _parse_dashboard_port(payload.get("port"), default=3000)
    path = str(payload.get("path", "/")).strip() or "/"
    if not path.startswith("/"):
        path = "/" + path

    local_admin_port = _parse_dashboard_port(payload.get("local_admin_port"), default=4466)
    local_admin_path = str(payload.get("local_admin_path", "/")).strip() or "/"
    if not local_admin_path.startswith("/"):
        local_admin_path = "/" + local_admin_path

    return {
        "user_id": user_id,
        "tailscale_ip": tailscale_ip,
        "url": f"http://{lan_ip}:{port}{path}",
        "local_admin_url": f"http://{local_admin_host}:{local_admin_port}{local_admin_path}",
        "resolved": True,
    }


@router.get("/update-compose")
async def get_updated_compose(user: dict = Depends(get_current_user)) -> dict:
    """Get the latest docker-compose.yml for existing users to update their installation.
    
    This allows users to pick up new features without needing to completely
    reinstall. The compose file doesn't include the pre-auth key since
    Tailscale is already authenticated.
    
    Usage:
    1. Download the new compose file
    2. cd ~/.loreholm
    3. docker compose down
    4. docker compose pull
    5. Replace docker-compose.yml with the new content
    6. docker compose up -d
    """
    user_id = user["sub"]
    namespace = user_id_to_namespace(user_id)
    public_host = os.getenv("PUBLIC_API_HOST", "http://localhost:8080")
    headscale_url = public_host + ":50443"
    
    # Get existing node name if available
    node_name = "loreholm-node"
    try:
        result = await verify_user_connection(user_id)
        if user_id in _user_data:
            node_name = _user_data[user_id].get("node_name", node_name)
    except Exception:
        pass
    
    compose_content = f"""# loreholm BYODB Stack
# Updated docker-compose.yml - regenerated by your loreholm server
# Documentation: {public_host}/docs

services:
  # Tailscale sidecar - connects to Headscale mesh network
  tailscale:
    image: tailscale/tailscale:latest
    container_name: loreholm-tailscale
    hostname: {node_name}
    restart: unless-stopped
    # Only NET_ADMIN is required: /dev/net/tun is bind-mounted, so the host
    # already provides the tun device. Re-add SYS_MODULE only if a host lacks
    # the tun kernel module and tailscale cannot create the interface.
    cap_add:
      - NET_ADMIN
    volumes:
      - tailscale_state:/var/lib/tailscale
      - /dev/net/tun:/dev/net/tun
    environment:
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_USERSPACE=false
      # No --accept-routes: a leaf node never needs subnet routes pushed by the
      # control server, so Headscale cannot steer this node's traffic.
      - TS_EXTRA_ARGS=--login-server={headscale_url}
    healthcheck:
      test: ["CMD", "tailscale", "status"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # Bifrost proxy for local wizard and chat-compatible /v1 model APIs.
  # Lives on the default compose bridge — NOT in the tailscale netns —
  # so 8080 is unreachable from the tailnet. Reached from the dashboard
  # as `loreholm-bifrost-proxy:8080` over the bridge.
  bifrost-proxy:
    image: {LOCAL_DASHBOARD_BIFROST_IMAGE}
    container_name: loreholm-bifrost-proxy
    restart: unless-stopped
    volumes:
      - ./chat-bifrost-config.json:/app/config/config.json:ro

  # Single shared ArcadeDB server. All per-database CRUD is HTTP against
  # this one container; no per-database containers, no Docker socket.
  # Lives on the default compose bridge — NOT in the tailscale netns —
  # so 2480 is unreachable from the tailnet regardless of ACL state.
  # Reached from the dashboard as `loreholm-arcadedb:2480` over the bridge.
  arcadedb:
    image: arcadedata/arcadedb:26.3.1
    container_name: loreholm-arcadedb
    restart: unless-stopped
    environment:
      JAVA_OPTS: "-Darcadedb.server.httpIncoming.port=2480 -Darcadedb.server.rootPasswordPath=/opt/arcadedb/root-password -Darcadedb.server.mode=production -Darcadedb.profile=low-ram"
    volumes:
      - arcadedb_data:/home/arcadedb/databases
      - arcadedb_log:/home/arcadedb/log
      - ./arcadedb-root.password:/opt/arcadedb/root-password:ro

  # Local admin API + basic graph visualization for all registered local databases.
  local-dashboard:
    image: {LOCAL_DASHBOARD_IMAGE}
    container_name: loreholm-local-dashboard
    restart: unless-stopped
    depends_on:
      tailscale:
        condition: service_healthy
      arcadedb:
        condition: service_started
    ports:
      - "4466:4466"
    environment:
      - LOCAL_DASHBOARD_TOKEN_FILE=/opt/loreholm/local-dashboard.token
      - LOCAL_SYNC_TOKEN_FILE=/opt/loreholm/local-sync.token
      - LOCAL_DASHBOARD_REGISTRY_FILE=/opt/loreholm/databases.json
      - LOCAL_DASHBOARD_BIFROST_CONFIG_FILE=/opt/loreholm/chat-bifrost-config.json
      - LOCAL_DASHBOARD_BIFROST_URL=http://loreholm-bifrost-proxy:8080
      - LOCAL_DASHBOARD_ARCADEDB_HOST=loreholm-arcadedb
      - LOCAL_DASHBOARD_ARCADEDB_PORT=2480
      - LOCAL_DASHBOARD_ARCADEDB_ROOT_PASSWORD_FILE=/opt/loreholm/arcadedb-root.password
    command:
      - uvicorn
      - app.local_dashboard.main:app
      - --host
      - 0.0.0.0
      - --port
      - "4466"
    volumes:
      - ./local-dashboard.token:/opt/loreholm/local-dashboard.token:ro
      - ./local-sync.token:/opt/loreholm/local-sync.token:ro
      - ./databases.json:/opt/loreholm/databases.json
      - ./chat-bifrost-config.json:/opt/loreholm/chat-bifrost-config.json
      - ./arcadedb-root.password:/opt/loreholm/arcadedb-root.password:ro

  # Tiny metadata endpoint exposed on the node's Tailscale IP so the API
  # can resolve the correct LAN URL for the dashboard at click time.
  local-dashboard-endpoint:
    image: python:3.12-alpine
    container_name: loreholm-local-dashboard-endpoint
    restart: unless-stopped
    network_mode: service:tailscale
    depends_on:
      tailscale:
        condition: service_healthy
    command:
      - python
      - /opt/local-dashboard/endpoint_server.py
    environment:
      - LOCAL_DASHBOARD_META_FILE=/opt/local-dashboard/local-dashboard.json
      - LOCAL_DASHBOARD_REGISTRY_FILE=/opt/loreholm/databases.json
      - LOCAL_SYNC_TOKEN_FILE=/opt/loreholm/local-sync.token
      - LOCAL_SYNC_BIND_PORT=8081
    volumes:
      - ./local-dashboard:/opt/local-dashboard:ro
      - ./local-sync.token:/opt/loreholm/local-sync.token:ro
      - ./databases.json:/opt/loreholm/databases.json:ro

volumes:
  tailscale_state:
    name: loreholm-tailscale-state
  arcadedb_data:
    name: loreholm-arcadedb-data
  arcadedb_log:
    name: loreholm-arcadedb-log
"""
    
    return {
        "user_id": user_id,
        "namespace": namespace,
        "compose_content": compose_content,
        "instructions": [
            "1. SSH into your server running loreholm",
            "2. cd ~/.loreholm",
            "3. docker compose down",
            "4. docker compose pull",
            "5. Create local-dashboard/local-dashboard.json and local-dashboard/endpoint_server.py (update scripts generate both automatically)",
            "6. Ensure local-dashboard.token, local-sync.token, and databases.json exist (update scripts generate them automatically)",
            "7. Ensure chat-bifrost-config.json exists (minimum content: providers object with no keys)",
            "8. Save the compose_content to docker-compose.yml",
            "9. docker compose up -d",
            "10. Open the local dashboard at http://<your-server-ip>:4466",
        ],
        "note": "Your Tailscale authentication is preserved in the volume, no new key needed.",
    }


@router.get("/debug/headscale")
async def debug_headscale(user: dict = Depends(get_current_user)) -> dict:
    """Debug endpoint to check Headscale connection and list all machines.
    
    This is helpful for troubleshooting connection issues.
    """
    if not _debug_routes_enabled():
        raise HTTPException(status_code=404, detail="Not Found")

    config = get_headscale_config()
    user_id = user["sub"]
    namespace = user_id_to_namespace(user_id)
    
    if not config["api_key"]:
        return {
            "error": "No Headscale API key configured (dev mode)",
            "user_namespace": namespace,
        }
    
    api_url = config["api_url"]
    verify_ssl = not api_url.startswith("https://headscale:")
    
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=verify_ssl) as client:
            headers = {"Authorization": f"Bearer {config['api_key']}"}
            
            # List all nodes (Headscale v0.23+ uses /node instead of /machine)
            response = await client.get(
                f"{api_url}/api/v1/node",
                headers=headers,
            )
            
            if response.status_code != 200:
                return {
                    "error": f"Headscale API error: {response.status_code}",
                    "response": response.text[:500],
                    "api_url": api_url,
                }
            
            data = response.json()
            nodes = data.get("nodes", [])
            
            # Summarize nodes
            node_summaries = []
            user_nodes = []
            for n in nodes:
                node_info = {
                    "id": n.get("id"),
                    "name": n.get("name"),
                    "namespace": n.get("user", {}).get("name"),
                    "online": n.get("online"),
                    "ips": n.get("ipAddresses", []),
                    "lastSeen": n.get("lastSeen"),
                }
                node_summaries.append(node_info)
                
                if n.get("user", {}).get("name") == namespace:
                    user_nodes.append(node_info)
            
            return {
                "user_id": user_id,
                "user_namespace": namespace,
                "total_nodes": len(nodes),
                "user_nodes": user_nodes,
                "all_nodes": node_summaries,
                "headscale_api_url": api_url,
            }
            
    except Exception as e:
        return {
            "error": str(e),
            "user_namespace": namespace,
            "headscale_api_url": api_url,
        }
