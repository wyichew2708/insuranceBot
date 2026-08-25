"""Shared pytest fixtures.

Lives at the repo root so `evals` (a top-level package, not a workspace member)
imports the same way under pytest as it does under uvicorn, and so the bundle
fixture is defined once rather than in each package's tests directory.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUNDLE_ROOT = ROOT / "okf"
TODAY = dt.date(2026, 8, 19)


@pytest.fixture(scope="session", autouse=True)
def offline_by_default():  # type: ignore[no-untyped-def]
    """Pin the suite to the deterministic path, whatever the machine is configured for.

    `Settings` reads `.env`, so the moment a real key lands there the tests stop
    being tests: `test_pipeline_e2e` runs 610 cases through the full loop, and
    with a provider configured that is three API calls each — eighteen hundred
    billed requests, minutes of wall clock, and results that depend on a
    network. Environment variables win over `.env` in pydantic-settings, so
    setting them here restores the property the suite was built on: it runs
    offline, free, and identically on every machine.

    A test that wants the model layer injects its own provider, which is what
    the guardrail tests do.
    """
    import os

    pinned = {"LLM_PROVIDER": "deterministic", "GUARDRAILS": "rules"}
    saved = {key: os.environ.get(key) for key in pinned}
    os.environ.update(pinned)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(scope="session")
def settings():  # type: ignore[no-untyped-def]
    from api.settings import Settings

    return Settings(bundle_path=BUNDLE_ROOT)


@pytest.fixture(scope="session")
def bundle():  # type: ignore[no-untyped-def]
    from okf import Bundle

    return Bundle.load(BUNDLE_ROOT)


def make_session(  # type: ignore[no-untyped-def]
    channel=None,
    auth=None,
    policy_id: str | None = "TRV-100001",
    version: str = "2026.1",
    tier: str = "tier-2",
    today: dt.date = TODAY,
):
    from harness import AuthLevel, Channel, PolicyContext, Session

    policy = (
        PolicyContext(policy_id=policy_id, product_id="product/general/travel", version=version, tier=tier)
        if policy_id
        else None
    )
    return Session(
        session_id="test",
        channel=channel or Channel.direct,
        auth_level=auth or AuthLevel.authenticated,
        policy=policy,
        today=today,
    )
