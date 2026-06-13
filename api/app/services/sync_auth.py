"""Per-user sync token derivation.

The cloud authenticates to each user's local dashboard `/api/sync/*`
endpoints over Tailnet with a Bearer token. Previously the whole fleet
shared one static token (`LOCAL_SYNC_TOKEN` / `LOCAL_SYNC_SHARED_TOKEN`);
if it leaked, every user's local node was exposed. This module replaces
that with a per-user HMAC derivation so a compromised token only reveals
one user, and rotation is a secret-file edit rather than a fleet reinstall.

Design:
  - The cloud holds one long-lived `LOCAL_SYNC_SIGNING_SECRET` (env var or
    mounted file). This secret never leaves the cloud.
  - For each user, `derive_user_sync_token(user_id)` returns
    `HMAC_SHA256(secret, user_id)` encoded as URL-safe base64. The value is
    stable for a given (secret, user_id) pair.
  - The onboarding install-command generator embeds this derived token
    into the `--sync-token` flag of the installer, which writes it to the
    user's `local-sync.token` file. The local dashboard continues to do a
    byte-for-byte comparison against that file — it never sees or derives
    anything.
  - On every outbound cloud->local sync call, the cloud re-derives the
    token from the authenticated user's sub. Steady state is HMAC per
    request, which is cheap.

Rotation: bumping `LOCAL_SYNC_SIGNING_SECRET` invalidates every user's
currently-installed token. Users need to re-run the install command from
their onboarding dashboard, which will emit the freshly-derived token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path


LOCAL_SYNC_SIGNING_SECRET_FILE = os.getenv(
    "LOCAL_SYNC_SIGNING_SECRET_FILE",
    "/opt/mcp-cloud/local-sync-signing.secret",
)


class SyncAuthNotConfiguredError(RuntimeError):
    """Raised when the cloud has no signing secret to derive per-user
    tokens. This is a cloud-side deployment problem — the operator needs
    to set `LOCAL_SYNC_SIGNING_SECRET` (or mount the secret file). It is
    not a per-user or per-install failure.
    """


def _load_signing_secret() -> bytes:
    value = (os.getenv("LOCAL_SYNC_SIGNING_SECRET") or "").strip()
    if not value:
        try:
            value = (
                Path(LOCAL_SYNC_SIGNING_SECRET_FILE)
                .read_text(encoding="utf-8")
                .strip()
            )
        except OSError:
            value = ""
    if not value:
        raise SyncAuthNotConfiguredError(
            "Local sync signing secret is not configured. Set "
            "LOCAL_SYNC_SIGNING_SECRET or LOCAL_SYNC_SIGNING_SECRET_FILE "
            f"({LOCAL_SYNC_SIGNING_SECRET_FILE})."
        )
    return value.encode("utf-8")


def derive_user_sync_token(user_id: str) -> str:
    """Derive the per-user sync bearer token.

    HMAC-SHA256 over the user's Auth0 sub with the fleet-wide signing
    secret as the key. Returned as unpadded URL-safe base64 (43 chars),
    which fits cleanly in an `Authorization: Bearer …` header and in a
    shell `--sync-token` argument without quoting.
    """
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required to derive a sync token.")
    secret = _load_signing_secret()
    digest = hmac.new(secret, user_id.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
