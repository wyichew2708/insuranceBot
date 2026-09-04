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


def test_by_section_keeps_the_heading_the_index_returned() -> None:
    """`by_page` pools section hits to page scores to decide which pages to
    read. `by_section` is the same hits deciding which section of them
    answers — the level the index is actually built at, and the level the
    module's opening note says was still unpatched."""
    hits = VectorHits(
        hits=[
            VectorHit(page_id="product/general/travel", heading="Baggage", similarity=0.7),
            VectorHit(page_id="product/general/travel", heading="Delay", similarity=0.9),
        ]
    )
    assert hits.by_section == {
        ("product/general/travel", "Baggage"): 0.7,
        ("product/general/travel", "Delay"): 0.9,
    }
    # Pooling still takes the best of them, as it did.
    assert hits.by_page == {"product/general/travel": 0.9}


def test_a_section_the_words_missed_can_lead_on_similarity(bundle: Bundle) -> None:
    from api.compose import select_sections

    pages = [p for p in [bundle.get("product/general/travel/benefits")] if p is not None]
    assert pages
    question = "the airline took my suitcase somewhere else"
    without = select_sections(pages, question)
    assert without
    # Lift the section the words placed last, and it gains.
    target = without[-1].heading
    with_dense = select_sections(pages, question, dense={(pages[0].id, target): 0.95}, dense_floor=0.55)
    lifted = {s.heading: s.score for s in with_dense}
    before = {s.heading: s.score for s in without}
    assert lifted[target] > before[target]


def test_a_section_hit_below_the_floor_adds_nothing(bundle: Bundle) -> None:
    from api.compose import select_sections

    page = bundle.get("product/general/travel/benefits")
    assert page is not None
    question = "what is the baggage limit"
    plain = {s.heading: s.score for s in select_sections([page], question)}
    weak = {
        s.heading: s.score
        for s in select_sections([page], question, dense={(page.id, h): 0.3 for h in plain}, dense_floor=0.55)
    }
    assert plain == weak


def test_similarity_cannot_compose_from_a_section_with_no_source_ref(bundle: Bundle) -> None:
    """The rule that keeps every claim citable. A section with no `[src:]` is a
    pointer, not evidence — composing from it makes a factual answer with no
    claims and reference-integrity refuses the turn. A vector hit does not buy
    an exemption."""
    from api.compose import select_sections, split_sections

    # "How to buy" on the home product: a channel table, no `[src:]` markers,
    # and no claim to be had from it. (Not the `index` page — the composer
    # drops navigation pages whole, which would pass this for the wrong
    # reason.)
    page = bundle.get("product/general/home")
    assert page is not None
    pointers = [h for h, body in split_sections(page) if "[src:" not in body]
    assert pointers, "the fixture no longer exercises this"
    picked = {
        s.heading
        for s in select_sections(
            [page], "anything", dense={(page.id, h): 1.0 for h in pointers}, dense_floor=0.55
        )
    }
    assert not (picked & set(pointers))
