"""Reported customer journeys, through the API as well as the pipeline."""

import json
from pathlib import Path

import pytest
from api import main
from api.handlers.travel_situation import situation
from api.pipeline import answer_question
from api.settings import Settings
from fastapi.testclient import TestClient
from harness import Channel, Session

from okf import Bundle


@pytest.fixture(scope="module")
def real() -> Bundle:
    return Bundle.load(Path("okf-real"))


def test_compare_without_tiers_and_generic_compare(real: Bundle) -> None:
    settings = Settings(bundle_path=real.root)
    e, t = answer_question(
        real,
        "Compare Tiq Travel and Travel Infinite",
        Session(session_id="compare", channel=Channel.direct),
        settings,
    )
    assert e.delivered and not e.answer.clarifying
    assert "Tiq Travel Insurance" in e.answer.answer and "Travel Infinite" in e.answer.answer
    assert "medical" in e.answer.answer.lower()
    assert len(e.answer.destinations) == 2
    e, _ = answer_question(
        real,
        "Compare it with Travel Infinite",
        Session(session_id="compare-followup"),
        settings,
        history=["Tiq Travel"],
    )
    assert e.delivered and not e.answer.clarifying
    assert "Tiq Travel Insurance" in e.answer.answer and "Travel Infinite" in e.answer.answer
    e, t = answer_question(real, "compare products", Session(session_id="choose"), settings)
    assert t.handler == "knowledge.compare" and e.answer.clarifying


def test_introductions_are_catalogue_or_named_overview(real: Bundle) -> None:
    settings = Settings(bundle_path=real.root)
    e, t = answer_question(real, "introduce your travel products", Session(session_id="intro"), settings)
    assert t.handler == "browse" and e.delivered
    assert "Tiq Travel Insurance" in e.answer.answer
    assert "home content" not in e.answer.answer
    e, t = answer_question(real, "introduce Tiq Travel", Session(session_id="intro-plan"), settings)
    assert t.handler == "knowledge.coverage" and e.delivered
    assert e.preview and len(e.preview.split()) <= 100
    assert e.preview in e.answer.answer
    assert e.answer.suggestions


@pytest.mark.parametrize("endpoint", ["/v1/answer", "/v1/answer/stream"])
def test_http_omitted_history_uses_memory(
    real: Bundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    monkeypatch.setitem(
        main._state, "settings", Settings(bundle_path=real.root, memory="on", state_dir=tmp_path)
    )
    monkeypatch.setitem(main._state, "bundle", real)
    monkeypatch.setattr(main, "STREAM_PAUSE_S", 0)
    with TestClient(main.app) as client:
        session = {"session_id": "customer-memory", "today": "2026-09-21"}
        client.post(endpoint, json={"question": "Tiq Travel", "session": session})
        for _ in range(7):
            client.post(endpoint, json={"question": "thanks", "session": session})
        response = client.post(endpoint, json={"question": "cancellation policy", "session": session})
        if endpoint.endswith("stream"):
            frames = response.text.split("\n\n")
            data = next(json.loads(f.split("data: ", 1)[1]) for f in frames if f.startswith("event: done"))
        else:
            data = response.json()
        assert not data["answer"]["clarifying"]
        assert "notice in writing" in data["answer"]["answer"]
        trace = main.traces().get(data["trace_id"])
        assert trace is not None and trace.route["product"] == "travel-insurance"
        fresh = client.post(
            "/v1/answer", json={"question": "cancellation policy", "session": session, "history": []}
        ).json()
        fresh_trace = main.traces().get(fresh["trace_id"])
        assert fresh_trace is not None and fresh_trace.route.get("product") != "travel-insurance"


@pytest.mark.parametrize(
    "question,expected",
    [
        (
            "i bought insurance after flight was cancelled, am i eligible to claim?",
            "otherwise no claim will be payable",
        ),
        ("I am at oversea, can i buy travel insurance?", "before departing Singapore"),
    ],
)
def test_purchase_timing_uses_conditions(real: Bundle, question: str, expected: str) -> None:
    e, t = answer_question(real, question, Session(session_id="timing"), Settings(bundle_path=real.root))
    assert t.handler == "travel_situation" and e.delivered
    assert expected in e.answer.answer
    assert "Tiq Travel Insurance" in e.answer.answer
    assert "Log in to the customer portal" not in e.answer.answer
    assert e.answer.claims and all("conditions" in c.source_id for c in e.answer.claims)


def test_does_not_reverse_purchase_sequence() -> None:
    assert situation("I bought insurance before my flight was cancelled") is None
    assert situation("Can I cancel my travel insurance?") is None
    assert situation("I will go overseas next year, can I buy travel insurance?") is None
