"""
API Key authentication using PASETO tokens.

API keys are self-contained signed tokens that encode:
- User ID (sub)
- User email
- Key name (user-provided label)
- Key ID (for revocation tracking)
- Expiry timestamp
- Issued timestamp

The server validates keys by:
1. Verifying the cryptographic signature
2. Checking expiry
3. Checking revocation list in Redis
"""

from __future__ import annotations

import os
import json
import secrets
from base64 import b64decode, b64encode
from datetime import datetime, timedelta, timezone
from typing import Optional

import pyseto
from pyseto import Key

from app.services.redis_client import get_api_key_store


def get_signing_key() -> bytes:
    """Get the API key signing secret from environment.
    
    Must be a 32-byte key, base64 encoded in the environment variable.
    Generate with: python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
    """
    key_b64 = os.getenv("API_KEY_SIGNING_SECRET")
    if not key_b64:
        raise RuntimeError(
            "API_KEY_SIGNING_SECRET environment variable is required. "
            "Generate with: python -c \"import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        )
    
    key_bytes = b64decode(key_b64)
    if len(key_bytes) != 32:
        raise RuntimeError("API_KEY_SIGNING_SECRET must be exactly 32 bytes (base64 encoded)")
    
    return key_bytes


def generate_key_id() -> str:
    """Generate a unique key ID for tracking/revocation."""
    return f"ak_{secrets.token_urlsafe(16)}"


def create_api_key(
    user_id: str,
    email: str,
    name: str,
    expires_days: int = 365,
    database_target_id: Optional[str] = None,
) -> dict:
    """Create a new API key for a user.

    Under the query-proxy topology the cloud never dials the database
    directly, so keys carry only a `db_ref` pointing at a `database_targets`
    row (which in turn holds a `database_id` the local dashboard can route).
    Embedded connection info (`db` claim with host/port/password) is no longer
    supported.

    Returns:
        dict with:
        - api_key: The actual token (show once to user)
        - key_id: Identifier for revocation
        - name: User-provided name
        - expires_at: Expiry timestamp
    """
    key_id = generate_key_id()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=expires_days)

    # Payload to encode in the token
    payload = {
        "sub": user_id,
        "email": email,
        "name": name,
        "kid": key_id,
        "exp": expires_at.isoformat(),
        "iat": now.isoformat(),
    }
    if database_target_id:
        payload["db_ref"] = database_target_id

    # Create PASETO v4.local token (encrypted + authenticated)
    signing_key = get_signing_key()
    key = Key.new(version=4, purpose="local", key=signing_key)
    # pyseto.encode expects bytes payload, so we serialize to JSON bytes
    token = pyseto.encode(key, json.dumps(payload).encode())

    return {
        "api_key": token.decode() if isinstance(token, bytes) else token,
        "key_id": key_id,
        "name": name,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "database_target_id": database_target_id,
    }


async def validate_api_key(api_key: str) -> dict:
    """Validate an API key and return the decoded payload.
    
    Raises:
        ValueError: If key is invalid, expired, or revoked
    
    Returns:
        dict with user info: sub, email, name, kid, exp, iat, and optional db_ref
    """
    try:
        signing_key = get_signing_key()
        key = Key.new(version=4, purpose="local", key=signing_key)
        decoded = pyseto.decode(key, api_key)
        # Payload is bytes, decode to JSON
        payload = json.loads(decoded.payload.decode())
        
    except Exception as e:
        raise ValueError(f"Invalid API key: {e}")
    
    # Check expiry
    expires_at = datetime.fromisoformat(payload["exp"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    
    now = datetime.now(timezone.utc)
    if expires_at < now:
        raise ValueError("API key has expired")
    
    # Check revocation
    store = await get_api_key_store()
    if await store.is_key_revoked(payload["kid"]):
        raise ValueError("API key has been revoked")
    
    return payload


def mask_api_key(api_key: str) -> str:
    """Mask an API key for display (show first/last few chars)."""
    if len(api_key) < 20:
        return "****"
    return f"{api_key[:12]}...{api_key[-8:]}"
