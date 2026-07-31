"""Session JWTs and server-side claim binding (§9.1/§9.2)."""

import time

from gateway.sessions import issue_widget_session, mint_token, verify_token

SECRET = "test-secret"
KEYS = {"widget-key-tiq": "tiq", "widget-key-etiqa": "etiqa"}


def test_mint_and_verify_roundtrip() -> None:
    token = mint_token({"sid": "s1", "brand": "tiq", "audience": "public"}, SECRET, ttl_minutes=5)
    claims = verify_token(token, SECRET)
    assert claims is not None
    assert claims["brand"] == "tiq" and claims["audience"] == "public"


def test_tampered_token_rejected() -> None:
    token = mint_token({"sid": "s1", "brand": "tiq", "audience": "public"}, SECRET, 5)
    header, payload, signature = token.split(".")
    tampered_payload = payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB")
    assert verify_token(f"{header}.{tampered_payload}.{signature}", SECRET) is None
    assert verify_token(token, "other-secret") is None
    assert verify_token("garbage", SECRET) is None


def test_expired_token_rejected() -> None:
    token = mint_token({"sid": "s1", "exp_override": True}, SECRET, ttl_minutes=0)
    # ttl 0 => exp == iat == now; wait past it
    time.sleep(1.1)
    assert verify_token(token, SECRET) is None


def test_widget_session_binds_brand_server_side() -> None:
    issued = issue_widget_session("widget-key-etiqa", KEYS, SECRET, 5)
    assert issued is not None
    _, token = issued
    claims = verify_token(token, SECRET)
    assert claims is not None
    assert claims["brand"] == "etiqa"  # from the key registry, not the client
    assert claims["audience"] == "public"


def test_unknown_widget_key_rejected() -> None:
    assert issue_widget_session("stolen-key", KEYS, SECRET, 5) is None
