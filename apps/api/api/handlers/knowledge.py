"""Knowledge workflow extracted from the serve pipeline."""

from __future__ import annotations

import threading

from harness import (
    AnswerEnvelope,
    AuthLevel,
    BudgetExhausted,
    GateContext,
    GroundedAnswer,
    Trace,
    blocked,
    run_gates,
)
from harness.gates import unbound_spans
from harness.intent import Intent, classify
from harness.trace import LoadedPage
from okf.names import plan_tier_in
from okf.page import Page
from okf.tables import find_tokens

from api.clarify import (
    clarification,
    did_you_mean,
    lexical_clarification,
    open_clarification,
    typo_clarification,
)
from api.compose import compose
from api.gates_ext import advice_required
from api.guidance import guidance
from api.handlers.contracts import Evidence, Turn
from api.handlers.shared import (
    CONTACT_LINK,
    HANDOFF,
    OVERVIEW_STYLE,
    _bind_ages,
    _fail_closed,
    _finish,
    _memoised,
    _prewarm_judge,
    _priced,
    _product_page,
    _root_page,
    _strip_unbound,
    _tier_specific,
    _wording_pointer,
)
from api.llm import Draft
from api.present import bulletise, digest, name_the_plan, present_overview, section_chips
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
from api.router import Layer2, Layer3
from api.sor import NotEntitled, policy_summary
from api.suggest import closing_question, suggest_next
from api.vectors import searcher_for
from okf import (
    DESTINATIONS,
    Desk,
    expand_vocabulary,
    load_vocabulary,
    term_idf,
)


def _lists_every_plan(text: str, product: Page) -> bool:
    """Does the answer already give a figure per plan?"""
    plans = [t for t in product.frontmatter.plan_tiers if t and t != "ALL"]
    if len(plans) < 2:
        return False
    labels = [" ".join(w.capitalize() for w in t.split("-")) for t in plans]
    return sum(1 for label in labels if label in text) >= 2


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace]:
    bundle = turn.bundle
    question = turn.question
    session = turn.session
    settings = turn.settings
    trace = turn.trace
    budget = turn.budget
    raw_root = turn.raw_root
    provider = turn.provider
    guard = turn.guard
    incoming = turn.incoming
    ask = turn.ask
    decision = turn.decision
    seeking_advice = turn.seeking_advice
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
        turn.require(Evidence.wiki)
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
        # ...unless the word we have never seen is one letter away from one we
        # have. The comment above is right that the model resolves "trvael
        # insurance" before the tie — but only where a model is configured, and
        # the deterministic path is what CI, the eval suites and every offline
        # deployment run. Without this, "mediacl insurance" was refused outright.
        if missing_line and not focus_override:
            meant = did_you_mean(bundle, missing_line)
            asked = typo_clarification(bundle, meant) if meant else None
            if asked is not None:
                with trace.stage("clarify") as detail:
                    detail["from"] = "misspelt product name"
                    detail["typo"] = missing_line
                    detail["options"] = meant[:4]
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
                turn.require(Evidence.raw)
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
                    turn.require(Evidence.sor)
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

        # A customer who names the plan has told us the tier, and that is the
        # tier the figures should come from. Only the system of record can say
        # which plan they *hold*, so a named plan never overrides one read from
        # a policy — but for everyone else the alternative was "[unavailable]"
        # and an invitation to sign in, on a question that named its own answer.
        if tier == "UNKNOWN" and product is not None:
            asked_tier = plan_tier_in(question, product.frontmatter.plan_tiers)
            if asked_tier and bundle.tables.tiers_for(bundle.product_key(product), version).count(asked_tier):
                tier = asked_tier
                trace.note(f"plan named in the question: {tier}")

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
                    carried_from=turn.carried_from,
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
            draft.suggestions = suggest_next(
                bundle,
                ask,
                product,
                clarifying=draft.clarifying,
                today=session.today,
            )
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

        if ask.section and "cancellation by you" in ask.section[1].lower() and not draft.handoff:
            # Keep the selected clause's heading: "terminate" in its body is
            # cancellation by the customer, not the automatic termination list.
            draft.answer = "Cancellation by you:\n\n" + draft.answer

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
            # The tier is unknown and that stays on the record either way: it
            # is what a reviewer reads to know the answer was not personalised.
            draft.unresolved.append("plan tier unknown — sign in for tier-specific limits")
            # The sentence, though, is only worth saying when the figures were
            # not already given per plan. "Entry $5,000, Savvy $5,000, Luxury
            # $10,000" answers the question for every plan there is; following
            # it with "tell me your tier and I'll give you the exact figure"
            # reads as though it had not.
            if not _lists_every_plan(draft.answer, product):
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
        soft = failed <= turn.soft_gates and not _fail_closed(outgoing, settings)
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

    # The gates have read every claim against the span it came from, so the
    # tier can now be named in the prose. Before them it could not: the span
    # says "for the plan tier held", and a sentence that no longer matched its
    # evidence was refused by groundedness — correctly.
    # The plans this product is sold in: the catalogue's list where the page
    # declares one, otherwise whatever its table actually has rows for.
    plans = list(product.frontmatter.plan_tiers) if product else []
    if not plans and product is not None:
        plans = bundle.tables.tiers_for(bundle.product_key(product), version)
    draft.answer = name_the_plan(draft.answer, tier, plans)
    trace.answer = draft.model_dump(mode="json")
    return AnswerEnvelope(answer=draft, gates=results, delivered=True, trace_id=trace.trace_id), trace
