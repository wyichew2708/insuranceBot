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
from typing import Any

from harness.contracts import Channel, Figure, GateResult, GroundedAnswer, Session, Verdict
from harness.intent import REQUIREMENTS, classify
from okf import ALL_CHANNELS, Bundle, Status, spec_for

# Currency amounts, percentages, quantities with a time unit, or any bare
# multi-digit number. A hallucinated "4 hours" must be caught as surely as a
# hallucinated limit.
NUMERIC_SPAN_RE = re.compile(
    r"(?:S?\$\s?\d[\d,]*(?:\.\d+)?)"
    r"|(?:\b\d+(?:\.\d+)?\s?%)"
    r"|(?:\b\d+(?:\.\d+)?\s+(?:hours?|days?|weeks?|months?|years?))"
    r"|(?:\b\d[\d,]{1,}\b)"
)

#: Raw documents read by the quotation check, keyed by resolved path. A wording
#: is megabytes of text and an answer may quote it several times.
_QUOTE_CACHE: dict[str, str] = {}

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
        if ctx.answer.smalltalk:
            return GateResult(gate=name, verdict=Verdict.skip, detail="greeting carries no claims")
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
        if figure.quote_ref:
            # Claiming a quotation is not enough — the gate goes and reads it.
            # Without this check `quote_ref` would be a way to assert any
            # number at all, which is precisely what the gate exists to stop.
            if not _quote_holds(ctx, figure):
                return GateResult(
                    gate=name,
                    verdict=Verdict.fail,
                    detail=f"{figure.text!r} is quoted from {figure.quote_ref!r}, which does not contain it",
                )
            continue
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
        bound_text.extend(render.surfaces)
        # Every front door of the rendered channel, from the registry. A route
        # with two addresses has two contact numbers, and both are structural
        # values the renderer substitutes — not figures the model invented.
        # With no route in the session every route is on offer, so every
        # route's contacts are renderer-substituted too.
        specs = ALL_CHANNELS if render.channel is Channel.unknown else [spec_for(render.channel)]
        for spec in specs:
            if spec is not None:
                bound_text.extend(spec.contact_values())
    bound_text.extend(_channel_values(ctx))

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


def _source_digits(ctx: GateContext, locator: str) -> str | None:
    """A raw document reduced to its figures, cached across gate runs.

    Punctuation and spacing go: an extractor writes `S$ 1,000` where the PDF
    printed `S$1,000`, and a comparison that failed on a thousands separator
    would reject correct quotations for a reason no one could act on.
    """
    if ctx.raw_root is None:
        return None
    path_part = locator.split("#", 1)[0]
    if not path_part.startswith("raw/"):
        return None
    path = ctx.raw_root / path_part[len("raw/") :]
    # Keyed by the resolved path, not the locator: an eval run loads several
    # bundles in one process and they all call their wordings `travel.md`.
    key = str(path)
    cached = _QUOTE_CACHE.get(key)
    if cached is None:
        if not path.is_file():
            return ""
        cached = re.sub(r"[^0-9a-z%$]+", "", path.read_text(encoding="utf-8", errors="replace").lower())
        _QUOTE_CACHE[key] = cached
    return cached


def _quote_holds(ctx: GateContext, figure: Figure) -> bool:
    """Does the cited document actually contain this figure?

    What this catches is a *page* that misquotes its source — a compile bug, a
    hand-edited wiki page, a locator pointing at the wrong document. It is not
    what stops a model inventing a number: that is the orphan scan below,
    which requires every numeric span in the answer to have come from a figure
    the composer lifted out of a page. Note the asymmetry in strength — a
    currency amount or a percentage is near-unique in a wording, while a bare
    two-digit number will be found somewhere in any document long enough.
    """
    source = _source_digits(ctx, figure.quote_ref or "")
    if source is None:
        # No raw root to check against. The bundle is the only thing loaded,
        # so the quotation cannot be verified — and an unverifiable claim of
        # verbatimness is not a binding.
        return False
    needle = re.sub(r"[^0-9a-z%$]+", "", figure.text.lower())
    return bool(needle) and needle in source


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
    """The rendered route must match the session's route, and the answer must
    not hand the customer a *different distribution channel's* contact.

    Surfaces of the same channel are interchangeable. etiqa.com.sg and
    tiq.com.sg are both the direct channel, so citing either one in a direct
    session is correct, not a leak — a customer starts from the product and
    never has to know which front door they arrived through.
    """
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
        return GateResult(gate=name, verdict=Verdict.pass_, detail="no channel; every route offered")

    present = _urls_in(ctx.answer.answer)
    for value, owner in _foreign_contacts(ctx, session_channel):
        if not value:
            continue
        # A URL matches on the whole address, never on a prefix: the agency
        # route lives under the direct route's domain, and
        # ".../find-an-agent/" is not an offer of the site root.
        hit = _norm_url(value) in present if _is_url(value) else value in ctx.answer.answer
        if hit:
            return GateResult(
                gate=name,
                verdict=Verdict.fail,
                detail=f"answer offers {owner}'s {value!r} to a {session_channel.value} session",
            )
    return GateResult(gate=name, verdict=Verdict.pass_, detail=f"channel {session_channel.value}")


# --- 5. exclusion completeness ---------------------------------------------


def gate_exclusion_completeness(ctx: GateContext) -> GateResult:
    name = "exclusion-completeness"
    if ctx.answer.handoff or not COVERAGE_ASSERTION_RE.search(ctx.answer.answer):
        return GateResult(gate=name, verdict=Verdict.skip, detail="no coverage asserted")
    loaded = set(ctx.loaded_page_ids)
    missing: list[str] = []
    # Scope to what the answer cites: asserting coverage for product X requires
    # having read X's exclusions, not those of every page that happened to load.
    cited = {c.source_id for c in ctx.answer.claims} or loaded
    for page_id in cited:
        for candidate in _product_chain(ctx, page_id):
            exclusions = candidate.frontmatter.links.exclusions
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
    if ctx.answer.smalltalk:
        # A greeting is entailed by nothing because it asserts nothing. The
        # gate below would fail it for loading no evidence pages, which is
        # true and beside the point.
        return GateResult(gate=name, verdict=Verdict.skip, detail="greeting asserts nothing")
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


def _product_chain(ctx: GateContext, page_id: str) -> list[Any]:
    """The cited page and its product ancestor, whichever declares exclusions."""
    chain = []
    parts = page_id.split("/")
    for i in range(len(parts), 0, -1):
        candidate = ctx.bundle.get("/".join(parts[:i]))
        if candidate is not None and candidate.frontmatter.type.value == "product":
            chain.append(candidate)
    return chain


URL_RE = re.compile(r"https?://[^\s,;)\]<>\"']+")


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _norm_url(value: str) -> str:
    return value.rstrip("/.,;:").lower()


def _urls_in(text: str) -> set[str]:
    return {_norm_url(match.group()) for match in URL_RE.finditer(text)}


def _channel_values(ctx: GateContext) -> list[str]:
    """Every landing URL and hotline declared on the loaded pages, across all
    routes. These are renderer-substituted, so digits in them are bound."""
    values: list[str] = []
    for page_id in ctx.loaded_page_ids:
        page = ctx.bundle.get(page_id)
        if page is None:
            continue
        for binding in page.frontmatter.channels:
            values.extend(binding.landings)
            if binding.hotline:
                values.append(binding.hotline)
        # A channel page declares its own route in frontmatter rather than in a
        # binding, and the renderer substitutes from there too.
        extra = page.frontmatter.model_extra or {}
        values.extend(str(extra[key]) for key in ("landing", "hotline") if extra.get(key))
        values.extend(str(surface) for surface in (extra.get("surfaces") or []))
    return values


def _own_contacts(ctx: GateContext, channel: Channel) -> set[str]:
    """Every landing URL and hotline that legitimately belongs to `channel`,
    from the bundle if it describes the route, else from the registry."""
    values: set[str] = set(spec.contact_values()) if (spec := spec_for(channel)) else set()
    page = ctx.bundle.get(channel.value)
    if page is not None and page.frontmatter.model_extra:
        extra = page.frontmatter.model_extra
        for key in ("landing", "hotline"):
            if extra.get(key):
                values.add(str(extra[key]))
        for surface in extra.get("surfaces", []) or []:
            values.add(str(surface))
    # A product page may bind this route with its own deep link.
    for page_id in ctx.loaded_page_ids:
        loaded = ctx.bundle.get(page_id)
        if loaded is None:
            continue
        for binding in loaded.frontmatter.channels:
            if binding.ref == channel.value:
                values.update(binding.landings)
                if binding.hotline:
                    values.add(binding.hotline)
    return {v for v in values if v}


def _foreign_contacts(ctx: GateContext, channel: Channel) -> list[tuple[str, str]]:
    """(contact value, owning channel) for every *other* distribution channel.

    Anything the session's own channel also publishes is excluded — the routes
    share a corporate hotline, and a shared number is not a leak.
    """
    own = _own_contacts(ctx, channel)
    own_norm = {_norm_url(v) if _is_url(v) else v for v in own}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(value: str, owner: str) -> None:
        key = _norm_url(value) if _is_url(value) else value
        if value and key not in own_norm and key not in seen:
            seen.add(key)
            out.append((value, owner))

    for other in Channel:
        if other in (channel, Channel.unknown):
            continue
        if spec := spec_for(other):
            for value in spec.contact_values():
                add(value, other.value)
        page = ctx.bundle.get(other.value)
        if page is not None and page.frontmatter.model_extra:
            extra = page.frontmatter.model_extra
            for key in ("landing", "hotline"):
                if extra.get(key):
                    add(str(extra[key]), other.value)
            for surface in extra.get("surfaces", []) or []:
                add(str(surface), other.value)
    # Bindings declared on the loaded product pages, which may carry deep links
    # the channel page itself does not.
    for page_id in ctx.loaded_page_ids:
        loaded = ctx.bundle.get(page_id)
        if loaded is None:
            continue
        for binding in loaded.frontmatter.channels:
            if binding.ref == channel.value:
                continue
            for landing in binding.landings:
                add(landing, binding.ref)
            if binding.hotline:
                add(binding.hotline, binding.ref)
    return out


# --- 8. answerability -------------------------------------------------------


def gate_answerability(ctx: GateContext) -> GateResult:
    """Does this answer address what was asked?

    The other seven gates are provenance checks. Every one of them passes on an
    answer about travel-delay thresholds given to a customer who asked what the
    policy costs a year, because the thresholds really did come from a page we
    really did load. Measured on the real corpus, that is the largest failure
    class there is: of 3,130 failing cases, 1,177 were answered when nothing in
    the corpus could answer them.

    So this gate compares the question's intent against what the answer shows.
    It is deliberately hard to trip:

      * an unrecognised intent passes — most questions are broad, and refusing
        one for being broad is worse than answering it broadly;
      * `coverage` and `definition` carry no requirement at all;
      * a handoff passes, because refusing is already the safe outcome;
      * and a requirement is satisfied by *any* of its clauses, not all.

    What is left is the narrow case worth refusing: the customer asked for a
    limit and the answer carries no bound figure, asked how to claim and nothing
    cited is a claims page, asked the price of a policy whose price is nowhere
    in the corpus. In those the honest answer is that we do not know, and the
    expensive mistake is the fluent paragraph about something adjacent.
    """
    name = "answerability"
    if ctx.answer.handoff:
        return GateResult(gate=name, verdict=Verdict.skip, detail="handoff")
    if ctx.answer.smalltalk:
        return GateResult(gate=name, verdict=Verdict.skip, detail="not a question about the product")

    intent = classify(ctx.question)
    requirement = REQUIREMENTS.get(intent)
    if requirement is None:
        return GateResult(gate=name, verdict=Verdict.skip, detail=f"{intent.value}: unconstrained")

    text = ctx.answer.answer.lower()
    cited = [c.source_id for c in ctx.answer.claims]

    if requirement.needs_figure and any(f.is_bound for f in ctx.answer.figures):
        return GateResult(gate=name, verdict=Verdict.pass_, detail=f"{intent.value}: bound figure")
    if requirement.satisfied_by_unresolved and ctx.answer.unresolved:
        return GateResult(
            gate=name, verdict=Verdict.pass_, detail=f"{intent.value}: accounted for as unresolved"
        )
    if requirement.needs_page_suffix and any(
        page_id.endswith(requirement.needs_page_suffix) for page_id in cited
    ):
        return GateResult(gate=name, verdict=Verdict.pass_, detail=f"{intent.value}: cited the right page")
    if requirement.needs_page_type:
        for page_id in cited:
            page = ctx.bundle.get(page_id)
            if page is not None and page.frontmatter.type.value in requirement.needs_page_type:
                return GateResult(
                    gate=name,
                    verdict=Verdict.pass_,
                    detail=f"{intent.value}: cited a {page.frontmatter.type.value} page",
                )
    if requirement.needs_any_term and any(term in text for term in requirement.needs_any_term):
        return GateResult(gate=name, verdict=Verdict.pass_, detail=f"{intent.value}: on subject")
    # A page that declares it answers this intent settles the question without
    # the answer having to use any particular word. This is what the compiled
    # `faq_intents` are for: a published eligibility answer reads "Singaporean,
    # PR, Work Pass holder" and contains none of "eligible", "age" or "qualify",
    # and refusing it for that would be refusing the insurer's own answer.
    for page_id in cited:
        page = ctx.bundle.get(page_id)
        declared = (page.frontmatter.model_extra or {}).get("faq_intents") if page else None
        if declared and intent.value in declared:
            return GateResult(
                gate=name, verdict=Verdict.pass_, detail=f"{intent.value}: cited a page that answers it"
            )

    return GateResult(
        gate=name,
        verdict=Verdict.fail,
        detail=f"asked for {intent.value}; the answer shows none of it",
    )


ALL_GATES = [
    gate_reference_integrity,
    gate_numeric_binding,
    gate_version_coherence,
    gate_channel_coherence,
    gate_exclusion_completeness,
    gate_advice_boundary,
    gate_groundedness,
    gate_answerability,
]


def run_gates(ctx: GateContext) -> list[GateResult]:
    """All of them run regardless of earlier failures — the debug console shows
    the full picture, and partial verdicts hide root causes."""
    return [gate(ctx) for gate in ALL_GATES]


def blocked(results: list[GateResult]) -> bool:
    return any(r.blocking for r in results)
