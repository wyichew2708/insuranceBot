"""Resolve purchase timing before keyword routing confuses trip and policy cancellation."""

from __future__ import annotations

import re

from harness import AnswerEnvelope, Claim, GroundedAnswer, Trace
from harness.contracts import Link
from harness.trace import LoadedPage

from api.handlers.contracts import Evidence, Turn
from api.handlers.shared import CONTACT_LINK, _fail_closed, _finish, _refusal
from api.suggest import product_label, servable
from okf import landing_for


def situation(question: str) -> str | None:
    text = question.lower()
    purchase = r"(?:bought|purchased|buy|purchase|took out|taken out)"
    cancelled = r"(?:flight|trip)[^.?!]{0,35}cancel(?:led|ed|lation)"
    if re.search(purchase + r"[^.?!]{0,35}\bafter\b[^.?!]{0,35}" + cancelled, text) or re.search(
        cancelled + r"[^.?!]{0,25}\b(?:then|before)\b[^.?!]{0,25}" + purchase, text
    ):
        return "known_event"
    if (
        re.search(r"\b(?:buy|purchase|take out)\b", text)
        and re.search(r"\b(?:travel|trip|insurance)\b", text)
        and re.search(
            r"\b(?:already|currently|now|i am|i'm|im|we are)\b[^.?!]{0,25}\b(?:overseas?|abroad)\b", text
        )
    ):
        return "already_abroad"
    return None


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace] | None:
    kind = situation(turn.question)
    if kind is None:
        return None
    turn.require(Evidence.wiki)
    # A general travel question gets an explicitly scoped example, never a rule
    # asserted for every insurer. A named product must supply its own clause.
    product = turn.bundle.get(turn.ask.product_page or "product/general/travel-insurance")
    if product is None or "travel" not in product.id or not servable(product, turn.session.today):
        return None
    page = turn.bundle.get(product.id + "/conditions")
    if page is None or not servable(page, turn.session.today):
        return None
    pattern = (
        r"At the time of effecting this insurance[^\n]+not be aware[^\n]+otherwise no claim will be payable"
        if kind == "known_event"
        else r"You must purchase this insurance before departing Singapore\.[^\n]+"
    )
    match = re.search(pattern, page.body, re.I)
    if match is None:
        return None
    clause = re.sub(r"\s*\[src:[^]]+\]", "", match.group()).strip().rstrip(".") + "."
    lead = (
        "The key issue is whether you knew about the cancellation when you bought the insurance."
        if kind == "known_event"
        else "The key issue is that you are already overseas."
    )
    name = product_label(product)
    answer = GroundedAnswer(
        answer=f"{lead}\n\nFor **{name}**, the published condition says:\n{clause}\n\n"
        "This is the published rule, not a decision on your individual claim or policy. "
        "Our team can check your purchase and travel circumstances.",
        claims=[Claim(text=clause, source_id=page.id, locator=page.id)],
        handoff=True,
        guidance=True,
        confidence=0.9,
        destinations=[CONTACT_LINK],
    )
    url = landing_for(product, turn.session.channel)
    if url:
        answer.destinations.append(Link(label=name, url=url, desk="product"))
    turn.budget.charge_page()
    turn.budget.check_clock()
    turn.trace.loaded.append(LoadedPage(page_id=page.id, title=page.frontmatter.title, via="situation"))
    outgoing = turn.guard.screen_output(turn.question, clause, answer.answer, [])
    if outgoing.blocked or _fail_closed(outgoing, turn.settings):
        return _refusal(turn.trace, outgoing, "guardrail-output", "travel situation failed screening")
    envelope, trace = _finish(
        turn.trace, answer, turn.bundle, turn.session, turn.question, turn.raw_root, [page.id], ask=turn.ask
    )
    envelope.gates.append(outgoing.as_gate("guardrail-output"))
    trace.gates = envelope.gates
    if not envelope.delivered:
        trace.blocked_draft = answer.answer
        envelope.answer = GroundedAnswer(
            answer="I cannot verify this against the approved documents. Please contact our team for help.",
            handoff=True,
            destinations=[CONTACT_LINK],
        )
        return envelope, trace
    return envelope, trace
