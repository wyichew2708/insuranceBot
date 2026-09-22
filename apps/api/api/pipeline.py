"""Loop 1 — Serve (§G).

    route → read wiki / RAG / SOR → generate → gates → answer

Its second job is to emit good telemetry, because that is what powers Loop 4
(Evolve). Every decision the loop makes is recorded on the trace, including
the pages it considered and rejected.
"""

from __future__ import annotations

import dataclasses
import re

from harness import (
    AnswerEnvelope,
    Budget,
    GroundedAnswer,
    Session,
    Trace,
)
from harness.ask import Ask, read_ask, read_section
from harness.contracts import Link
from harness.gates import ADVICE_SEEKING_RE
from harness.intent import OUT_OF_CORPUS, Intent, classify, smalltalk_kind
from harness.trace import StageListener

from api.domain import off_domain
from api.entities import EntitySlots, extract_slots
from api.guardrails import (
    Guard,
    guard_for,
    medical_emergency,
    named_third_party,
    redact_pii,
    third_party_screening,
)
from api.guidance import wants_a_recommendation
from api.handlers import dispatch, dispatch_required
from api.handlers.compare import requested as comparison_requested
from api.handlers.contracts import Turn
from api.handlers.shared import CANCEL_CONTEXT_RE, REFUND_FOLLOWUP_RE, _ask_from_trace, _fail_closed, _refusal
from api.handlers.shared import REFUSED as REFUSED
from api.handlers.shared import _strip_unbound as _strip_unbound
from api.language import REFUSAL as LOCALISED_REFUSAL
from api.language import detect_language
from api.llm import LLMProvider, provider_for
from api.memory import ConversationState, SessionMemory
from api.navigation import greeting_map
from api.present import response_preview
from api.reference import resolve
from api.router import Layer1
from api.router import route as route_turn
from api.settings import Settings
from api.split import split_questions
from api.suggest import QUESTIONS, product_label, suggest_next
from api.understand import Understanding, understand, worth_resolving
from okf import (
    Bundle,
    PageType,
    expand_abbreviations,
    load_abbreviations,
)

_MEMORIES: dict[str, SessionMemory] = {}


def memory_for(settings: Settings) -> SessionMemory:
    """One memory per state directory, for the life of the process."""
    # `auto` is on in the API server and off everywhere else: a test or a
    # batch evaluation constructs Settings directly and must stay stateless,
    # or one case's question would carry into the next. `main.py` resolves
    # `auto` to `on` when it loads settings for the served process.
    key = (
        f"{settings.state_dir}|{settings.memory.lower()}|"
        f"{settings.memory_max_turns}|{settings.memory_idle_ttl_seconds}"
    )
    if key not in _MEMORIES:
        _MEMORIES[key] = SessionMemory(
            settings.state_dir,
            enabled=settings.memory.lower() == "on",
            max_turns=settings.memory_max_turns,
            idle_ttl_seconds=settings.memory_idle_ttl_seconds,
        )
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
    turns = list(history) if history is not None else recalled.questions
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
        envelope, trace = _answer_turn(
            bundle,
            question,
            session,
            settings,
            turns,
            provider,
            on_stage,
            state=recalled.state if history is None else None,
        )
    context_trace = traces[-1] if len(parts) > 1 else trace
    ask = _ask_from_trace(context_trace)
    if ask is not None:
        # Use the resolved route's product when retrieval inferred one; resolve
        # page identity from the current bundle, never a stale persisted page.
        product_key = context_trace.route.get("product") or ask.product
        focus = next(
            (
                p
                for p in bundle.pages.values()
                if p.frontmatter.type is PageType.product
                and p.id.count("/") == 2
                and bundle.product_key(p) == product_key
            ),
            None,
        )
        if focus is not None:
            ask = dataclasses.replace(ask, product=product_key, product_page=focus.id)
            answered = recalled.state.answered_topics.get(product_key or "", frozenset())
            current = envelope.answer
            if current.guidance and not current.clarifying:
                current.suggestions = suggest_next(
                    bundle,
                    ask,
                    focus,
                    today=session.today,
                    answered=answered,
                )
            elif answered:
                hidden = {
                    QUESTIONS[t].format(product=product_label(focus)) for t in answered if t in QUESTIONS
                }
                current.suggestions = [s for s in current.suggestions if s not in hidden]
    if envelope.answer.smalltalk and smalltalk_kind(question) in {"greeting", "capability"}:
        envelope.map = greeting_map(bundle, session)
    # Explicit history replaces server state for entity inputs too. Only screened
    # turns update slots; refusal text must not become future tool parameters.
    slots = recalled.state.slots if history is None else EntitySlots()
    slot_product = recalled.state.product if history is None else None
    slot_questions = [question] if history is None else [*history, question]
    if trace.handler != "refusal":
        for slot_question in slot_questions:
            clean_question, _ = redact_pii(slot_question)
            slot_ask = read_ask(bundle, clean_question)
            selected = slot_ask.product or slot_product
            if slot_ask.product and slot_product and slot_ask.product != slot_product:
                slots = EntitySlots()
            slot_product = selected
            slot_page = next(
                (
                    page
                    for page in bundle.pages.values()
                    if page.frontmatter.type is PageType.product
                    and page.id.count("/") == 2
                    and bundle.product_key(page) == selected
                ),
                None,
            )
            slot_version = slot_page.frontmatter.version_in_force if slot_page else None
            tiers = tuple(
                dict.fromkeys(
                    row.tier
                    for row in bundle.tables.rows
                    if row.product == selected and row.version == slot_version and row.tier != "ALL"
                )
            )
            slots = extract_slots(clean_question, slots, tiers=tiers)
    trace.slots = slots.model_dump(mode="json", exclude_none=True)
    envelope.summary = memory.remember(session.session_id, question, envelope, ask, slots=slots)
    if envelope.answer.handoff:
        envelope.handover_summary = " ".join(filter(None, (recalled.summary, envelope.summary)))
    envelope.language = detect_language(question)
    trace.language = envelope.language
    if trace.handler == "refusal" and trace.language != "en":
        envelope.answer.answer = LOCALISED_REFUSAL[trace.language]
    if envelope.delivered and not (ask and ask.full) and envelope.answer.table is None:
        envelope.preview = response_preview(envelope.answer.answer)
    trace.answer = envelope.answer.model_dump(mode="json")
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
    part_handlers = [t.handler for t in traces]
    trace = traces[0]
    trace.handler = "compound"
    trace.route = {**trace.route, "parts": str(len(parts))}
    for index, (part, part_trace, part_envelope) in enumerate(
        zip(parts, traces, envelopes, strict=True), start=1
    ):
        route = part_trace.route
        summary = f"{route.get('layer1', '')}/{route.get('layer2', '')}/{route.get('layer3', '')}"
        trace.route[f"part{index}"] = summary
        trace.route[f"part{index}_handler"] = part_handlers[index - 1]
        trace.note(f"part {index} {part!r}: routed {summary}, delivered={part_envelope.delivered}")
    trace.gates = [g for e in envelopes for g in e.gates]
    delivered = any(e.delivered for e in envelopes)
    trace.delivered = delivered
    trace.answer = merged.model_dump(mode="json")
    return AnswerEnvelope(
        answer=merged, gates=trace.gates, delivered=delivered, trace_id=trace.trace_id
    ), trace


def _answer_turn(
    bundle: Bundle,
    question: str,
    session: Session,
    settings: Settings,
    history: list[str] | None,
    provider: LLMProvider,
    on_stage: StageListener | None = None,
    *,
    state: ConversationState | None = None,
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
    current_ask = read_ask(bundle, question)
    if state is not None and state.product and not current_ask.resolved and not current_ask.family:
        page = bundle.get(state.product_page) if state.product_page else None
        if page is not None and bundle.product_key(page) == state.product:
            ask = current_ask.carried_from(
                Ask(
                    question="",
                    product=state.product,
                    product_page=page.id,
                )
            )
    if ask.product_page and ask.section is None:
        section = read_section(bundle, ask.product_page, question)
        if section is not None:
            ask = dataclasses.replace(ask, section=section, scope="specific")
    recent = (history or [])[-3:]
    if REFUND_FOLLOWUP_RE.search(question) and any(CANCEL_CONTEXT_RE.search(t) for t in recent):
        ask = dataclasses.replace(ask, intent=Intent.payment)
        trace.note("read as a refund question: the conversation is about a cancellation")
    if "introduce" in question.lower() and current_ask.resolved:
        ask = dataclasses.replace(ask, intent=Intent.coverage, scope="overview")
    if comparison_requested(bundle, question, ask):
        ask = dataclasses.replace(ask, intent=Intent.compare)
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

    turn = Turn(
        bundle, question, session, settings, trace, budget, raw_root, provider, guard, incoming, ask, decision
    )

    # After screening, before retrieval. A greeting is not a question the
    # corpus can fail to answer, and routing one through retrieval replies to
    # "hi" with "I could not establish that from our approved product pages" —
    # which reads as a broken bot, not a careful one. It is screened first
    # because "hi" with an injection payload stapled to it is not a greeting.
    kind = smalltalk_kind(question)
    if kind is not None:
        return dispatch_required("smalltalk", turn)

    # Somebody else's record. The rule layer screens the phrasings that need no
    # catalogue ("my friend's policy"); this is the one that does, because a
    # capitalised name is a product as often as it is a person. Refused rather
    # than guided: the account guidance says "log in and open My Policies",
    # which is the right answer for your own policy and an instruction to go
    # looking for someone else's.
    if named_third_party(bundle, question):
        return _refusal(trace, third_party_screening(), "guardrail-input", "a third party's record")

    # Current approved corpora are English-only. Preserve the input/privacy
    # screen and emergency route, then offer a person in the customer's language.
    if detect_language(question) != "en":
        if medical_emergency(question):
            return dispatch_required("emergency", turn)
        return dispatch_required("language", turn)

    # Not a question about insurance at all. Screened here for the same reason
    # smalltalk is: the corpus cannot fail to answer a question that was never
    # asked of it, and letting one through costs a page budget to arrive at a
    # paragraph of policy wording about something else entirely. `gate_domain`
    # blocks the reply, so nothing here is counted as an answer.
    if off_domain(bundle, question, ask) and ask.intent not in {Intent.compare, Intent.browse}:
        return dispatch_required("domain", turn)

    # A recommendation, which no page can give and a licensed adviser must.
    # Routed here rather than flagged after composition: retrieval for "should
    # I buy this or that" returns the pages either plan happens to rank for,
    # and the adviser sentence was being appended to four paragraphs of them.
    if wants_a_recommendation(question):
        return dispatch_required("advice", turn)

    if ask.intent is Intent.compare:
        if re.search(r"\b(?:it|this|that)\b", question, re.I):
            previous = state.product_page if state is not None else None
            if previous is None:
                previous = next(
                    (
                        prior.product_page
                        for earlier in reversed(history or [])
                        if (prior := read_ask(bundle, earlier)).resolved
                    ),
                    None,
                )
            turn.comparison_base = previous
        return dispatch_required("knowledge.compare", turn)

    situation_answer = dispatch("travel_situation", turn)
    if situation_answer is not None:
        return situation_answer

    # A shopper, not a questioner. "what life products" and "looking for a CI
    # plan" ask what exists; retrieval finds the best single page and answers
    # from its prose, which is how "what life products" came back as Products
    # Liability. The bundle already knows every product and its line of
    # business — this reports that rather than ranking it.
    # Who underwrites this. One entity page, one underwriter across every
    # product, so it is a fact to state rather than a page to rank for — and
    # ranking for it is how a fire-peril clause answered "who is the insurer".
    if classify(question) is Intent.entity:
        handled = dispatch("entity", turn)
        if handled is not None:
            return handled

    if ask.intent is Intent.browse:
        handled = dispatch("browse", turn)
        if handled is not None:
            return handled

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

    turn.question, turn.ask, turn.understanding = question, ask, understanding
    turn.carried_from = resolution.carried_from

    # Before anything is retrieved, and before any product can be named. Asked
    # about chest pain and a numb arm, this answered "I cannot provide medical
    # advice regarding your symptoms, but our Cancer Insurance..." — the
    # disclaimer turned into a hinge for a pitch. Nothing downstream would stop
    # that, because the pitch was accurate and properly cited.
    if medical_emergency(question):
        return dispatch_required("emergency", turn)

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
        return dispatch_required("off_topic", turn)

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
        return dispatch_required("account_state", turn)

    # A price question with no plan in hand is not a which-plan question.
    # "Get me a quote" and "how much does insurance cost for a family of
    # four?" were asked to choose between Home and Cyber; the price of any of
    # them is not in the corpus, and the quote steps are the same for all.
    if ask.intent is Intent.price and not ask.resolved and decision.layer1 is Layer1.product:
        return dispatch_required("price", turn)

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
        handled = dispatch("browse_open", turn)
        if handled is not None:
            return handled

    # Unsure which product: ask. Three cases, one behaviour. `ambiguous` — the
    # customer named two, or the model could not choose. `guessed` — the
    # customer named a category and the code had picked its flagship; that
    # is a reading, not the customer's word. `none` on a handler whose answer
    # depends on the product — cover, exclusions, a limit, how to claim.
    if decision.clarify and not seeking_advice:
        return dispatch_required("clarify", turn)

    turn.seeking_advice = seeking_advice
    handler_kind = decision.layer3.value if decision.layer3.value != "n/a" else "general"
    return dispatch_required(f"knowledge.{handler_kind}", turn)
