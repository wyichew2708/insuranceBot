"""Every offered question is exercised against the corpus that offered it."""

import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest
from api.memory import SessionMemory
from api.navigation import greeting_map, starter_questions
from api.pipeline import answer_question
from api.settings import Settings
from api.suggest import _available, suggest_next
from harness import Channel, Session
from harness.ask import read_ask
from harness.contracts import NavigationNode

from okf import DESTINATIONS, Bundle, Status

TODAY = dt.date(2026, 9, 4)


@pytest.fixture(scope="module")
def real() -> Bundle:
    return Bundle.load(Path("okf-real"))


def walk(nodes: list[NavigationNode]) -> list[NavigationNode]:
    return [node for n in nodes for node in [n, *walk(n.children)]]


def test_every_map_question_is_delivered(real: Bundle) -> None:
    session = Session(session_id="map-test", channel=Channel.direct, today=TODAY)
    nodes = walk(greeting_map(real, session))
    questions = {n.question for n in nodes if n.question}
    assert len(questions) > 150
    settings = Settings(bundle_path=real.root, memory="off")
    failures = []
    for question in sorted(questions):
        envelope, _ = answer_question(real, question, session, settings)
        if not envelope.delivered or envelope.answer.clarifying:
            failures.append(question)
    assert not failures, failures
    trusted = {d.url for d in DESTINATIONS.values()}
    trusted.update(
        url for p in real.pages.values() for binding in p.frontmatter.channels for url in binding.landings
    )
    assert all(n.url in trusted for n in nodes if n.url)


def test_map_has_six_branches_and_only_current_approved_pages(real: Bundle) -> None:
    session = Session(session_id="map-window", channel=Channel.direct, today=TODAY)
    assert len(greeting_map(real, session)) == 6
    product = real.get("product/general/home-insurance")
    assert product is not None
    draft = product.model_copy(
        update={"frontmatter": product.frontmatter.model_copy(update={"status": Status.draft})}
    )
    modified = replace(real, pages={**real.pages, product.id: draft})
    assert not _available(modified, draft, today=TODAY)
    assert not any("Tiq Home Insurance" in (n.question or "") for n in walk(greeting_map(modified, session)))
    future = session.model_copy(update={"today": dt.date(2030, 1, 1)})
    assert not any(n.question for n in walk(greeting_map(real, future)))
    assert starter_questions(real, future.today) == []


def test_suggestions_skip_answered_topics_and_promotions_use_registry(real: Bundle) -> None:
    page = real.get("product/general/home-insurance")
    assert page is not None
    ask = read_ask(real, "What does Tiq Home Insurance cover?")
    chips = suggest_next(real, ask, page, today=TODAY, answered=frozenset({"exclusion", "limit"}))
    assert not any("not cover" in q or "limits" in q for q in chips)
    assert not any("promotion" in q for q in chips)


def test_typed_memory_outlives_raw_recall_and_handoff_has_context(real: Bundle, tmp_path: Path) -> None:
    settings = Settings(bundle_path=real.root, memory="on", state_dir=tmp_path)
    session = Session(session_id="typed-memory", channel=Channel.direct, today=TODAY)
    answer_question(real, "What does Tiq Home Insurance not cover?", session, settings)
    for _ in range(7):
        answer_question(real, "thanks", session, settings)
    recalled = SessionMemory(tmp_path).recall(session.session_id)
    assert recalled.state.product == "home-insurance"
    assert all(q == "thanks" for q in recalled.questions)
    envelope, trace = answer_question(real, "How do I make a claim?", session, settings)
    assert trace.route.get("product") == "home-insurance"
    assert not any("not cover" in s for s in envelope.answer.suggestions)
    handoff, _ = answer_question(real, "How much does it cost?", session, settings)
    assert handoff.answer.handoff
    assert handoff.handover_summary
    assert handoff.answer.suggestions
    assert all("Tiq Home Insurance" in s for s in handoff.answer.suggestions)
    # Explicit empty client history is a fresh reading, not the saved product.
    _, reset_trace = answer_question(real, "How do I make a claim?", session, settings, history=[])
    assert reset_trace.route.get("product") != "home-insurance"


def test_greeting_envelope_serializes_navigation(real: Bundle) -> None:
    envelope, _ = answer_question(
        real,
        "hi",
        Session(session_id="greet", today=TODAY),
        Settings(bundle_path=real.root),
    )
    assert len(envelope.map) == 6
    assert len(envelope.model_dump(mode="json")["map"]) == 6
