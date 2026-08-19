"""System-of-record tools (§E.1 step 5).

Customer-specific data never lives in the wiki (§C.5) — it comes from here at
runtime, behind an entitlement predicate. This is a fixture implementation; a
deployment swaps the body for the real policy-admin call and keeps the shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness import AuthLevel, Session


class NotEntitled(PermissionError):
    pass


@dataclass(frozen=True)
class PolicySummary:
    policy_id: str
    product_id: str
    version: str
    tier: str
    in_force: bool

    def as_fields(self) -> dict[str, str]:
        return {
            "policy.tier": self.tier,
            "policy.version": self.version,
            "policy.in_force": str(self.in_force).lower(),
        }


# Fixture policies for development and the eval suites. There is one per
# (product, version, tier) the benefit tables define, so the generated FAQ
# suite can exercise every tier-varying figure.
FIXTURE_POLICIES: dict[str, PolicySummary] = {
    "TRV-100003": PolicySummary("TRV-100003", "product/general/travel", "2026.1", "tier-1", True),
    "TRV-100001": PolicySummary("TRV-100001", "product/general/travel", "2026.1", "tier-2", True),
    "TRV-100002": PolicySummary("TRV-100002", "product/general/travel", "2026.1", "tier-3", True),
    "TRV-900001": PolicySummary("TRV-900001", "product/general/travel", "2025.2", "tier-2", True),
    "HOM-100001": PolicySummary("HOM-100001", "product/general/home", "2026.1", "ALL", True),
    "CAR-100001": PolicySummary("CAR-100001", "product/motor/private-car", "2026.1", "ALL", True),
}


def policy_for(product_key: str, version: str, tier: str) -> PolicySummary | None:
    """Find the fixture policy matching a table coordinate, so a generated
    question about a tier-specific figure runs under a session that holds it."""
    for summary in FIXTURE_POLICIES.values():
        if (
            summary.product_id.rsplit("/", 1)[-1] == product_key
            and summary.version == version
            and summary.tier == tier
        ):
            return summary
    return None


def policy_summary(session: Session) -> PolicySummary:
    """Entitlement predicate: only an authenticated session may read a policy,
    and only its own."""
    if session.auth_level != AuthLevel.authenticated or session.policy is None:
        raise NotEntitled("policy_summary requires an authenticated session with a bound policy")
    summary = FIXTURE_POLICIES.get(session.policy.policy_id)
    if summary is None:
        raise NotEntitled(f"policy {session.policy.policy_id} is not readable by this session")
    return summary
