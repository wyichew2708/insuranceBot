"""Shared workflow extracted from the serve pipeline."""

from __future__ import annotations

import contextlib
import re
import threading
from pathlib import Path
from typing import Any

from harness import (
    AnswerEnvelope,
    Figure,
    GateContext,
    GroundedAnswer,
    Judge,
    Session,
    Trace,
    blocked,
    run_gates,
)
from harness.ask import Ask
from harness.contracts import Link
from harness.gates import NUMERIC_SPAN_RE
from harness.intent import Intent
from okf.tables import find_tokens

from api.guardrails import (
    Screening,
)
from api.settings import Settings
from api.suggest import suggest_next
from okf import (
    DESTINATIONS,
    Bundle,
    Desk,
    Page,
    PageType,
    landing_for,
)

#: The destination a refusal can always give, whatever went wrong. Built once
#: rather than per turn, and from the registry rather than from a literal, so
#: there is exactly one place the address is written down.
CONTACT_LINK = Link(
    label=DESTINATIONS[Desk.contact].label,
    url=DESTINATIONS[Desk.contact].url,
    desk=Desk.contact.value,
)

HANDOFF = (
    "I'd rather not answer that from memory. I'm passing you to a colleague who can "
    "confirm the details against your policy."
)

#: A turn the input screen refused. Deliberately says nothing about *why* — a
#: refusal that explains which rule it tripped is a probe's reward, and tells
#: the next attempt what to avoid.
#: "How much will I get back?" two turns after "I want to cancel X" is a
#: refund question — the customer's own money, which no product page carries —
#: and not the limit question its words make it on its own. Read with the
#: conversation, not the sentence.
REFUND_FOLLOWUP_RE = re.compile(
    r"\brefund|\b(?:get|getting|receive|receiving|have|having)\b[\w\s]{0,16}\bback\b|\bmoney back\b",
    re.I,
)
CANCEL_CONTEXT_RE = re.compile(r"\b(?:cancel|cancell?ation|terminate|surrender|free.look|cooling)\w*", re.I)
#: The age words an eligibility answer is allowed to keep its figures for.
AGE_RE = re.compile(r"\bage\b|\baged\b|\byears? old\b|\byears of age\b|\bentry age\b", re.I)
PRICED_LABELS = ("premium", "price", "cost")

REFUSED = (
    "I can't help with that one. If you have a question about a policy or a product, "
    "I'm happy to take it — otherwise I can put you through to a colleague."
)

#: A question that is not about insurance. Declines and says what is on offer,
#: rather than refusing flatly: a customer who wandered off topic is still a
#: customer, and the useful half of the reply is the redirect. Claimless and
#: marked `smalltalk`, because it asserts nothing about any product — the same
#: shape a greeting takes, and for the same reason.
OFF_TOPIC = (
    "That one's outside what I can help with, I'm afraid — I only answer from "
    "our policy documents. If there's something you'd like to know about your "
    "cover, a claim, or one of our products, ask away."
)


def _memoised(classify: Any) -> Any:
    """A judging callable that answers an identical (system, user) once."""
    memo: dict[tuple[str, str], Any] = {}
    lock = threading.Lock()

    def call(system: str, user: str, schema: dict[str, Any], **kw: Any) -> Any:
        key = (system, user)
        with lock:
            if key in memo:
                return memo[key]
        out = classify(system, user, schema, **kw)
        with lock:
            memo[key] = out
        return out

    return call


def _prewarm_judge(ctx: GateContext) -> None:
    """Run the entailment judge on the draft so the gate finds it memoised.

    Swallows everything: this runs off the request thread purely to warm a
    memo, and a fault here must cost the turn its head start and nothing else.
    The gate calls the judge itself in that case.
    """
    from harness.gates import _judge_entailment

    with contextlib.suppress(Exception):
        _judge_entailment(ctx)


def _refusal(trace: Trace, screening: Screening, gate: str, budget_note: str) -> tuple[AnswerEnvelope, Trace]:
    """End the turn on a guardrail verdict, by the same route a failed gate
    takes so the console, the trace and the eval harness need no special case."""
    trace.handler = "refusal"
    result = screening.as_gate(gate)
    trace.gates.append(result)
    trace.delivered = False
    trace.note(budget_note)
    envelope = AnswerEnvelope(
        answer=GroundedAnswer(
            answer=REFUSED,
            handoff=True,
            confidence=0.0,
            unresolved=[f"{f.category}: {f.detail}" for f in screening.findings],
        ),
        gates=trace.gates,
        delivered=False,
        trace_id=trace.trace_id,
    )
    trace.answer = envelope.answer.model_dump(mode="json")
    return envelope, trace


def _fail_closed(screening: Screening, settings: Settings) -> bool:
    """Whether a silent screening model should stop the turn.

    Only reachable when a model was configured and returned nothing. The rule
    layer has already run either way, so this is a policy question about the
    semantic layer alone, not about screening as such.
    """
    return bool(screening.degraded) and bool(getattr(settings, "guardrail_fail_closed", False))


def _product_page(pages: list[Page]) -> Page | None:
    """The canonical product page among those loaded — the one carrying the
    channel bindings and version in force."""
    products = [p for p in pages if p.frontmatter.type == PageType.product]
    if not products:
        return None
    # Prefer the shallowest id: product/general/travel over .../travel/benefits.
    return sorted(products, key=lambda p: (p.id.count("/"), p.id))[0]


#: What the bot says when the turn was a pleasantry rather than a question.
#: Deterministic and claimless on purpose — a greeting is the one reply with no
#: source behind it, so it must not be a place where a model can offer
#: capabilities the corpus does not have. It says what this actually does and
#: stops.
_PLEASANTRIES = {
    "greeting": (
        "Hello. I can answer questions about {underwriter}'s products from the "
        "policy wordings and product pages — what is covered, what is not, how "
        "to claim, and the policy conditions. What would you like to know?"
    ),
    "thanks": "You're welcome. Anything else about your cover?",
    "farewell": "Goodbye. Come back any time you need to check your cover.",
    "capability": (
        "I am an automated assistant for {underwriter}. I answer from the "
        "compiled policy wordings and product pages, and every answer names the "
        "document it came from. I can cover what a product includes and "
        "excludes, how to make a claim, and the policy conditions. I cannot give "
        "financial advice or tell you which plan to buy — that needs a licensed "
        "adviser — and I will say so rather than guess when the documents do "
        "not answer your question."
    ),
}


def _pleasantry(kind: str, bundle: Bundle) -> str:
    """The reply, named after whoever actually underwrites this bundle.

    The trailing stop goes: the legal name is "Etiqa Insurance Pte. Ltd." and
    interpolating it mid-sentence otherwise yields "Ltd..".
    """
    underwriter = (bundle.manifest.underwriter or "this insurer").rstrip(".")
    return _PLEASANTRIES[kind].format(underwriter=underwriter)


def _finish(
    trace: Trace,
    answer: GroundedAnswer,
    bundle: Bundle,
    session: Session,
    question: str,
    raw_root: Path,
    loaded: list[str],
    judge: Judge | None = None,
    ask: Ask | None = None,
) -> tuple[AnswerEnvelope, Trace]:
    """Gate an answer produced without the retrieve-and-compose path.

    The short-circuits still go through the gates. A greeting will skip every
    one and a directory listing will pass reference-integrity on the products
    it named — but a turn that bypassed verification silently would look, on
    the trace, exactly like one that passed it.
    """
    focus = bundle.get(ask.product_page) if ask is not None and ask.product_page else None
    if not answer.handoff or (focus is not None and answer.guidance):
        answer.suggestions = suggest_next(
            bundle,
            ask,
            focus,
            clarifying=answer.clarifying,
            today=session.today,
        )
    with trace.stage("gates") as detail:
        results = run_gates(
            GateContext(
                answer=answer,
                bundle=bundle,
                session=session,
                question=question,
                loaded_page_ids=loaded,
                raw_root=raw_root,
                today=session.today,
                judge=judge,
                ask=ask,
            )
        )
        trace.gates = results
        detail["failed"] = [g.gate for g in results if g.blocking]
    delivered = not blocked(results)
    trace.delivered = delivered
    return (
        AnswerEnvelope(answer=answer, gates=results, delivered=delivered, trace_id=trace.trace_id),
        trace,
    )


#: What the rewrite is asked for when the customer asked for the shape of a
#: product. The deterministic presentation layer produces the same shape, so
#: the two paths read alike.
OVERVIEW_STYLE = (
    "This is a product introduction. Open with one sentence saying what the plan is, "
    "then a short bulleted list headed 'What it covers:' (one bullet per cover item, "
    "keep the wording of the facts), then the route to buy if given, and end with one "
    "question offering the customer two or three things to ask next (what is not "
    "covered, how to claim, promotions, how to buy). Friendly, plain, no marketing."
)


def _priced(draft: GroundedAnswer) -> bool:
    """A bound figure that is a premium, price or cost — not the plan's FAQ."""
    return any(f.is_bound and any(w in f.label.lower() for w in PRICED_LABELS) for f in draft.figures)


def _wording_pointer(bundle: Bundle, product: Page | None) -> str:
    """Where the figures that were left out can be read. No digits in it."""
    from api.guidance import root_page

    root = root_page(bundle, product)
    url = landing_for(root) if root is not None else None
    if url and not re.search(r"\d{2,}", url):
        return (
            "The exact figures — time limits, amounts and ages — are in the policy wording, "
            f"on the plan's page: {url}"
        )
    return (
        "The exact figures — time limits, amounts and ages — are in the policy wording, "
        "which the plan's page links to."
    )


def _strip_unbound(draft: GroundedAnswer, orphans: list[str], pointer: str) -> GroundedAnswer | None:
    """The draft without the lines that carry an unbound figure, or None if
    nothing substantive is left. The claims those lines made go with them.

    Lines are found from the figure's position in the text, not by searching
    for its digits: a span the gate read across a line break ("S$" at the
    end of one table cell, "3" at the start of the next) names two lines,
    and a bare "3" searched for would name every line with a 3 in it.
    """
    if not orphans:
        return None
    text = draft.answer
    wanted = {" ".join(o.split()) for o in orphans}
    starts: list[int] = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    lines = text.split("\n")

    def line_of(offset: int) -> int:
        return max(i for i, start in enumerate(starts) if start <= offset)

    doomed: set[int] = set()
    for match in NUMERIC_SPAN_RE.finditer(text):
        if " ".join(match.group().split()) in wanted:
            doomed.update(range(line_of(match.start()), line_of(max(match.start(), match.end() - 1)) + 1))
    kept = [line for i, line in enumerate(lines) if i not in doomed]
    dropped = [line for i, line in enumerate(lines) if i in doomed]
    remaining = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    gone = "\n".join(dropped)
    claims = [
        c
        for c in draft.claims
        if not (c.text and c.text in gone) and not any(len(d) > 20 and d.strip() in c.text for d in dropped)
    ]
    if not remaining or not claims or not re.search(r"[A-Za-z]{4,}", remaining):
        return None
    return draft.model_copy(
        update={
            "answer": f"{remaining}\n\n{pointer}",
            "claims": claims,
            "figures": [f for f in draft.figures if f.is_bound],
            "confidence": min(draft.confidence, 0.6),
        }
    )


def _bind_ages(draft: GroundedAnswer, orphans: list[str]) -> GroundedAnswer | None:
    """Bind an unbound figure in an age sentence to the page the sentence
    came from; the numeric-binding gate re-reads that page to confirm it.
    None where no orphan sits in an age sentence."""
    added: list[Figure] = []
    for claim in draft.claims:
        if not claim.text or not AGE_RE.search(claim.text):
            continue
        for orphan in orphans:
            if orphan in claim.text and not any(f.text == orphan for f in added):
                added.append(Figure(label="age", text=orphan, page_ref=claim.source_id))
    if not added:
        return None
    return draft.model_copy(update={"figures": [*draft.figures, *added]})


def _ask_from_trace(trace: Trace) -> Ask | None:
    """The Ask the turn recorded, rebuilt for the memory line."""
    for stage in trace.stages:
        if stage.name == "ask":
            detail = stage.detail
            try:
                intent = Intent(detail.get("intent", "unknown"))
            except ValueError:
                intent = Intent.unknown
            return Ask(
                question=trace.question,
                intent=intent,
                product=detail.get("product"),
                scope=detail.get("scope", "specific"),
                full=bool(detail.get("full")),
            )
    return None


def _root_page(bundle: Bundle, product_key: str) -> Page | None:
    """The product's own page for a benefit-table key, or None."""
    for page in bundle.pages.values():
        if (
            page.frontmatter.type == PageType.product
            and page.id.count("/") == 2
            and bundle.product_key(page) == product_key
        ):
            return page
    return None


def _tier_specific(product: Page, bundle: Bundle) -> bool:
    """True when any transcluded figure on the product's pages varies by tier."""
    version = product.frontmatter.version_in_force or ""
    product_key = product.id.rsplit("/", 1)[-1]
    tiers = [t for t in bundle.tables.tiers_for(product_key, version) if t != "ALL"]
    if not tiers:
        return False
    for page in bundle.pages.values():
        if not page.id.startswith(product.id):
            continue
        for benefit, attribute in find_tokens(page.body):
            try:
                bundle.tables.fetch(product_key, version, tiers[0], benefit, attribute)
            except LookupError:
                continue
            for other in tiers[1:]:
                try:
                    if (
                        bundle.tables.fetch(product_key, version, other, benefit, attribute).value
                        != bundle.tables.fetch(product_key, version, tiers[0], benefit, attribute).value
                    ):
                        return True
                except LookupError:
                    continue
    return False
