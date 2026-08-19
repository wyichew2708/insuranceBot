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
        channel=channel or Channel.tiq_sg,
        auth_level=auth or AuthLevel.authenticated,
        policy=policy,
        today=today,
    )
