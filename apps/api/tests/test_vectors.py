"""Dense recall is fused into the lexical rank, and never bypasses its filters."""

from __future__ import annotations

import datetime as dt

from api.retrieval import frontmatter_filter
from api.settings import Settings
from api.vectors import VectorHit, VectorHits, searcher_for
from harness import AuthLevel, Channel, Session, Trace

from okf import Bundle


def _session() -> Session:
    return Session(session_id="t", channel=Channel("channel/direct"), auth_level=AuthLevel("L0"))


def test_auto_with_nothing_configured_is_the_lexical_path() -> None:
    assert searcher_for(Settings(pgvector="auto", pgvector_dsn="", embed_base_url="")) is None


def test_off_never_resolves_even_when_configured() -> None:
    s = Settings(pgvector="off", pgvector_dsn="postgresql://x", embed_base_url="http://e")
    assert searcher_for(s) is None


def test_on_resolves_and_fails_closed_by_mode() -> None:
    s = Settings(pgvector="on", pgvector_dsn="postgresql://x", embed_base_url="http://e")
    searcher = searcher_for(s)
    assert searcher is not None and searcher.mode == "on"


def test_a_page_the_words_missed_can_rise_on_similarity(bundle: Bundle) -> None:
    # A question with no lexical purchase on the exclusions page, and a strong
    # vector hit on it. Without fusion it sits below the floor; with it, it is
    # admitted — through the same filter ladder as everything else.
    question = "my things were taken from the flat while we were away"
    trace = Trace(question=question)
    without = frontmatter_filter(bundle, question, _session(), trace, 0.08, None, 0.45)
    hits = VectorHits(
        hits=[VectorHit(page_id="product/general/home/exclusions", heading="x", similarity=0.92)]
    )
    trace2 = Trace(question=question)
    with_vec = frontmatter_filter(bundle, question, _session(), trace2, 0.08, None, 0.45, hits, 0.55)
    scores = {p.id: s for p, s in with_vec}
    assert "product/general/home/exclusions" in scores
    lexical = {p.id: s for p, s in without}.get("product/general/home/exclusions", 0.0)
    assert scores["product/general/home/exclusions"] > lexical


def test_a_hit_below_the_floor_adds_nothing(bundle: Bundle) -> None:
    question = "my things were taken from the flat while we were away"
    hits = VectorHits(
        hits=[VectorHit(page_id="product/general/home/exclusions", heading="x", similarity=0.40)]
    )
    a = {
        p.id: s
        for p, s in frontmatter_filter(
            bundle, question, _session(), Trace(question=question), 0.08, None, 0.45
        )
    }
    b = {
        p.id: s
        for p, s in frontmatter_filter(
            bundle, question, _session(), Trace(question=question), 0.08, None, 0.45, hits, 0.55
        )
    }
    assert a.get("product/general/home/exclusions", 0.0) == b.get("product/general/home/exclusions", 0.0)


def test_similarity_cannot_admit_a_page_the_filter_rejects(bundle: Bundle) -> None:
    # A draft page lifted to similarity 1.0 is still a draft. The ladder runs
    # after fusion, so the lift changes its score and not its fate.
    draft = next((p for p in bundle.pages.values() if p.frontmatter.status.value != "approved"), None)
    if draft is None:
        return
    hits = VectorHits(hits=[VectorHit(page_id=draft.id, heading="x", similarity=1.0)])
    trace = Trace(question="anything")
    admitted = frontmatter_filter(bundle, "anything", _session(), trace, 0.08, None, 0.45, hits, 0.55)
    assert draft.id not in {p.id for p, _ in admitted}
    rejected = {c.page_id: c.reason for c in trace.candidates if not c.admitted}
    assert "status" in rejected.get(draft.id, "")


def test_degraded_hits_carry_the_reason() -> None:
    assert VectorHits(degraded="query: OperationalError").by_page == {}
    assert VectorHits(hits=[VectorHit("p", "h", 0.7), VectorHit("p", "h2", 0.9)]).by_page == {"p": 0.9}


def test_today_is_a_date(bundle: Bundle) -> None:
    assert isinstance(_session().today, dt.date)
