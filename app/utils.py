"""Signature verification and plain-env helpers."""
import hashlib
import hmac
import os
from typing import Optional

from fastapi import HTTPException, status


# ---------------------------------------------------------------------------
# GitHub signature
# ---------------------------------------------------------------------------

def verify_signature(signature_header: Optional[str], body: bytes):
    from app.models import get_setting
    secret = get_setting("GITHUB_WEBHOOK_SECRET", "").encode()

    if not signature_header:
        if not secret:
            return  # No secret configured — allow all webhooks
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="Missing X-Hub-Signature-256 header")
    if not secret:
        return  # Secret not configured — skip verification

    parts = signature_header.split("=", 1)
    if len(parts) != 2 or parts[0] != "sha256":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Unsupported signature algorithm")
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts[1]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid signature")


# ---------------------------------------------------------------------------
# Plain .env parser
# ---------------------------------------------------------------------------

def parse_env_file(content: str) -> dict[str, str]:
    """Parse a plain KEY=VALUE .env file into a dict.
    Skips blank lines and comments (#). Strips surrounding quotes from values.
    """
    result = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Strip matching surrounding quotes
        if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
            val = val[1:-1]
        if key:
            result[key] = val
    return result
