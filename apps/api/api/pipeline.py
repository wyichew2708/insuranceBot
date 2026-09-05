"""Loop 1 — Serve (§G).

    route → read wiki / RAG / SOR → generate → gates → answer

Its second job is to emit good telemetry, because that is what powers Loop 4
(Evolve). Every decision the loop makes is recorded on the trace, including
the pages it considered and rejected.
"""

from __future__ import annotations

import contextlib
import dataclasses
import re
import threading
from pathlib import Path
from typing import Any

from harness import (
    AnswerEnvelope,
    AuthLevel,
    Budget,
    BudgetExhausted,
    Figure,
    GateContext,
    GroundedAnswer,
    Judge,
    Session,
    Trace,
    blocked,
    run_gates,
)
from harness.ask import Ask, read_ask
from harness.contracts import Link
from harness.gates import ADVICE_SEEKING_RE, NUMERIC_SPAN_RE, unbound_spans
from harness.intent import OUT_OF_CORPUS, Intent, classify, smalltalk_kind
from harness.trace import LoadedPage, StageListener
from okf.tables import find_tokens

from api.clarify import LISTABLE, MAX_OPTIONS, clarification, lexical_clarification, open_clarification
from api.compose import compose
from api.directory import answer as directory_answer
from api.directory import lines_overview
from api.entity import answer as entity_answer
from api.gates_ext import advice_required
from api.guardrails import MEDICAL_EMERGENCY, Guard, Screening, guard_for, medical_emergency, redact_pii
from api.guidance import guidance
from api.llm import Draft, LLMProvider, provider_for
from api.memory import SessionMemory
from api.present import bulletise, digest, present_overview, section_chips
from api.reference import resolve
from api.retrieval import (
    NO_MATCH_PREFIXES,
    frontmatter_filter,
    keywords,
    needs_rag,
    product_family_pages,
    rag_search,
    tie_on_subject,
    unsupported_term,
    wiki_read,
)
from api.route import FRAUD_RE, destinations_for, fraud_opener
from api.router import Layer1, Layer2, Layer3
from api.router import route as route_turn
from api.settings import Settings
from api.sor import NotEntitled, policy_summary
from api.split import split_questions
from api.suggest import closing_question, suggest_next
from api.understand import Understanding, understand, worth_resolving
from api.vectors import searcher_for
from okf import (
    DESTINATIONS,
    Bundle,
    Desk,
    Page,
    PageType,
    expand_abbreviations,
    expand_vocabulary,
    landing_for,
    load_abbreviations,
    load_vocabulary,
    term_idf,
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
    if not answer.handoff:
        focus = bundle.get(ask.product_page) if ask is not None and ask.product_page else None
        answer.suggestions = suggest_next(bundle, ask, focus, clarifying=answer.clarifying)
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

_MEMORIES: dict[str, SessionMemory] = {}


def memory_for(settings: Settings) -> SessionMemory:
    """One memory per state directory, for the life of the process."""
    # `auto` is on in the API server and off everywhere else: a test or a
    # batch evaluation constructs Settings directly and must stay stateless,
    # or one case's question would carry into the next. `main.py` resolves
    # `auto` to `on` when it loads settings for the served process.
    key = f"{settings.state_dir}|{settings.memory.lower()}"
    if key not in _MEMORIES:
        _MEMORIES[key] = SessionMemory(settings.state_dir, enabled=settings.memory.lower() == "on")
    return _MEMORIES[key]


def answer_question(
    bundle: Bundle,
    question: str,
    session: Session,
    settings: Settings,
    history: list[str] | None = None,
    provider: LLMProvider | None = None,
    on_stage: StageListener | None = None,
) -> tuple[AnswerEnvelope, Trace]:
    """One turn, remembered.

    The client's `history` is believed when it sends one; a client that sends
    nothing gets the session's own earlier questions from memory, so the
    subject carries forward either way. Every turn leaves a one-line summary
    behind it, and the summary rides back on the envelope.
    """
    memory = memory_for(settings)
    # Redacted here, before the memory or the trace can see it — the inner
    # turn redacts again for callers that reach it directly. The first live
    # run stored "my nric is S1234567A" in the session file; this is why.
    question, _ = redact_pii(question)
    recalled = memory.recall(session.session_id)
    turns = list(history) if history else recalled.questions
    provider = provider or provider_for(settings)
    # Two questions in one breath are two turns, each routed to its own
    # handler, answered in order with the earlier parts as history, and put
    # back together (`api.split`, `_consolidate`).
    parts = split_questions(question)
    if len(parts) > 1:
        envelopes: list[AnswerEnvelope] = []
        traces: list[Trace] = []
        for index, part in enumerate(parts):
            part_envelope, part_trace = _answer_turn(
                bundle, part, session, settings, [*turns, *parts[:index]], provider, on_stage
            )
            envelopes.append(part_envelope)
            traces.append(part_trace)
        envelope, trace = _consolidate(parts, envelopes, traces)
    else:
        envelope, trace = _answer_turn(bundle, question, session, settings, turns, provider, on_stage)
    ask = _ask_from_trace(trace)
    envelope.summary = memory.remember(session.session_id, question, envelope, ask)
    if envelope.delivered and not envelope.answer.smalltalk:
        memory.refine_later(session.session_id, question, envelope.answer.answer, provider)
    return envelope, trace


def _consolidate(
    parts: list[str], envelopes: list[AnswerEnvelope], traces: list[Trace]
) -> tuple[AnswerEnvelope, Trace]:
    """One reply from the parts' replies, and one trace that says it was several.

    The text is the parts in order. Claims, figures and destinations are the
    union — each part's own gates have already held them, and the gate
    results travel with the envelope so a reader sees every verdict. A
    handoff only where every part handed off; delivered where any part was.
    """
    answers = [e.answer for e in envelopes]
    links: list[Link] = []
    for answer in answers:
        for link in answer.destinations:
            if all(link.url != seen.url for seen in links):
                links.append(link)
    merged = GroundedAnswer(
        answer="\n\n".join(a.answer.strip() for a in answers if a.answer.strip()),
        claims=[c for a in answers for c in a.claims],
        figures=[f for a in answers for f in a.figures],
        channel_render=next((a.channel_render for a in answers if a.channel_render is not None), None),
        advice_flag=any(a.advice_flag for a in answers),
        confidence=min(a.confidence for a in answers),
        unresolved=[u for a in answers for u in a.unresolved],
        handoff=all(a.handoff for a in answers),
        smalltalk=all(a.smalltalk for a in answers),
        clarifying=any(a.clarifying for a in answers),
        guidance=all(a.guidance for a in answers),
        suggestions=answers[-1].suggestions,
        destinations=links,
    )
    trace = traces[0]
    trace.route = {**trace.route, "parts": str(len(parts))}
    for index, (part, part_trace, part_envelope) in enumerate(
        zip(parts, traces, envelopes, strict=True), start=1
    ):
        route = part_trace.route
        summary = f"{route.get('layer1', '')}/{route.get('layer2', '')}/{route.get('layer3', '')}"
        trace.route[f"part{index}"] = summary
        trace.note(f"part {index} {part!r}: routed {summary}, delivered={part_envelope.delivered}")
    trace.gates = [g for e in envelopes for g in e.gates]
    delivered = any(e.delivered for e in envelopes)
    trace.delivered = delivered
    trace.answer = merged.model_dump(mode="json")
    return AnswerEnvelope(
        answer=merged, gates=trace.gates, delivered=delivered, trace_id=trace.trace_id
    ), trace


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


def _answer_turn(
    bundle: Bundle,
    question: str,
    session: Session,
    settings: Settings,
    history: list[str] | None,
    provider: LLMProvider,
    on_stage: StageListener | None = None,
) -> tuple[AnswerEnvelope, Trace]:
    trace = Trace(question=question, session_id=session.session_id, channel=session.channel.value)
    # A caller that streams progress hears each stage as it opens and closes.
    # Nothing about the answer is streamed from here: the text a customer sees
    # is the text the gates passed, and that does not exist until the end.
    trace.listen(on_stage)
    budget = Budget(
        max_pages=settings.max_pages,
        max_tool_calls=settings.max_tool_calls,
        max_wall_clock_s=settings.max_wall_clock_s,
        max_tokens=settings.max_tokens,
    )
    raw_root = settings.bundle_path / "raw"

    # Screened before anything is retrieved. A turn that will not be answered
    # should not spend a page budget, a SOR call or a model call finding that
    # out, and a turn carrying instructions should never reach a prompt.
    # One provider for the turn, shared by the two screens and the rewrite.
    # Same credentials by construction — there is no separate guardrail key —
    # and one client rather than two for what may be three calls.
    # Injectable so a test can dictate a verdict without a network or a key.
    # The guardrail layer already takes one for the same reason.
    provider = provider or provider_for(settings)
    guard: Guard = guard_for(settings, provider)
    with trace.stage("guardrail-input") as detail:
        incoming = guard.screen_input(question)
        detail["risk"] = incoming.risk.value
        detail["checked_by"] = incoming.checked_by
        if incoming.degraded:
            detail["degraded"] = incoming.degraded
        if guard.unusable_model:
            detail["dropped_model_override"] = guard.unusable_model
        if incoming.findings:
            detail["findings"] = [f"{f.category}:{f.source}@{f.confidence:.2f}" for f in incoming.findings]
            # The arithmetic behind the verdict, so a refusal can be accounted
            # for and a threshold tuned against real traffic.
            detail["scores"] = [str(sc) for sc in incoming.scores]
    if incoming.blocked or _fail_closed(incoming, settings):
        return _refusal(trace, incoming, "guardrail-input", "refused by the input guardrail")

    # Personal identifiers leave the turn here, before the Ask, the model,
    # the trace or the memory sees them. The customer's NRIC has no bearing on
    # what a policy covers.
    question, redacted = redact_pii(question)
    if redacted:
        trace.note(f"personal data redacted from the turn: {', '.join(redacted)}")

    # One reading of the question, before anything rewrites it. The product
    # name is read from the customer's own words — the abbreviation pass
    # turns "Tiq PA Insurance" into another product's title — and everything
    # downstream reads this object rather than re-deriving its own guess.
    ask = read_ask(bundle, question, list(history or []))
    recent = (history or [])[-3:]
    if REFUND_FOLLOWUP_RE.search(question) and any(CANCEL_CONTEXT_RE.search(t) for t in recent):
        ask = dataclasses.replace(ask, intent=Intent.payment)
        trace.note("read as a refund question: the conversation is about a cancellation")
    with trace.stage("ask") as detail:
        detail.update(ask.as_trace())

    # The three-layer decision, made once and written down. The branches
    # below are its handlers; what is new is that the trace and the
    # evaluation can say which layer a turn went through, and that a guess
    # at the product is asked about rather than answered (`api.router`).
    decision = route_turn(bundle, ask, question)
    trace.route = decision.as_trace()
    with trace.stage("router") as detail:
        detail.update(decision.as_trace())
        if decision.options:
            detail["options"] = list(decision.options)

    # After screening, before retrieval. A greeting is not a question the
    # corpus can fail to answer, and routing one through retrieval replies to
    # "hi" with "I could not establish that from our approved product pages" —
    # which reads as a broken bot, not a careful one. It is screened first
    # because "hi" with an injection payload stapled to it is not a greeting.
    kind = smalltalk_kind(question)
    if kind is not None:
        with trace.stage("smalltalk") as detail:
            detail["kind"] = kind
        answer = GroundedAnswer(
            answer=_pleasantry(kind, bundle),
            smalltalk=True,
            confidence=1.0,
        )
        # The gates still run. Every one of them will skip — there is nothing
        # to check in a reply that asserts nothing — but a turn that silently
        # bypassed verification would be indistinguishable, on the trace, from
        # one that passed it. Skipping on the record is the point.
        return _finish(trace, answer, bundle, session, question, raw_root, [], ask=ask)

    # A shopper, not a questioner. "what life products" and "looking for a CI
    # plan" ask what exists; retrieval finds the best single page and answers
    # from its prose, which is how "what life products" came back as Products
    # Liability. The bundle already knows every product and its line of
    # business — this reports that rather than ranking it.
    # Who underwrites this. One entity page, one underwriter across every
    # product, so it is a fact to state rather than a page to rank for — and
    # ranking for it is how a fire-peril clause answered "who is the insurer".
    if classify(question) is Intent.entity:
        stated = entity_answer(bundle)
        if stated is not None:
            with trace.stage("entity") as detail:
                detail["underwriter"] = [c.text for c in stated.claims]
            return _finish(
                trace,
                stated,
                bundle,
                session,
                question,
                raw_root,
                [c.source_id for c in stated.claims],
                ask=ask,
            )

    if classify(question) is Intent.browse:
        listing = directory_answer(bundle, question)
        if listing is not None:
            with trace.stage("directory") as detail:
                detail["products"] = [c.source_id for c in listing.claims]
            return _finish(
                trace,
                listing,
                bundle,
                session,
                question,
                raw_root,
                [c.source_id for c in listing.claims],
                ask=ask,
            )

    # What "it" refers to. A turn that names no subject borrows the topic from
    # the nearest earlier turn that did — "what's the coverages" after "term
    # life" — and a turn that stands on its own is left exactly as typed, or
    # "what about car insurance?" gets answered about term life.
    with trace.stage("reference") as detail:
        resolution = resolve(question, list(history or []), bundle)
        if resolution.resolved:
            detail["carried_from"] = resolution.carried_from
            detail["resolved"] = resolution.question
            question = resolution.question
            # A subject borrowed from an earlier turn names a product this
            # turn did not; the Ask carries it, marked as carried.
            ask = ask.carried_from(read_ask(bundle, question))
            if ask.named_by == "history":
                detail["ask_product"] = ask.product

    # Spell out the initials before anything scores the words. The tokeniser
    # drops anything under three characters, so "ci" reached retrieval as
    # nothing at all and the turn was scored on "product" alone. Expanded
    # rather than replaced: the wordings say "covered CI" and the product pages
    # say "Critical Illness", and an answer has to reach both.
    with trace.stage("expand") as detail:
        expanded = expand_abbreviations(question, load_abbreviations(settings.bundle_path))
        if expanded != question:
            detail["expanded"] = expanded
            question = expanded

    # Which product this is about, read rather than counted. Falls through to
    # lexical ranking on absence, timeout, malformed output, or an id that does
    # not resolve — so it can improve selection and cannot degrade it.
    understanding = Understanding(degraded="not attempted")
    # No stage at all on the deterministic path: opening one that reports "no
    # model" on every offline turn is noise in the one trace people read most.
    # A product named in full needs no model to identify it, and the catalogue
    # call is the single slowest thing on the turn (8.5 s of a 30 s answer on a
    # 35B model, most of it prefill of the same 3,100-token list). The name is
    # authoritative either way — it overrules the model's pick below — so
    # where it resolves to exactly one product the call is skipped outright.
    if ask.resolved or ask.ambiguous:
        understanding = Understanding(
            product_ids=[], subject="product", degraded=f"read: {ask.named_by or 'family'}"
        )
    elif settings.resolve_with_model and provider.name != "deterministic" and worth_resolving(question):
        with trace.stage("understand") as detail:
            understanding = understand(bundle, question, provider, history=list(history or []))
            detail["products"] = understanding.product_ids
            if understanding.ambiguous:
                detail["ambiguous"] = True
            if understanding.degraded:
                detail["degraded"] = understanding.degraded
            ask = ask.with_model(
                understanding.product_ids, understanding.ambiguous, understanding.subject, bundle
            )
            detail["ask"] = ask.as_trace()

    # Before anything is retrieved, and before any product can be named. Asked
    # about chest pain and a numb arm, this answered "I cannot provide medical
    # advice regarding your symptoms, but our Cancer Insurance..." — the
    # disclaimer turned into a hinge for a pitch. Nothing downstream would stop
    # that, because the pitch was accurate and properly cited.
    if medical_emergency(question):
        with trace.stage("medical-emergency") as detail:
            detail["routed"] = "care"
        return _finish(
            trace,
            GroundedAnswer(answer=MEDICAL_EMERGENCY, smalltalk=True, handoff=True, confidence=1.0),
            bundle,
            session,
            question,
            raw_root,
            [],
            ask=ask,
        )

    # Not a question about insurance. Every gate downstream verifies that an
    # answer is faithful to the corpus; none asks whether the question was ours,
    # so retrieval would find *something* and ground an answer in it perfectly
    # — which is how "what is the capital of france" was answered with the
    # definitions of Loss of Sight and Permanent Total Disablement.
    #
    # Only on the model's explicit `off_topic`. A question that merely resolves
    # to no product is usually a general one about insurance — "what is an
    # excess", "how do I contact you" — and those must still be answered.
    if ask.kind == "off_topic":
        with trace.stage("off-topic") as detail:
            detail["declined"] = True
        return _finish(
            trace,
            GroundedAnswer(answer=OFF_TOPIC, smalltalk=True, confidence=1.0),
            bundle,
            session,
            question,
            raw_root,
            [],
            ask=ask,
        )

    # Before retrieval, and the reason is not cost. A question about where a
    # claim got to, when a refund lands, whether an address change went
    # through, a password, or a request for a person is not a gap in the
    # corpus — it is not the kind of thing a policy document contains. Sent
    # through retrieval anyway it finds the nearest page and answers from it:
    # "where is my claim now?" came back with the claim-notification clause,
    # "when will the refund reach me?" with the terms of a 2024 promotion, and
    # a customer checking whether an email was a phishing attempt was told to
    # log in and update their details. 237 of 379 failing turns on the golden
    # conversation dataset are this one mode.
    #
    # So they are refused here, deterministically, and — the half that makes
    # the refusal worth giving — pointed at the page that does know.
    if ask.intent in OUT_OF_CORPUS:
        routed_intent = ask.intent
        focus = bundle.get(ask.product_page) if ask.product_page else None
        with trace.stage("route") as detail:
            detail["intent"] = routed_intent.value
            detail["product"] = focus.id if focus is not None else ""
            detail["destinations"] = [d.url for d in destinations_for(routed_intent, focus, question)]
        # Not a refusal: the steps to the real answer, from the guidance
        # table (`api.guidance`). A fraud report keeps its safety line first
        # and goes to a person and nowhere else.
        fraud = routed_intent is Intent.contact and bool(FRAUD_RE.search(question))
        return _finish(
            trace,
            guidance(
                bundle,
                raw_root,
                routed_intent,
                focus,
                question,
                opener=fraud_opener(question) if fraud else None,
            ),
            bundle,
            session,
            question,
            raw_root,
            [],
            ask=ask,
        )

    # A price question with no plan in hand is not a which-plan question.
    # "Get me a quote" and "how much does insurance cost for a family of
    # four?" were asked to choose between Home and Cyber; the price of any of
    # them is not in the corpus, and the quote steps are the same for all.
    if ask.intent is Intent.price and not ask.resolved and decision.layer1 is Layer1.product:
        with trace.stage("route") as detail:
            detail["intent"] = Intent.price.value
            detail["product"] = ""
        return _finish(
            trace,
            guidance(bundle, raw_root, Intent.price, None, question),
            bundle,
            session,
            question,
            raw_root,
            [],
            ask=ask,
        )

    # A request for advice is answered by the adviser handoff, never by a menu
    # of products: "should I cancel my Great Eastern policy and move to Etiqa"
    # clarified between Term Life and Whole Life, which is the recommendation
    # the boundary exists to prevent, dressed as a question. Both clarifying
    # paths defer to it.
    seeking_advice = bool(ADVICE_SEEKING_RE.search(question))

    # Shopping with no line named — "what insurance products do you offer?"
    # — matched no directory line and fell through to a product
    # clarification. The reply is the shape of the catalogue.
    if decision.layer1 is Layer1.browse and not seeking_advice:
        # A shopper who named a line this insurer does not write — "kidnap
        # and ransom cover" — is told so further down, never shown the
        # nearest thing we do sell (`api.directory.answer` says as much of
        # `None`, and on the real corpus "ransom" found Property Insurance).
        listed = None
        if not unsupported_term(bundle, question, []):
            listed = directory_answer(bundle, question) or lines_overview(bundle)
        if listed is not None:
            with trace.stage("directory") as detail:
                detail["listed"] = [c.source_id for c in listed.claims]
            return _finish(
                trace,
                listed,
                bundle,
                session,
                question,
                raw_root,
                [c.source_id for c in listed.claims],
                ask=ask,
            )

    # Unsure which product: ask. Three cases, one behaviour. `ambiguous` — the
    # customer named two, or the model could not choose. `guessed` — the
    # customer named a category and the code had picked its flagship; that
    # is a reading, not the customer's word. `none` on a handler whose answer
    # depends on the product — cover, exclusions, a limit, how to claim.
    if decision.clarify and not seeking_advice:
        # A guess at a line lists the line: a customer who said "my flight was
        # delayed" is choosing among every travel plan, not the first three.
        limit = max(MAX_OPTIONS, min(len(decision.options), LISTABLE))
        asked = clarification(bundle, list(decision.options), limit=limit) if decision.options else None
        if asked is None:
            asked = open_clarification()
        with trace.stage("clarify") as detail:
            detail["layer2"] = decision.layer2.value
            detail["options"] = [c.source_id for c in asked.claims]
        return _finish(
            trace,
            asked,
            bundle,
            session,
            question,
            raw_root,
            [c.source_id for c in asked.claims],
            ask=ask,
        )

    # The product this turn is about, as the Ask read it: named by the
    # customer, carried from an earlier turn, the flagship of a category, or
    # the model's pick. A name given by the customer is not one more
    # candidate — it is the answer to "which product", and it overrules the
    # lexical rank below. Eleven answers in a 1,000-case sample cited a
    # sibling rider of the one the question named in full, all at 0.99.
    focus_override = (
        decision.product if decision.layer2 in (Layer2.named, Layer2.carried, Layer2.inferred) else None
    )
    scope = decision.scope

    try:
        # Dense recall, where an index is configured. No stage at all on the
        # lexical path, for the reason `understand` gives: a stage that
        # reports "not configured" on every offline turn is noise in the one
        # trace people read most. Where it is configured and fails, the turn
        # carries on lexically and the trace says why — unless fail-closed,
        # which is what an unreachable index is worth in a deployment that
        # would rather refuse than degrade.
        vector = None
        searcher = searcher_for(settings)
        if searcher is not None:
            with trace.stage("vector-search") as detail:
                vector = searcher.search(bundle, question, session.today)
                detail["hits"] = len(vector.hits)
                if vector.degraded:
                    detail["degraded"] = vector.degraded
                    trace.vector_degraded = vector.degraded
                    if searcher.fail_closed or searcher.mode == "on":
                        # The defined exit (§F.3): a handoff, with the reason on
                        # the trace. `limit=0` — there was no budget here, only
                        # a dependency that was asked for and did not answer.
                        raise BudgetExhausted(f"vector index: {vector.degraded}", 0)
                else:
                    trace.retrieval_mode = "hybrid"

        with trace.stage("frontmatter-filter") as detail:
            admitted = frontmatter_filter(
                bundle,
                question,
                session,
                trace,
                settings.candidate_floor,
                focus_override,
                settings.confidence_floor,
                vector,
                settings.vector_floor,
            )
            # `must_include` semantics on the dense side: a vector candidate
            # that does not contain the product-shaped word the corpus has
            # never seen is the nearest neighbour, which is the failure.
            if vector is not None and vector.hits:
                missing = unsupported_term(bundle, question, admitted)
                if missing:
                    lifted = set(vector.by_page)
                    admitted = [
                        (pg, sc) for pg, sc in admitted if pg.id not in lifted or missing in keywords(pg.body)
                    ]
            detail["admitted"] = len(admitted)
            detail["rejected"] = len(trace.candidates) - len(admitted)
            if trace.ambiguous_products:
                detail["ambiguous"] = trace.ambiguous_products[:8]

        # Nothing read the question well enough to name a product, and the
        # lexical layer did not either — it produced a tie. Ask, rather than
        # let an alphabetical tiebreak answer on the customer's behalf: this is
        # how "how do i make a claim", which ties 87 products, was answered
        # about Plate Glass.
        # ...but not where the customer named a product line we do not carry.
        # "What does your crop insurance cover?" ties three products on the word
        # "cover" alone, and offering the customer a choice between home, travel
        # and car implies one of them is what they asked for. `unsupported_term`
        # already knows better; it just runs later, so it is consulted here.
        # A misspelt product we *do* carry does not reach this — the model
        # resolves "trvael insurance" and sets a focus long before the tie.
        missing_line = unsupported_term(bundle, question, admitted)
        if (
            not focus_override
            and not missing_line
            and not seeking_advice
            and len(trace.ambiguous_products) >= 2
        ):
            # A tie reached on the question's subject is a real choice and
            # is named. A tie reached on its generic words alone — "how much
            # can I claim for a lost bag?" tied Term Life, Whole Life and
            # Maid on "how much", "claim" and "lost", with "bag" matched by
            # none of them — is not, and naming it would be naming three
            # wrong products with confidence. That tie is asked about openly.
            asked = (
                lexical_clarification(bundle, trace.ambiguous_products)
                if tie_on_subject(bundle, question, trace.ambiguous_products)
                else open_clarification()
            )
            if asked is not None:
                with trace.stage("clarify") as detail:
                    detail["from"] = "lexical tie"
                    detail["options"] = trace.ambiguous_products[:8]
                return _finish(
                    trace,
                    asked,
                    bundle,
                    session,
                    question,
                    raw_root,
                    [c.source_id for c in asked.claims],
                    ask=ask,
                )

        with trace.stage("wiki-read") as detail:
            pages = wiki_read(
                bundle, admitted, trace, budget, settings.wiki_read_limit, session.today, question, scope
            )
            # An unnamed product, settled or not by what the corpus produced.
            # One product's pages and nothing else's: the corpus can answer,
            # and the rest of the turn is scoped to it as if it had been
            # named. Several products, or none: the handler's answer depends
            # on which, and the customer is asked rather than answered from
            # whichever scored first.
            if decision.needs_product:
                loaded_products = {
                    bundle.product_key(page) for page in pages if page.id.startswith("product/")
                }
                # The filter narrows to one product whenever any focus wins,
                # so "one product loaded" alone proves little. What proves the
                # corpus can settle it is one product loaded *and* the
                # filter's own tie detector silent: `ambiguous_products` is
                # every product within the focus margin of the leader.
                settled = len(loaded_products) == 1 and not trace.ambiguous_products
                # Asked only where there is something to choose between. A
                # turn that loaded no product at all is not a choice: a stale
                # bundle, or a line this insurer does not write ("crop
                # insurance"), and both already have their own honest reply
                # further down — a handoff, and "we do not carry that".
                undecided = bool(trace.ambiguous_products) or len(loaded_products) >= 2
                if settled:
                    decision = decision.inferred(next(iter(loaded_products)))
                    scope = decision.scope
                    trace.route = decision.as_trace()
                elif undecided and not unsupported_term(bundle, question, admitted):
                    # Name options only among products whose pages were
                    # actually read. A tie with nothing loaded is a tie on
                    # generic words — "how much can I claim for a lost bag?"
                    # tied Term Life, Whole Life and Maid at a score of
                    # nothing — and listing it would be naming three wrong
                    # products with confidence. That turn is asked openly.
                    tied = sorted(loaded_products) if loaded_products else []
                    roots = [r for r in (_root_page(bundle, key) for key in tied) if r is not None]
                    asked = clarification(bundle, [r.id for r in roots]) if 2 <= len(roots) <= 3 else None
                    if asked is None:
                        asked = open_clarification()
                    with trace.stage("clarify") as detail:
                        detail["layer2"] = "none"
                        detail["products_loaded"] = sorted(loaded_products)
                        detail["tied"] = tied[:8]
                    return _finish(
                        trace,
                        asked,
                        bundle,
                        session,
                        question,
                        raw_root,
                        [c.source_id for c in asked.claims],
                        ask=ask,
                    )
            # The product's published FAQ rides along whenever the product is
            # known: the composer answers a question the insurer has already
            # answered with that answer, and the page has to be loaded for
            # the gates to hold it as evidence.
            if ask.product_page:
                # A misspelt name resolves the product but matches no page
                # lexically — "car insurnace coverage" loaded nothing and
                # handed off. The product the Ask read is loaded whatever the
                # words scored.
                # The product's family, read off the graph rather than guessed
                # from a suffix list. The list said `/faq /cover /benefits
                # /exclusions /claims /conditions`; the real corpus also files
                # `/definitions` and `/eligibility`, and every question about a
                # defined term on a product the words did not find was answered
                # without the page that defines it. `EdgeKind.child` is the
                # containment the suffixes were approximating, and it is right
                # by construction for whatever the compiler emits next.
                wanted = [ask.product_page, *product_family_pages(bundle, ask.product_page)]
                held = {p.id for p in pages}
                has_product = any(p.id.startswith(ask.product_page) for p in pages)
                # The root and the FAQ always; the rest only when nothing of
                # the product was loaded. The first cut kept the root alone
                # once any product page was in hand, and the FAQ — the short
                # published answer — stopped arriving.
                always = [ask.product_page, f"{ask.product_page}/faq"]
                for page_id in wanted if not has_product else always:
                    extra = bundle.get(page_id)
                    if extra is not None and extra.id not in held:
                        pages.append(extra)
                        held.add(extra.id)
                        trace.loaded.append(
                            LoadedPage(page_id=extra.id, title=extra.frontmatter.title, via="ask")
                        )
            detail["pages"] = [p.id for p in pages]

        product = _product_page(pages)
        top_score = admitted[0][1] if admitted else 0.0

        with trace.stage("rag-decision") as detail:
            reason = needs_rag(question, admitted, session, settings.confidence_floor, bundle)
            detail["reason"] = reason or "not needed"
            if reason:
                trace.rag_used = True
                trace.rag_reason = reason
                budget.charge_tool()
                # Dense recall over the sources, where an index is configured.
                # Only here, and only on the turns the fallback actually fires
                # — a few per cent of them — so the second query costs the
                # request path nothing on a turn the wiki answered. The
                # question's embedding is already in hand from the search
                # above; `VectorSearch.embed` memoises it rather than paying
                # for it twice.
                raw_dense = []
                if searcher is not None:
                    found = searcher.search_raw(bundle, question)
                    detail["dense"] = len(found.hits) if not found.degraded else found.degraded
                    raw_dense = found.hits
                trace.rag_hits = rag_search(
                    raw_root,
                    question,
                    session,
                    idf=term_idf(bundle),
                    must_include=unsupported_term(bundle, question, admitted),
                    dense=raw_dense,
                    dense_floor=settings.vector_raw_floor,
                    # The product scope reaches the raw sources too: a
                    # wording tagged to another product is not a fallback.
                    admit=(lambda rel: scope.allows_raw(bundle, rel)) if scope.scoped else None,
                )
                detail["hits"] = [f"{h.found_by}:{h.source_path}#{h.locator}" for h in trace.rag_hits]
                if scope.scoped:
                    detail["scope"] = scope.describe()
            # A product the Ask resolved is never "starved": the words may
            # have scored nothing — a misspelling, "ok what about travel then"
            # — but the product's pages are loaded and the customer named it.
            starved = reason.startswith(NO_MATCH_PREFIXES) and not trace.rag_hits and not ask.resolved
            # A situational phrasing scores badly on lexical overlap — "my place
            # was broken into" shares almost nothing with a page about contents
            # cover — so the confidence floor calls it starved and the composer
            # stops before it ever looks at a section. But if the question named
            # a benefit in the customer's own words, and a page we loaded can
            # produce that benefit, the corpus plainly does hold the answer.
            implied = expand_vocabulary(question, load_vocabulary(settings.bundle_path))
            if starved and implied:
                servable = {b for page in pages for b, _ in find_tokens(page.body)} & implied
                if servable:
                    starved = False
                    detail["vocabulary_rescued"] = sorted(servable)
            detail["starved"] = starved

        # Customer-specific data only ever comes from the system of record.
        version = (product.frontmatter.version_in_force or "") if product else ""
        tier = "UNKNOWN"
        with trace.stage("sor") as detail:
            if session.auth_level == AuthLevel.authenticated and session.policy is not None:
                try:
                    budget.charge_tool()
                    summary = policy_summary(session)
                    version, tier = summary.version, summary.tier
                    trace.sor_calls.append(f"policy_summary({summary.policy_id})")
                    detail["policy"] = summary.as_fields()
                except NotEntitled as exc:
                    trace.note(f"SOR refused: {exc}")
                    detail["refused"] = str(exc)
            else:
                detail["skipped"] = "unauthenticated session"

        if not version and product is not None:
            version = product.frontmatter.version_in_force or ""

        with trace.stage("compose") as detail:
            # The keyword classifier catches "which plan should I buy" and
            # misses "what cover do you recommend I take" — same regulated
            # request, different verb, and the eval suite counts the misses.
            # An input screen that reached `advice` closes that gap by routing
            # the turn the same way, rather than only noting it on the trace.
            needs_advice = advice_required(bundle, question, [p.id for p in pages]) or incoming.acted_on(
                "advice"
            )
            composition = compose(
                bundle=bundle,
                pages=pages,
                question=question,
                session=session,
                product=product,
                version=version,
                tier=tier,
                advice_required=needs_advice,
                top_score=top_score,
                idf=term_idf(bundle),
                benefits=expand_vocabulary(question, load_vocabulary(settings.bundle_path)),
                no_confident_match=starved,
                ask=ask,
                # The section-level half of the dense layer. `frontmatter_filter`
                # pooled these to page scores to decide *which pages* to read;
                # this is the same hits deciding *which section of them* answers.
                dense=vector.by_section if vector is not None else None,
                dense_floor=settings.vector_floor,
            )
            draft = composition.answer
            trace.composer = "deterministic"
            trace.figures_resolved = composition.figures_detail
            trace.unresolved = draft.unresolved
            detail["sections"] = [f"{s.page.id}#{s.heading}" for s in composition.selections]
            detail["tier"] = tier
            detail["version"] = version

        # Generation (§H.1). The model phrases what the composer established;
        # it is never asked to supply a fact. Whatever it writes goes through
        # the same gates below, so a provider that drifts is caught rather
        # than trusted — and a provider that is down degrades to the
        # deterministic prose instead of failing the question.
        # The rewrite and the entailment judge both read the composed draft —
        # the judge its claims, the rewrite its prose — and neither needs the
        # other's output. In series they were 12 s and 10 s of a 30 s answer.
        # The judge is started now on the draft's claims, memoised at the
        # provider call, so when the gates run below the identical judging
        # call returns from the memo instead of the model.
        judge = None
        judge_warmup = None
        if provider.name != "deterministic" and getattr(provider, "classify", None) is not None:
            judge = _memoised(provider.classify)
            if draft.claims and not draft.handoff:
                # A plain daemon thread, not a pool. A module-level
                # ThreadPoolExecutor registers an atexit handler, and that
                # handler crashed the interpreter at the end of a test run
                # ("recursive_mutex lock failed") — a background pool that
                # outlives the request is a liability the turn does not need.
                # The judge reads `claims`, which the rewrite never touches;
                # it only rebinds the prose.
                judge_warmup = threading.Thread(
                    target=_prewarm_judge,
                    args=(
                        GateContext(
                            answer=draft,
                            bundle=bundle,
                            session=session,
                            question=question,
                            loaded_page_ids=[p.id for p in pages],
                            raw_root=raw_root,
                            today=session.today,
                            judge=judge,
                            ask=ask,
                        ),
                    ),
                    daemon=True,
                )
                judge_warmup.start()

        if not draft.handoff and draft.answer:
            with trace.stage("generate") as detail:
                detail["provider"] = provider.name
                draft_facts = Draft(
                    question=question,
                    prose=draft.answer,
                    claims=draft.claims,
                    figures=draft.figures,
                    unresolved=list(draft.unresolved),
                    product=product.frontmatter.title if product is not None else None,
                    carried_from=resolution.carried_from,
                    style=OVERVIEW_STYLE if ask.scope == "overview" else "",
                )
                rewrite = provider.rewrite(draft_facts)
                fell_back = ""
                if rewrite is not None and not draft_facts.accepts(rewrite.answer):
                    # The model dropped a figure the composer had established.
                    # Keep the wording that is known to carry it.
                    fell_back = "dropped a resolved figure"
                    rewrite = None
                elif rewrite is None and provider.name != "deterministic":
                    fell_back = "unavailable"
                if rewrite is None:
                    trace.composer = (
                        provider.name
                        if provider.name == "deterministic"
                        else f"{provider.name} ({fell_back} — kept deterministic prose)"
                    )
                    detail["applied"] = False
                    if fell_back:
                        detail["fell_back"] = fell_back
                else:
                    draft.answer = rewrite.answer
                    for item in rewrite.unresolved:
                        if item not in draft.unresolved:
                            draft.unresolved.append(item)
                    trace.composer = f"{rewrite.provider}:{rewrite.model}"
                    detail["applied"] = True
                    detail["model"] = rewrite.model
                    if rewrite.tokens:
                        budget.charge_tokens(rewrite.tokens)
                        detail["tokens"] = rewrite.tokens

        # The presentation layer: the same verified sentences, organised. An
        # introduction gets an opening line, "What it covers" as a list, the
        # route, and a closing question built from the same chips it offers.
        # Every other answer keeps its shape and gets the chips alone.
        if not draft.handoff and draft.answer:
            draft.suggestions = suggest_next(bundle, ask, product, clarifying=draft.clarifying)
            with trace.stage("present") as detail:
                if ask.scope == "overview" and product is not None:
                    draft.answer = present_overview(
                        draft.answer, product, closing_question(draft.suggestions)
                    )
                    detail["shape"] = "introduction"
                elif product is not None:
                    # A long answer becomes a digest of its sections, each a
                    # chip away in full; a short one is bulleted where the
                    # compiler flattened an enumeration. The section chips
                    # come first, so "tap a part below" is true.
                    triples = [(s.page.id, s.heading, s.body) for s in composition.selections]
                    short = digest(draft.answer, product, bundle, ask, triples, figures=len(draft.figures))
                    drill = section_chips(bundle, product, ask.intent)
                    if short is not None:
                        draft.answer = short
                        detail["shape"] = "digest"
                        draft.suggestions = (drill + draft.suggestions)[:7]
                    else:
                        draft.answer = bulletise(draft.answer)
                        detail["shape"] = "bulleted"
                        faq_answer = any(s.page.id.endswith("/faq") for s in composition.selections)
                        if faq_answer and drill:
                            draft.suggestions = (drill[:3] + draft.suggestions)[:6]
                else:
                    draft.answer = bulletise(draft.answer)
                    detail["shape"] = "bulleted"

        if incoming.acted_on("distress"):
            # Routed to a person rather than answered. What a customer in
            # crisis should actually be told is a compliance decision, not one
            # to invent here — this sets the route and records why.
            draft.handoff = True
            trace.note("distress flagged on the incoming turn — routed to a person")

        # Not on a handoff: "limits vary by plan tier" tacked onto "let me pass
        # you to a colleague" offers a figure the turn never had, and the same
        # sentence was landing on refusals where there is no limit to vary.
        if (
            tier == "UNKNOWN"
            and product is not None
            and not draft.handoff
            and ask.scope != "overview"
            # Only where a figure was the point: on an exclusions or claims
            # answer the line offered a number the question never asked for.
            and ask.intent in (Intent.limit, Intent.coverage, Intent.price, Intent.unknown)
            and _tier_specific(product, bundle)
        ):
            draft.unresolved.append("plan tier unknown — sign in for tier-specific limits")
            draft.answer += (
                "\n\nLimits vary by plan tier, so sign in or tell me your tier and "
                "I'll give you the exact figure."
            )

    except BudgetExhausted as exc:
        # A defined exit, never a loop (§F.3).
        trace.note(f"budget exhausted on {exc.resource}")
        trace.budget = budget.snapshot()
        trace.delivered = False
        envelope = AnswerEnvelope(
            answer=GroundedAnswer(answer=HANDOFF, handoff=True, confidence=0.0, unresolved=[str(exc)]),
            gates=[],
            delivered=False,
            trace_id=trace.trace_id,
        )
        trace.answer = envelope.answer.model_dump(mode="json")
        return envelope, trace

    # Screened against the evidence it was built from, after generation so the
    # text reviewed is the text that would ship. The deterministic gates below
    # still run on it: this catches what they cannot read, not what they check.
    with trace.stage("guardrail-output") as detail:
        # The channel render belongs in the evidence, not just in the answer.
        # Contact details are resolved from the session's channel binding
        # rather than from a page, so without them the reviewer sees a URL and
        # a hotline that appear in the draft and nowhere in its evidence — and
        # correctly, by the rules it was given, calls that leakage. Measured:
        # it did exactly that, at 0.95, on "what is the trip cancellation
        # limit", which contains no personal data at all.
        render = draft.channel_render
        contacts: list[str] = []
        if render is not None:
            for value in (render.landing, *(getattr(render, "surfaces", None) or [])):
                if value:
                    contacts.append(f"- route link ({render.name or render.channel}): {value}")
            hotline = getattr(render, "hotline", None)
            if hotline:
                contacts.append(f"- route hotline ({render.name or render.channel}): {hotline}")
        evidence = "\n".join(
            [*(f"- {c.text}  (source: {c.source_id})" for c in draft.claims)]
            + [f"- {f.label}: {f.text}" for f in draft.figures]
            + contacts
            + [f"- NOT ESTABLISHED: {u}" for u in draft.unresolved]
        )
        outgoing = guard.screen_output(
            question, evidence, draft.answer, [f.text for f in draft.figures if f.text]
        )
        detail["risk"] = outgoing.risk.value
        detail["checked_by"] = outgoing.checked_by
        if outgoing.degraded:
            detail["degraded"] = outgoing.degraded
        if outgoing.findings:
            detail["findings"] = [f"{f.category}:{f.source}@{f.confidence:.2f}" for f in outgoing.findings]
            detail["scores"] = [str(sc) for sc in outgoing.scores]
    trace.gates.append(outgoing.as_gate("guardrail-output"))

    # A price with no premium figure bound to it is not a price; it is the
    # FAQ's description of the plan, and "How much does Tiq CashSaver cost?"
    # was answered with one. The owner's rule: say how to get the real price.
    if decision.layer3 is Layer3.price and not _priced(draft):
        guide = guidance(bundle, raw_root, Intent.price, product, question)
        guide.advice_flag = draft.advice_flag
        trace.blocked_draft = draft.answer
        trace.note("no premium figure bound: the quote steps instead of the draft")
        return _finish(trace, guide, bundle, session, question, raw_root, [p.id for p in pages], judge, ask)

    with trace.stage("gates") as detail:
        ctx = GateContext(
            answer=draft,
            bundle=bundle,
            session=session,
            question=question,
            loaded_page_ids=[p.id for p in pages],
            raw_root=raw_root,
            today=session.today,
            # Meaning is judged where a model is configured; on the
            # deterministic path the lexical test stands.
            judge=judge,
            ask=ask,
        )
        if judge_warmup is not None:
            # Wait for the pre-warm so the gate's call is a memo hit. A warm-up
            # that raised, or timed out, is simply a cold memo — the gate then
            # calls the model itself and the turn is slower, never wrong.
            judge_warmup.join(timeout=60)
        # Guardrail verdicts are already on the trace; extending rather than
        # replacing keeps them in the list `blocked()` reads, so an output the
        # screen refused cannot be delivered by a clean sweep of the seven.
        results = [*trace.gates, *run_gates(ctx)]
        trace.gates = results
        detail["failed"] = [r.gate for r in results if r.blocking]

    trace.budget = budget.snapshot()

    if blocked(results) or _fail_closed(outgoing, settings):
        trace.blocked_draft = draft.answer
        trace.delivered = False
        trace.note("delivery blocked by a verification gate")
        # `answerability` means precisely "nothing loaded settles this", which
        # is a thing the customer can be told. Every other gate means "we
        # caught a problem with the draft", which is not — a customer told the
        # premium is not published can go and get a quote; a customer told that
        # about a groundedness failure has been told something false.
        failed = {r.gate for r in results if r.blocking}
        # Tier 2 of the routing. An answerability refusal has established
        # something specific — the corpus does not carry this — so it can name
        # the page that does: the promotions page for an offer, the plan's own
        # page for a published figure the composer could not reach. Any other
        # gate means the draft was faulty, which says nothing about where the
        # answer lives, so those get the one destination that is always true
        # and nothing that would imply the corpus was asked and found wanting.
        intent = ask.intent if ask is not None else classify(question)
        loaded_ids = [p.id for p in pages]
        # The owner's rules (v2.5). A number the draft could not bind is
        # dropped and the rest is delivered as the generic reply — unless the
        # customer asked who can buy, where the age requirement is the answer
        # and is bound to the page that states it. Where nothing loaded
        # settles the question, the reply is the steps to the real answer,
        # not a colleague. Every other gate means the draft itself was faulty,
        # which is not a thing to reshape: that is still a handoff.
        # `guardrail-output` joins the soft set: the draft carried something the
        # screen refuses to show — an external link, a leaked clause — and the
        # steps to the answer carry neither. It never joins the trim: a draft
        # the screen refused is not reshaped, it is replaced.
        soft = failed <= {"numeric-binding", "answerability", "guardrail-output"} and not _fail_closed(
            outgoing, settings
        )
        # Trimmed only where the product is settled — named, carried or
        # inferred. With no product, the draft is whichever pages a lexical
        # tie sorted first, and "How do I contact my agent?" was delivered a
        # trimmed Business Owners Super Suite overview. That turn gets the
        # steps instead.
        settled_product = decision.layer2 in (Layer2.named, Layer2.carried, Layer2.inferred)
        if soft and failed == {"numeric-binding"} and settled_product:
            orphans = unbound_spans(ctx)
            if intent is Intent.eligibility:
                aged = _bind_ages(draft, orphans)
                if aged is not None:
                    trace.note("age figures bound to the eligibility page that states them")
                    aged_envelope, aged_trace = _finish(
                        trace, aged, bundle, session, question, raw_root, loaded_ids, judge, ask
                    )
                    if aged_envelope.delivered:
                        return aged_envelope, aged_trace
            # Trimmed only where the composer quoted the pages: an unbound
            # number there is page text the gate cannot tie to a row. A
            # model's draft with an unbound number is a model that invented
            # one, and the rest of its draft is not trusted line by line.
            generic = (
                _strip_unbound(draft, orphans, _wording_pointer(bundle, product))
                if trace.composer == "deterministic"
                else None
            )
            if generic is not None:
                trace.note(f"unbound figures removed and the rest delivered: {sorted(set(orphans))}")
                return _finish(trace, generic, bundle, session, question, raw_root, loaded_ids, judge, ask)
        if soft:
            guide = guidance(bundle, raw_root, intent, product, question)
            guide.advice_flag = draft.advice_flag
            guide.unresolved = [f"{r.gate}: {r.detail}" for r in results if r.blocking]
            trace.note(f"the steps to the answer instead of the draft: {', '.join(sorted(failed))}")
            return _finish(trace, guide, bundle, session, question, raw_root, loaded_ids, judge, ask)
        envelope = AnswerEnvelope(
            answer=GroundedAnswer(
                answer=f"{HANDOFF} {DESTINATIONS[Desk.contact].sentence}",
                handoff=True,
                destinations=[CONTACT_LINK],
                # Preserve what the turn established: an advice question that
                # gets blocked still needs the adviser handoff downstream.
                advice_flag=draft.advice_flag,
                confidence=0.0,
                unresolved=[f"{r.gate}: {r.detail}" for r in results if r.blocking],
            ),
            gates=results,
            delivered=False,
            trace_id=trace.trace_id,
        )
        trace.answer = envelope.answer.model_dump(mode="json")
        return envelope, trace

    trace.answer = draft.model_dump(mode="json")
    return AnswerEnvelope(answer=draft, gates=results, delivered=True, trace_id=trace.trace_id), trace


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
