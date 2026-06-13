"""
User-specific store management for BYODB architecture.

Each authenticated user has their own ArcadeDB database running on their machine,
accessible via their Tailscale IP on the Headscale mesh network.

Security Model:
1. User authenticates via Auth0 JWT
2. User's Auth0 sub (user ID) maps to a Headscale namespace
3. We query Headscale to get the user's node Tailscale IP
4. We connect to the local dashboard at that IP for all their requests
5. Headscale namespaces provide network-level isolation
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import httpx

_executor = ThreadPoolExecutor(max_workers=4)

from app.services.arcadedb_store import ArcadeDBConfig, ArcadeDBStore
from app.services.graph_store_errors import GraphStoreUnavailableError
from app.services.database_targets import get_database_target_for_routing
from app.services.sync_auth import (
    SyncAuthNotConfiguredError,
    derive_user_sync_token,
)


# Cache user stores to avoid reconnecting on every request.
# Key: route hash, Value: ArcadeDBStore
_user_stores: dict[str, ArcadeDBStore] = {}
# Reverse index for targeted invalidation.
# Key: route hash, Value: user_id
_user_store_users: dict[str, str] = {}


def get_headscale_config() -> dict:
    """Get Headscale configuration from environment."""
    return {
        "api_url": os.getenv("HEADSCALE_API_URL", "http://headscale:8080"),
        "api_key": os.getenv("HEADSCALE_API_KEY", ""),
    }


def user_id_to_namespace(user_id: str) -> str:
    """Convert Auth0 user ID to Headscale namespace.
    
    Auth0 user IDs look like: google-oauth2|123456789
    Headscale namespaces must be alphanumeric with hyphens.
    """
    return f"user-{user_id.replace('|', '-').replace(':', '-')}"


async def get_user_tailscale_ip(user_id: str) -> Optional[str]:
    """Query Headscale to get the user's node Tailscale IP.
    
    Returns the IP of the user's first online node, or None if not found.
    """
    config = get_headscale_config()
    
    # Dev mode: no Headscale API key configured
    if not config["api_key"]:
        # Return mock IP for development/testing — ArcadeDB runs locally or
        # behind a test instance in that case.
        dev_ip = os.getenv("DEV_ARCADEDB_HOST", "localhost")
        return dev_ip
    
    namespace = user_id_to_namespace(user_id)
    
    # For internal Docker network communication, we may need to skip SSL verification
    # since the cert is for the public domain, not the internal container hostname
    api_url = config["api_url"]
    verify_ssl = not api_url.startswith("https://headscale:")  # Skip for internal Docker
    
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=verify_ssl) as client:
            headers = {"Authorization": f"Bearer {config['api_key']}"}
            
            # List ALL nodes first (Headscale v0.23+ uses /node instead of /machine)
            response = await client.get(
                f"{config['api_url']}/api/v1/node",
                headers=headers,
            )
            
            if response.status_code != 200:
                print(f"[user_store] Headscale API error: {response.status_code} - {response.text}")
                return None
            
            data = response.json()
            nodes = data.get("nodes", [])
            
            print(f"[user_store] Found {len(nodes)} total nodes, looking for namespace: {namespace}")
            
            # Find nodes belonging to this user's namespace
            #
            # TODO(backup-node): With multi-node support (free-tier cap of
            # 3 devices), this picks the first online node in iteration
            # order, which is non-deterministic when 2+ are online. Each
            # database lives on a specific node's ArcadeDB; routing by
            # "first online" can land on the wrong machine. Bind
            # database_targets to a node_id at sync time and route here
            # by target -> node, falling back to "primary" or "any
            # online" only when no specific target is requested. Design
            # alongside backup-node semantics (active/standby).
            for node in nodes:
                node_user = node.get("user", {})
                node_namespace = node_user.get("name", "")
                
                print(f"[user_store] Node: {node.get('name')} | Namespace: {node_namespace} | Online: {node.get('online')}")
                
                # Check if node belongs to this user's namespace
                if node_namespace != namespace:
                    continue
                
                # Check if node is online
                if not node.get("online", False):
                    continue
                
                # Get the Tailscale IP (usually in ipAddresses)
                ip_addresses = node.get("ipAddresses", [])
                for ip in ip_addresses:
                    # Prefer IPv4 addresses (100.x.x.x for Tailscale)
                    if ip.startswith("100."):
                        print(f"[user_store] Found user's node with IP: {ip}")
                        return ip
                
                # Fallback to first IP if no 100.x.x.x found
                if ip_addresses:
                    return ip_addresses[0]
            
            print(f"[user_store] No online nodes found for namespace: {namespace}")
            return None
            
    except httpx.RequestError as e:
        print(f"[user_store] Headscale request error: {e}")
        return None


def _store_cache_key(user_id: str, database_id: Optional[str]) -> str:
    payload = {
        "user_id": user_id,
        "database_id": database_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


async def get_user_store(
    user_id: str,
    database_target_id: Optional[str] = None,
) -> Optional[ArcadeDBStore]:
    """Return an ArcadeDBStore that routes queries to the user's local dashboard.

    Returns None when routing cannot be resolved (user has no online node,
    or target_id is unknown).
    """
    database_id: Optional[str] = None
    if database_target_id:
        try:
            target_database = await get_database_target_for_routing(
                user_id,
                database_target_id,
            )
        except RuntimeError:
            return None
        if not target_database:
            return None
        database_id = target_database.get("database_id")

    cache_key = _store_cache_key(user_id, database_id)

    # Check cache first
    cached_store = _user_stores.get(cache_key)
    if cached_store:
        # TODO: Add TTL or verify node is still online.
        return cached_store

    target_host = await get_user_tailscale_ip(user_id)
    if not target_host:
        return None

    # The cloud never opens direct database connections. Every query flows
    # through the user's local-dashboard proxy endpoint. The transport layer
    # only needs `host` (Tailnet IP of the dashboard) and `database_id`
    # (to route the query on the local side).
    try:
        sync_token = derive_user_sync_token(user_id)
    except SyncAuthNotConfiguredError as exc:
        # Fail loud: a missing signing secret is a cloud deployment
        # problem, not a "this user has no node" problem, so returning None
        # would swallow the real issue.
        raise GraphStoreUnavailableError(str(exc)) from exc

    store = ArcadeDBStore(
        ArcadeDBConfig(
            host=str(target_host),
            database_id=database_id,
            sync_token=sync_token,
        )
    )

    # Cache for future requests
    _user_stores[cache_key] = store
    _user_store_users[cache_key] = user_id

    return store


def clear_user_store_cache(user_id: Optional[str] = None) -> None:
    """Clear cached user stores.
    
    Call this when a user's node goes offline or changes IP.
    """
    if user_id:
        cache_keys = [
            cache_key
            for cache_key, owner_user_id in _user_store_users.items()
            if owner_user_id == user_id
        ]
        for cache_key in cache_keys:
            _user_stores.pop(cache_key, None)
            _user_store_users.pop(cache_key, None)
        return

    _user_stores.clear()
    _user_store_users.clear()


def _try_connect_sync(store: ArcadeDBStore) -> dict:
    """Synchronous helper to test connection - runs in thread pool."""
    try:
        stats = store.stats()
        return {"success": True, "stats": stats}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def verify_user_connection(user_id: str, timeout: float = 5.0) -> dict:
    """Verify that we can connect to a user's database.
    
    Returns connection status and details with separate node/database status:
    - node_connected: True if node is registered and online in Headscale
    - database_connected: True if we can actually connect to ArcadeDB
    - connected: True only if both node and database are accessible

    Uses a timeout to prevent hanging if the database is unreachable.
    """
    tailscale_ip = await get_user_tailscale_ip(user_id)
    
    if not tailscale_ip:
        return {
            "connected": False,
            "node_connected": False,
            "database_connected": False,
            "error": "No online nodes found for user",
            "tailscale_ip": None,
        }
    
    store = await get_user_store(user_id)
    
    if not store:
        return {
            "connected": False,
            "node_connected": True,  # Node is online in Headscale
            "database_connected": False,  # But can't create connection
            "error": "Could not create store connection",
            "tailscale_ip": tailscale_ip,
        }
    
    try:
        # Run the blocking connection test in a thread pool with timeout
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(_executor, _try_connect_sync, store),
            timeout=timeout
        )
        
        if result["success"]:
            return {
                "connected": True,
                "node_connected": True,
                "database_connected": True,
                "tailscale_ip": tailscale_ip,
                "stats": result.get("stats"),
            }
        else:
            return {
                "connected": False,
                "node_connected": True,  # Node is online
                "database_connected": False,  # But ArcadeDB connection failed
                "error": result.get("error", "Connection failed"),
                "tailscale_ip": tailscale_ip,
            }
    except asyncio.TimeoutError:
        # Clear cached store since connection failed
        clear_user_store_cache(user_id)
        return {
            "connected": False,
            "node_connected": True,  # Node is online
            "database_connected": False,  # But connection timed out
            "error": "Connection timed out - database may be offline",
            "tailscale_ip": tailscale_ip,
        }
    except Exception as e:
        return {
            "connected": False,
            "node_connected": True,  # Node is online
            "database_connected": False,  # But got an error
            "error": str(e),
            "tailscale_ip": tailscale_ip,
        }
