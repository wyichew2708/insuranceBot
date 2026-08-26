"""Loop 1 — Serve (§G).

    route → read wiki / RAG / SOR → generate → gates → answer

Its second job is to emit good telemetry, because that is what powers Loop 4
(Evolve). Every decision the loop makes is recorded on the trace, including
the pages it considered and rejected.
"""

from __future__ import annotations

from pathlib import Path

from harness import (
    AnswerEnvelope,
    AuthLevel,
    Budget,
    BudgetExhausted,
    GateContext,
    GroundedAnswer,
    Session,
    Trace,
    blocked,
    run_gates,
)
from harness.intent import Intent, classify, smalltalk_kind
from okf.tables import find_tokens

from api.compose import compose
from api.directory import answer as directory_answer
from api.gates_ext import advice_required
from api.guardrails import Guard, Screening, guard_for
from api.llm import Draft, provider_for
from api.retrieval import (
    NO_MATCH_PREFIXES,
    frontmatter_filter,
    needs_rag,
    rag_search,
    unsupported_term,
    wiki_read,
)
from api.settings import Settings
from api.sor import NotEntitled, policy_summary
from okf import (
    Bundle,
    Page,
    PageType,
    expand_abbreviations,
    expand_vocabulary,
    load_abbreviations,
    load_vocabulary,
    term_idf,
)

HANDOFF = (
    "I'd rather not answer that from memory. I'm passing you to a colleague who can "
    "confirm the details against your policy."
)

#: A turn the input screen refused. Deliberately says nothing about *why* — a
#: refusal that explains which rule it tripped is a probe's reward, and tells
#: the next attempt what to avoid.
REFUSED = (
    "I can't help with that one. If you have a question about a policy or a product, "
    "I'm happy to take it — otherwise I can put you through to a colleague."
)


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
) -> tuple[AnswerEnvelope, Trace]:
    """Gate an answer produced without the retrieve-and-compose path.

    The short-circuits still go through the gates. A greeting will skip every
    one and a directory listing will pass reference-integrity on the products
    it named — but a turn that bypassed verification silently would look, on
    the trace, exactly like one that passed it.
    """
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


def answer_question(
    bundle: Bundle, question: str, session: Session, settings: Settings
) -> tuple[AnswerEnvelope, Trace]:
    trace = Trace(question=question, session_id=session.session_id, channel=session.channel.value)
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
    provider = provider_for(settings)
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
        return _finish(trace, answer, bundle, session, question, raw_root, [])

    # A shopper, not a questioner. "what life products" and "looking for a CI
    # plan" ask what exists; retrieval finds the best single page and answers
    # from its prose, which is how "what life products" came back as Products
    # Liability. The bundle already knows every product and its line of
    # business — this reports that rather than ranking it.
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
            )

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

    try:
        with trace.stage("frontmatter-filter") as detail:
            admitted = frontmatter_filter(bundle, question, session, trace, settings.candidate_floor)
            detail["admitted"] = len(admitted)
            detail["rejected"] = len(trace.candidates) - len(admitted)

        with trace.stage("wiki-read") as detail:
            pages = wiki_read(bundle, admitted, trace, budget, settings.wiki_read_limit, session.today)
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
                trace.rag_hits = rag_search(
                    raw_root,
                    question,
                    session,
                    idf=term_idf(bundle),
                    must_include=unsupported_term(bundle, question, admitted),
                )
            starved = reason.startswith(NO_MATCH_PREFIXES) and not trace.rag_hits
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
        if not draft.handoff and draft.answer:
            with trace.stage("generate") as detail:
                detail["provider"] = provider.name
                draft_facts = Draft(
                    question=question,
                    prose=draft.answer,
                    claims=draft.claims,
                    figures=draft.figures,
                    unresolved=list(draft.unresolved),
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

        if incoming.acted_on("distress"):
            # Routed to a person rather than answered. What a customer in
            # crisis should actually be told is a compliance decision, not one
            # to invent here — this sets the route and records why.
            draft.handoff = True
            trace.note("distress flagged on the incoming turn — routed to a person")

        if tier == "UNKNOWN" and product is not None and _tier_specific(product, bundle):
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

    with trace.stage("gates") as detail:
        ctx = GateContext(
            answer=draft,
            bundle=bundle,
            session=session,
            question=question,
            loaded_page_ids=[p.id for p in pages],
            raw_root=raw_root,
            today=session.today,
        )
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
        envelope = AnswerEnvelope(
            answer=GroundedAnswer(
                answer=HANDOFF,
                handoff=True,
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
