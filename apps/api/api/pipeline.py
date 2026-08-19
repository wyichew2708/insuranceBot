"""Loop 1 — Serve (§G).

    route → read wiki / RAG / SOR → generate → gates → answer

Its second job is to emit good telemetry, because that is what powers Loop 4
(Evolve). Every decision the loop makes is recorded on the trace, including
the pages it considered and rejected.
"""

from __future__ import annotations

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
from okf.tables import find_tokens

from api.compose import compose
from api.gates_ext import advice_required
from api.retrieval import frontmatter_filter, needs_rag, rag_search, wiki_read
from api.settings import Settings
from api.sor import NotEntitled, policy_summary
from okf import Bundle, Page, PageType

HANDOFF = (
    "I'd rather not answer that from memory. I'm passing you to a colleague who can "
    "confirm the details against your policy."
)


def _product_page(pages: list[Page]) -> Page | None:
    """The canonical product page among those loaded — the one carrying the
    channel bindings and version in force."""
    products = [p for p in pages if p.frontmatter.type == PageType.product]
    if not products:
        return None
    # Prefer the shallowest id: product/general/travel over .../travel/benefits.
    return sorted(products, key=lambda p: (p.id.count("/"), p.id))[0]


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
            reason = needs_rag(question, admitted, session, settings.confidence_floor)
            detail["reason"] = reason or "not needed"
            if reason:
                trace.rag_used = True
                trace.rag_reason = reason
                budget.charge_tool()
                trace.rag_hits = rag_search(raw_root, question, session)

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
            needs_advice = advice_required(bundle, question, [p.id for p in pages])
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
            )
            draft = composition.answer
            trace.composer = "deterministic"
            trace.figures_resolved = composition.figures_detail
            trace.unresolved = draft.unresolved
            detail["sections"] = [f"{s.page.id}#{s.heading}" for s in composition.selections]
            detail["tier"] = tier
            detail["version"] = version

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
        results = run_gates(ctx)
        trace.gates = results
        detail["failed"] = [r.gate for r in results if r.blocking]

    trace.budget = budget.snapshot()

    if blocked(results):
        trace.blocked_draft = draft.answer
        trace.delivered = False
        trace.note("delivery blocked by a verification gate")
        envelope = AnswerEnvelope(
            answer=GroundedAnswer(
                answer=HANDOFF,
                handoff=True,
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
