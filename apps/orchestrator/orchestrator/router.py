"""Deterministic-first intent router (§8.1). First match wins.

The emergency route MUST short-circuit before any retrieval or model call
(hard product rule 5). The servicing classifier is model-backed; when no
agent endpoint is configured it falls through deterministically.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel


class Route(str, Enum):
    emergency = "emergency"
    servicing = "servicing"
    discovery = "discovery"
    coverage_qa = "coverage_qa"
    out_of_scope = "out_of_scope"


class RouteDecision(BaseModel):
    route: Route
    intent: str | None = None
    product_code: str | None = None
    confidence: float = 1.0


# Overseas-emergency signals: an ongoing incident, not a hypothetical coverage question.
_EMERGENCY_PATTERNS = [
    r"\bemergency\b.*\b(overseas|abroad|now|help)\b",
    r"\b(overseas|abroad)\b.*\bemergency\b",
    r"\b(i am|i'm|we are|we're)\b.*\b(hospitalised|hospitalized|in hospital)\b",
    r"\b(been in|had|met with) an accident\b.*\b(overseas|abroad|in \w+)\b",
    r"\bmedical evacuation\b",
    r"\bneed (an ambulance|urgent medical)\b",
    r"\b(passport|wallet) (was |got |has been )?stolen\b.*\b(overseas|abroad|in \w+)\b",
]
_EMERGENCY_RE = [re.compile(p, re.IGNORECASE) for p in _EMERGENCY_PATTERNS]

# Servicing keyword prefilter; the model classifier refines this (Phase 3).
_SERVICING_RE = re.compile(
    r"\b(cancel|renew|update|change|amend|nominate|nomination|surrender|withdraw|"
    r"claim status|make a claim|submit a claim|file a claim|change my address|"
    r"update my (address|phone|email|bank)|giro|payment method|reinstate)\b",
    re.IGNORECASE,
)

_COMPARISON_RE = re.compile(
    r"\b(compare|difference between|which (plan|one) (is|should)|vs\.?|versus|"
    r"what plans|which plans|recommend a plan)\b",
    re.IGNORECASE,
)

_SMALLTALK_RE = re.compile(
    r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks?( you)?|bye|goodbye|ok(ay)?)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def is_emergency(message: str) -> bool:
    return any(p.search(message) for p in _EMERGENCY_RE)


def route_message(message: str) -> RouteDecision:
    """Deterministic routing. Model-backed servicing classification refines
    the servicing route later; ordering here is the product contract."""
    if is_emergency(message):
        return RouteDecision(route=Route.emergency)
    if _SMALLTALK_RE.match(message):
        return RouteDecision(route=Route.out_of_scope)
    if _SERVICING_RE.search(message):
        return RouteDecision(route=Route.servicing, confidence=0.8)
    if _COMPARISON_RE.search(message):
        return RouteDecision(route=Route.discovery)
    return RouteDecision(route=Route.coverage_qa)
