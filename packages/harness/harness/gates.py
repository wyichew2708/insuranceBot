"""Verification gates (§F.2).

Deterministic checks between generation and delivery that do not involve the
model's opinion of its own work. An unbound figure is blocked outright — there
is no retry with "please be careful". Gates are pure functions over the answer
plus the evidence actually loaded, so every one of them is unit-testable.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from harness.contracts import Channel, GateResult, GroundedAnswer, Session, Verdict
from okf import Bundle, Status

# Currency amounts, percentages, quantities with a time unit, or any bare
# multi-digit number. A hallucinated "4 hours" must be caught as surely as a
# hallucinated limit.
NUMERIC_SPAN_RE = re.compile(
    r"(?:S?\$\s?\d[\d,]*(?:\.\d+)?)"
    r"|(?:\b\d+(?:\.\d+)?\s?%)"
    r"|(?:\b\d+(?:\.\d+)?\s+(?:hours?|days?|weeks?|months?|years?))"
    r"|(?:\b\d[\d,]{1,}\b)"
)

COVERAGE_ASSERTION_RE = re.compile(
    r"\b(is covered|are covered|you are covered|covers|cover applies|reimbursed|"
    r"we (?:will )?pay|benefit is payable|payable)\b",
    re.IGNORECASE,
)

ADVICE_SEEKING_RE = re.compile(
    r"\b(should i (?:buy|get|take|choose)|which (?:plan|policy|one) (?:is best|should i)|"
    r"what do you recommend|recommend (?:a|the|me)|best (?:plan|policy) for me|"
    r"is (?:this|it) suitable|worth (?:it|buying)|better (?:for|than) me)\b",
    re.IGNORECASE,
)

STOPWORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "that",
    "this",
    "it",
    "as",
    "at",
    "by",
    "from",
    "your",
    "you",
    "we",
    "our",
    "any",
    "not",
    "no",
    "up",
    "per",
    "if",
    "which",
    "there",
    "their",
    "has",
    "have",
}


@dataclass
class GateContext:
    answer: GroundedAnswer
    bundle: Bundle
    session: Session
    question: str = ""
    loaded_page_ids: list[str] = field(default_factory=list)
    raw_root: Path | None = None
    today: dt.date = field(default_factory=dt.date.today)

    def loaded_text(self) -> str:
        parts = []
        for page_id in self.loaded_page_ids:
            page = self.bundle.get(page_id)
            if page:
                parts.append(page.body)
        return "\n".join(parts)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


# --- 1. reference integrity -------------------------------------------------


def gate_reference_integrity(ctx: GateContext) -> GateResult:
    name = "reference-integrity"
    if not ctx.answer.claims:
        if ctx.answer.handoff:
            return GateResult(gate=name, verdict=Verdict.skip, detail="handoff carries no claims")
        return GateResult(gate=name, verdict=Verdict.fail, detail="factual answer with no claims")
    problems: list[str] = []
    for claim in ctx.answer.claims:
        source = claim.source_id
        page = ctx.bundle.get(source)
        if page is not None:
            if page.frontmatter.status != Status.approved:
                problems.append(f"{source} is {page.frontmatter.status.value}, not approved")
            elif not page.frontmatter.is_effective_on(ctx.today):
                problems.append(f"{source} is outside its effective window")
            continue
        if source.startswith("raw/"):
            if ctx.raw_root is not None and not (ctx.raw_root / source[len("raw/") :]).exists():
                problems.append(f"raw locator {source} does not exist")
            continue
        problems.append(f"{source} resolves to nothing")
    if problems:
        return GateResult(gate=name, verdict=Verdict.fail, detail="; ".join(problems))
    return GateResult(gate=name, verdict=Verdict.pass_, detail=f"{len(ctx.answer.claims)} claims resolve")


# --- 2. numeric binding -----------------------------------------------------


def gate_numeric_binding(ctx: GateContext) -> GateResult:
    name = "numeric-binding"
    unbound = [f.label for f in ctx.answer.figures if not f.is_bound]
    if unbound:
        return GateResult(gate=name, verdict=Verdict.fail, detail=f"unbound figures: {unbound}")

    for figure in ctx.answer.figures:
        if not figure.page_ref or figure.table_row_id or figure.sor_field:
            continue
        page = ctx.bundle.get(figure.page_ref)
        if page is None or page.frontmatter.type.value != "promotion":
            return GateResult(
                gate=name,
                verdict=Verdict.fail,
                detail=f"{figure.label!r} binds to {figure.page_ref!r}, which is not a promotion page",
            )
        if not page.frontmatter.is_effective_on(ctx.today):
            return GateResult(
                gate=name,
                verdict=Verdict.fail,
                detail=f"{figure.label!r} cites expired promotion {figure.page_ref!r}",
            )

    bound_text = [f.text for f in ctx.answer.figures if f.is_bound]
    # Contact details and deep links are substituted by the renderer from the
    # channel binding, not produced by the model, so digits inside them are
    # bound by construction (§C.4).
    render = ctx.answer.channel_render
    if render is not None:
        bound_text.extend(v for v in (render.hotline, render.landing) if v)
    for binding in _channel_values(ctx):
        bound_text.append(binding)

    orphans: list[str] = []
    for match in NUMERIC_SPAN_RE.finditer(ctx.answer.answer):
        span = match.group().strip()
        if not any(span in text or text in span for text in bound_text):
            orphans.append(span)
    if orphans:
        return GateResult(
            gate=name,
            verdict=Verdict.fail,
            detail=f"numbers in answer not bound to a table row or SOR field: {sorted(set(orphans))}",
        )
    return GateResult(gate=name, verdict=Verdict.pass_, detail=f"{len(ctx.answer.figures)} figures bound")


# --- 3. version coherence ---------------------------------------------------


def gate_version_coherence(ctx: GateContext) -> GateResult:
    name = "version-coherence"
    policy = ctx.session.policy
    cited_pages = [ctx.bundle.get(c.source_id) for c in ctx.answer.claims]
    versions = {
        p.frontmatter.version_in_force
        for p in cited_pages
        if p is not None and p.frontmatter.version_in_force
    }
    if len(versions) > 1:
        return GateResult(
            gate=name, verdict=Verdict.fail, detail=f"cited pages mix versions {sorted(versions)}"
        )
    if policy is None:
        return GateResult(gate=name, verdict=Verdict.skip, detail="no in-force policy in session")
    if versions and policy.version not in versions:
        # The wiki describes what is on sale; a customer on an older version
        # must be answered from that version's wording (§B.2, §E).
        return GateResult(
            gate=name,
            verdict=Verdict.fail,
            detail=f"policy is version {policy.version}, answer cites {sorted(versions)}",
        )
    return GateResult(gate=name, verdict=Verdict.pass_, detail=f"version {policy.version} coherent")


# --- 4. channel coherence ---------------------------------------------------


def gate_channel_coherence(ctx: GateContext) -> GateResult:
    name = "channel-coherence"
    render = ctx.answer.channel_render
    session_channel = ctx.session.channel
    if render is None:
        return GateResult(gate=name, verdict=Verdict.skip, detail="no channel render on this answer")
    if render.channel != session_channel:
        return GateResult(
            gate=name,
            verdict=Verdict.fail,
            detail=f"rendered {render.channel.value} for session {session_channel.value}",
        )
    if session_channel == Channel.unknown:
        return GateResult(gate=name, verdict=Verdict.pass_, detail="no channel; both routes offered")

    other = ctx.bundle.get(
        Channel.etiqa_sg.value if session_channel == Channel.tiq_sg else Channel.tiq_sg.value
    )
    if other is not None:
        foreign = (
            str(other.frontmatter.model_extra.get("landing", "")) if other.frontmatter.model_extra else ""
        )
        hotline = (
            str(other.frontmatter.model_extra.get("hotline", "")) if other.frontmatter.model_extra else ""
        )
        for value in (foreign, hotline):
            if value and value in ctx.answer.answer:
                return GateResult(
                    gate=name, verdict=Verdict.fail, detail=f"answer leaks other channel's {value!r}"
                )
    return GateResult(gate=name, verdict=Verdict.pass_, detail=f"channel {session_channel.value}")


# --- 5. exclusion completeness ---------------------------------------------


def gate_exclusion_completeness(ctx: GateContext) -> GateResult:
    name = "exclusion-completeness"
    if ctx.answer.handoff or not COVERAGE_ASSERTION_RE.search(ctx.answer.answer):
        return GateResult(gate=name, verdict=Verdict.skip, detail="no coverage asserted")
    loaded = set(ctx.loaded_page_ids)
    missing: list[str] = []
    for page_id in ctx.loaded_page_ids:
        page = ctx.bundle.get(page_id)
        if page is None or page.frontmatter.type.value != "product":
            continue
        exclusions = page.frontmatter.links.exclusions
        if exclusions and exclusions not in loaded:
            missing.append(exclusions)
    if missing:
        return GateResult(
            gate=name,
            verdict=Verdict.fail,
            detail=f"coverage asserted without reading {sorted(set(missing))}",
        )
    return GateResult(gate=name, verdict=Verdict.pass_, detail="exclusion pages read")


# --- 6. advice boundary -----------------------------------------------------


def gate_advice_boundary(ctx: GateContext) -> GateResult:
    name = "advice-boundary"
    classifier_fired = bool(ADVICE_SEEKING_RE.search(ctx.question))
    regulated = any(
        (page := ctx.bundle.get(pid)) is not None and page.frontmatter.regulated_advice
        for pid in ctx.loaded_page_ids
    )
    if not classifier_fired and not regulated:
        return GateResult(gate=name, verdict=Verdict.pass_, detail="factual question, unregulated product")
    if ctx.answer.advice_flag:
        return GateResult(
            gate=name,
            verdict=Verdict.pass_,
            detail="advice boundary hit; flagged for adviser handoff",
        )
    return GateResult(
        gate=name,
        verdict=Verdict.fail,
        detail="advice sought or regulated product, but advice_flag is not set",
    )


# --- 7. groundedness --------------------------------------------------------


def gate_groundedness(ctx: GateContext, threshold: float = 0.6) -> GateResult:
    """Claim verification against the pages actually loaded. Lexical-overlap
    entailment by default; swap in an NLI model where one is configured."""
    name = "groundedness"
    if ctx.answer.handoff and not ctx.answer.claims:
        return GateResult(gate=name, verdict=Verdict.skip, detail="handoff")
    evidence = _tokens(ctx.loaded_text())
    if not evidence:
        return GateResult(gate=name, verdict=Verdict.fail, detail="no evidence pages were loaded")
    weak: list[str] = []
    scores: list[float] = []
    for claim in ctx.answer.claims:
        claim_tokens = _tokens(claim.text)
        if not claim_tokens:
            continue
        score = len(claim_tokens & evidence) / len(claim_tokens)
        scores.append(score)
        if score < threshold:
            weak.append(f"{claim.text[:50]!r} ({score:.2f})")
    mean = sum(scores) / len(scores) if scores else 0.0
    if weak:
        return GateResult(
            gate=name, verdict=Verdict.fail, detail=f"claims not entailed by loaded pages: {weak}"
        )
    return GateResult(gate=name, verdict=Verdict.pass_, detail=f"mean entailment {mean:.2f}")


def _channel_values(ctx: GateContext) -> list[str]:
    """Every landing URL and hotline declared on the loaded product pages."""
    values: list[str] = []
    for page_id in ctx.loaded_page_ids:
        page = ctx.bundle.get(page_id)
        if page is None:
            continue
        for binding in page.frontmatter.channels:
            values.append(binding.landing)
            if binding.hotline:
                values.append(binding.hotline)
    return values


ALL_GATES = [
    gate_reference_integrity,
    gate_numeric_binding,
    gate_version_coherence,
    gate_channel_coherence,
    gate_exclusion_completeness,
    gate_advice_boundary,
    gate_groundedness,
]


def run_gates(ctx: GateContext) -> list[GateResult]:
    """All seven run regardless of earlier failures — the debug console shows
    the full picture, and partial verdicts hide root causes."""
    return [gate(ctx) for gate in ALL_GATES]


def blocked(results: list[GateResult]) -> bool:
    return any(r.blocking for r in results)
