"""
Redis client for API key management.

Handles:
- Storing API key metadata (name, created_at, expires_at)
- Tracking revoked keys with automatic TTL expiry
- Enforcing per-user key limits
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as redis


def get_redis_config() -> dict:
    """Get Redis configuration from environment."""
    return {
        "host": os.getenv("REDIS_HOST", "localhost"),
        "port": int(os.getenv("REDIS_PORT", "6379")),
        "password": os.getenv("REDIS_PASSWORD"),
        "db": int(os.getenv("REDIS_DB", "0")),
    }


# Global Redis client (lazy initialized)
_redis_client: Optional[redis.Redis] = None


async def get_redis() -> redis.Redis:
    """Get or create Redis client."""
    global _redis_client
    if _redis_client is None:
        config = get_redis_config()
        _redis_client = redis.Redis(
            host=config["host"],
            port=config["port"],
            password=config["password"] if config["password"] else None,
            db=config["db"],
            decode_responses=True,
        )
    return _redis_client


async def close_redis() -> None:
    """Close Redis connection."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None


# Key patterns
def _user_keys_key(user_id: str) -> str:
    """Redis key for user's API key metadata hash."""
    return f"apikeys:{user_id}:keys"


def _revoked_key(key_id: str) -> str:
    """Redis key for revoked key marker."""
    return f"apikeys:revoked:{key_id}"


# Constants
MAX_KEYS_PER_USER = 10


class ApiKeyStore:
    """Redis-backed API key storage."""

    def __init__(self, client: redis.Redis):
        self.client = client

    async def count_user_keys(self, user_id: str) -> int:
        """Count how many active keys a user has."""
        return await self.client.hlen(_user_keys_key(user_id))

    async def can_create_key(self, user_id: str) -> bool:
        """Check if user can create another key."""
        count = await self.count_user_keys(user_id)
        return count < MAX_KEYS_PER_USER

    async def store_key_metadata(
        self,
        user_id: str,
        key_id: str,
        name: str,
        expires_at: datetime,
        database: Optional[dict] = None,
        database_target_id: Optional[str] = None,
    ) -> None:
        """Store metadata about an API key.
        
        We don't store the actual key - just metadata for listing/revocation.
        """
        metadata = {
            "key_id": key_id,
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at.isoformat(),
            "database": database or None,
            "database_target_id": database_target_id,
        }
        await self.client.hset(
            _user_keys_key(user_id),
            key_id,
            json.dumps(metadata),
        )

    async def list_user_keys(self, user_id: str) -> list[dict]:
        """List all API keys for a user (metadata only, not the actual keys)."""
        keys_data = await self.client.hgetall(_user_keys_key(user_id))
        
        keys = []
        now = datetime.now(timezone.utc)
        
        for key_id, metadata_json in keys_data.items():
            metadata = json.loads(metadata_json)
            expires_at = datetime.fromisoformat(metadata["expires_at"])
            
            # Check if expired
            is_expired = expires_at < now
            
            # Check if revoked
            is_revoked = await self.is_key_revoked(key_id)
            
            keys.append({
                **metadata,
                "is_expired": is_expired,
                "is_revoked": is_revoked,
                "is_active": not is_expired and not is_revoked,
            })
        
        # Sort by created_at descending
        keys.sort(key=lambda k: k["created_at"], reverse=True)
        return keys

    async def count_active_keys_for_target(self, user_id: str, target_id: str) -> int:
        """Count active keys that reference a specific database target."""
        keys = await self.list_user_keys(user_id)
        return sum(
            1
            for key in keys
            if key.get("is_active") and key.get("database_target_id") == target_id
        )

    async def get_key_metadata(self, user_id: str, key_id: str) -> Optional[dict]:
        """Get metadata for a specific key."""
        metadata_json = await self.client.hget(_user_keys_key(user_id), key_id)
        if metadata_json:
            return json.loads(metadata_json)
        return None

    async def revoke_key(self, user_id: str, key_id: str) -> bool:
        """Revoke an API key.
        
        Sets a revocation marker with TTL matching the key's expiry.
        Also removes from user's key list.
        """
        # Get key metadata to find expiry
        metadata = await self.get_key_metadata(user_id, key_id)
        if not metadata:
            return False
        
        expires_at = datetime.fromisoformat(metadata["expires_at"])
        now = datetime.now(timezone.utc)
        
        # Calculate TTL - how long until key would naturally expire
        ttl_seconds = int((expires_at - now).total_seconds())
        
        if ttl_seconds > 0:
            # Set revocation marker with TTL
            await self.client.setex(
                _revoked_key(key_id),
                ttl_seconds,
                "revoked",
            )
        
        # Remove from user's key list
        await self.client.hdel(_user_keys_key(user_id), key_id)
        
        return True

    async def is_key_revoked(self, key_id: str) -> bool:
        """Check if a key has been revoked."""
        return await self.client.exists(_revoked_key(key_id)) > 0

    async def cleanup_expired_keys(self, user_id: str) -> int:
        """Remove expired keys from user's list. Returns count removed."""
        keys = await self.list_user_keys(user_id)
        removed = 0
        
        for key in keys:
            if key["is_expired"]:
                await self.client.hdel(_user_keys_key(user_id), key["key_id"])
                removed += 1
        
        return removed


async def get_api_key_store() -> ApiKeyStore:
    """Get API key store instance."""
    client = await get_redis()
    return ApiKeyStore(client)
