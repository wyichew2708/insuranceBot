"""Rule graders (§8.4). Cheap, ordered, pure — every verdict is auditable.

Graders operate on a Draft plus the evidence the agent actually retrieved,
so they are unit-testable without any service running.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class Draft(BaseModel):
    """Candidate answer awaiting verification."""

    text: str
    citations: list[str] = Field(default_factory=list)  # chunk/block ids the answer cites
    action_ids: list[str] = Field(default_factory=list)
    route: str = "coverage_qa"
    is_factual: bool = True
    is_product_benefit_answer: bool = False


@dataclass
class Evidence:
    """What the session is allowed to ground on."""

    cited_texts: dict[str, str] = field(default_factory=dict)  # chunk_id -> text
    cited_audiences: dict[str, str] = field(default_factory=dict)  # chunk_id -> audience
    action_values: dict[str, str] = field(default_factory=dict)  # action_id -> exact value
    permitted_chunk_ids: set[str] = field(default_factory=set)
    session_audience: str = "public"
    # promo evidence: chunk_id -> (expires_at, accurate_as_of)
    promo_windows: dict[str, tuple[dt.datetime | None, dt.date | None]] = field(default_factory=dict)
    today: dt.date = field(default_factory=dt.date.today)


@dataclass
class GraderResult:
    name: str
    passed: bool
    reason: str = ""


# --- extraction helpers -----------------------------------------------------

PHONE_RE = re.compile(r"\+?\d[\d\s\-]{6,}\d")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SWIFT_RE = re.compile(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b")
_SWIFT_CONTEXT_RE = re.compile(r"\b(swift|bic)\b", re.IGNORECASE)
ACCOUNT_RE = re.compile(r"\b\d{3}-\d{4,}-\d+\b|\b\d{9,17}\b")


def _looks_like_swift(match: re.Match[str], text: str) -> bool:
    """The bare pattern matches any 8-letter uppercase word ("COVERAGE",
    "OVERSEAS"); only treat it as a SWIFT code when it contains a digit or
    appears near a SWIFT/BIC mention."""
    token = match.group()
    if any(ch.isdigit() for ch in token):
        return True
    window = text[max(0, match.start() - 30) : match.end() + 30]
    return bool(_SWIFT_CONTEXT_RE.search(window))


def extract_verbatim_tokens(text: str) -> list[str]:
    """Phone numbers, bank accounts, SWIFT codes, emails found in a draft."""
    tokens: list[str] = []
    tokens += [m.group().strip() for m in PHONE_RE.finditer(text)]
    tokens += [m.group() for m in EMAIL_RE.finditer(text)]
    tokens += [m.group() for m in SWIFT_RE.finditer(text) if _looks_like_swift(m, text)]
    tokens += [m.group() for m in ACCOUNT_RE.finditer(text)]
    return tokens


# --- graders, in evaluation order (§8.4) ------------------------------------


def grade_citation_presence(draft: Draft, ev: Evidence) -> GraderResult:
    name = "citation-presence"
    if not draft.is_factual:
        return GraderResult(name, True, "non-factual answer")
    if not draft.citations:
        return GraderResult(name, False, "factual answer without any citation")
    unknown = [c for c in draft.citations if c not in ev.permitted_chunk_ids]
    if unknown:
        return GraderResult(name, False, f"citations not permitted for this session: {unknown}")
    return GraderResult(name, True)


def grade_verbatim_digits(draft: Draft, ev: Evidence) -> GraderResult:
    """Every phone/account/SWIFT/email string must appear character-exact in a
    cited source or a registered action value (hard product rule 2)."""
    name = "verbatim-digits"
    sources = list(ev.cited_texts.values()) + list(ev.action_values.values())
    for token in extract_verbatim_tokens(draft.text):
        if not any(token in src for src in sources):
            return GraderResult(name, False, f"token {token!r} not verbatim in any cited source")
    return GraderResult(name, True)


def grade_audience_leak(draft: Draft, ev: Evidence) -> GraderResult:
    """Belt-and-braces after the SQL filter (hard product rule 6)."""
    name = "audience-leak"
    if ev.session_audience == "internal":
        return GraderResult(name, True, "internal session")
    leaked = [c for c in draft.citations if ev.cited_audiences.get(c) == "internal"]
    if leaked:
        return GraderResult(name, False, f"internal blocks cited in public session: {leaked}")
    return GraderResult(name, True)


PROMO_SIGNAL_RE = re.compile(r"\b\d{1,2}\s?%\s?(off|discount)|\bpromo\s?code\b|\buse code\b", re.IGNORECASE)


def grade_promo_freshness(draft: Draft, ev: Evidence) -> GraderResult:
    """Any %/code claim must trace to a live web chunk with a valid window
    (hard product rule 4)."""
    name = "promo-freshness"
    if not PROMO_SIGNAL_RE.search(draft.text):
        return GraderResult(name, True, "no promo claims")
    live = []
    for chunk_id in draft.citations:
        window = ev.promo_windows.get(chunk_id)
        if window is None:
            continue
        expires_at, _ = window
        if expires_at is None or expires_at.date() >= ev.today:
            live.append(chunk_id)
    if not live:
        return GraderResult(name, False, "promo claim without a live, in-window web citation")
    return GraderResult(name, True)


EXECUTION_CLAIM_RE = re.compile(
    r"\bI(?:'ve| have| ve)?\s+(?:already\s+)?"
    r"(updated|cancelled|canceled|submitted|changed|processed|renewed|amended|filed|terminated)\b"
    r"|\byour (?:policy|request|claim) (?:has been|is now|was) "
    r"(updated|cancelled|canceled|submitted|changed|processed|renewed|amended|terminated)\b",
    re.IGNORECASE,
)


def grade_no_execution_claims(draft: Draft, ev: Evidence) -> GraderResult:
    """The bot never claims to execute policy changes (hard product rule 3)."""
    name = "no-execution-claims"
    m = EXECUTION_CLAIM_RE.search(draft.text)
    if m:
        return GraderResult(name, False, f"execution claim found: {m.group()!r}")
    return GraderResult(name, True)


DISCLAIMER_MARKER = "[disclaimer]"


def grade_disclaimer_attach(draft: Draft, ev: Evidence) -> GraderResult:
    """Product benefit answers must carry the standard disclaimer block exactly once."""
    name = "disclaimer-attach"
    if not draft.is_product_benefit_answer:
        return GraderResult(name, True, "not a benefit answer")
    count = draft.text.count(DISCLAIMER_MARKER)
    if count == 1:
        return GraderResult(name, True)
    return GraderResult(name, False, f"disclaimer marker count {count}, expected exactly 1")


ADVICE_RE = re.compile(
    r"\b(you should (buy|invest|choose)|best (plan|investment) for you|"
    r"I recommend (buying|investing|the)|guaranteed returns)\b",
    re.IGNORECASE,
)
GET_ADVICE_MARKER = "[get-advice]"


def grade_advice_boundary(draft: Draft, ev: Evidence) -> GraderResult:
    """No financial advice for life/investment products (hard product rule 7)."""
    name = "advice-boundary"
    if ADVICE_RE.search(draft.text) and GET_ADVICE_MARKER not in draft.text:
        return GraderResult(name, False, "advice-like phrasing without Get Advice routing")
    return GraderResult(name, True)


ALL_GRADERS = [
    grade_citation_presence,
    grade_verbatim_digits,
    grade_audience_leak,
    grade_promo_freshness,
    grade_no_execution_claims,
    grade_disclaimer_attach,
    grade_advice_boundary,
]


def run_rule_graders(draft: Draft, ev: Evidence) -> list[GraderResult]:
    """Run all graders in order; short-circuits nothing so the audit log gets
    the full picture."""
    return [g(draft, ev) for g in ALL_GRADERS]


def verdict(results: list[GraderResult]) -> bool:
    return all(r.passed for r in results)
