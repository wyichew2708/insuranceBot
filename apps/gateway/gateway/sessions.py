"""Session issuance (§9.2): compact HS256 JWTs, dependency-free.

The audience and brand claims are set SERVER-SIDE: brand comes from the
widget key registry (anti-tamper — the client cannot pick a brand), audience
is `public` for widget sessions and `internal` only via the SSO header stub.
With no SESSION_SECRET configured the gateway runs in dev mode and trusts
the request body (never do this in production).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def mint_token(claims: dict[str, Any], secret: str, ttl_minutes: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {**claims, "iat": int(time.time()), "exp": int(time.time()) + ttl_minutes * 60}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def verify_token(token: str, secret: str) -> dict[str, Any] | None:
    """Returns the claims, or None for any invalid/expired/tampered token."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature_b64)):
            return None
        header = json.loads(_unb64(header_b64))
        if header.get("alg") != "HS256":
            return None
        claims: dict[str, Any] = json.loads(_unb64(payload_b64))
        if int(claims.get("exp", 0)) < time.time():
            return None
        return claims
    except Exception:
        return None


def issue_widget_session(
    widget_key: str, widget_key_map: dict[str, str], secret: str, ttl_minutes: int
) -> tuple[str, str] | None:
    """(session_id, token) for a valid widget key; None for unknown keys.
    Brand is bound from the key registry, audience is always public."""
    brand = widget_key_map.get(widget_key)
    if brand is None:
        return None
    session_id = uuid.uuid4().hex
    token = mint_token(
        {"sid": session_id, "brand": brand, "audience": "public", "channel": "widget"},
        secret,
        ttl_minutes,
    )
    return session_id, token


def issue_internal_session(staff_user: str, brand: str, secret: str, ttl_minutes: int) -> tuple[str, str]:
    """Internal portal session behind the SSO header stub (§9.3)."""
    session_id = uuid.uuid4().hex
    token = mint_token(
        {
            "sid": session_id,
            "brand": brand,
            "audience": "internal",
            "channel": "portal",
            "staff": staff_user,
        },
        secret,
        ttl_minutes,
    )
    return session_id, token
