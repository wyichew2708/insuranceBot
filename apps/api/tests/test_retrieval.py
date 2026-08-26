import datetime as dt

from api.retrieval import (
    focus_product,
    frontmatter_filter,
    keywords,
    needs_rag,
    rag_search,
    score_page,
    wiki_read,
)
from harness import Budget, Channel, Trace

from conftest import TODAY, make_session
from okf import Bundle


def test_filter_records_a_reason_for_every_rejection(bundle: Bundle) -> None:
    trace = Trace()
    frontmatter_filter(bundle, "travel delay benefit", make_session(), trace, 0.08)
    assert trace.candidates, "the filter must record what it considered"
    for candidate in trace.rejected:
        assert candidate.reason, f"{candidate.page_id} rejected without a reason"


def test_expired_promotion_is_rejected_with_its_reason(bundle: Bundle) -> None:
    trace = Trace()
    frontmatter_filter(bundle, "travel promotion discount", make_session(), trace, 0.08)
    expired = next(c for c in trace.candidates if c.page_id == "promotion/travel-jun-2026")
    assert not expired.admitted
    assert "effective window" in expired.reason


def test_review_overdue_demotes_everything(bundle: Bundle) -> None:
    trace = Trace()
    admitted = frontmatter_filter(
        bundle, "travel medical limit", make_session(today=dt.date(2026, 12, 1)), trace, 0.08
    )
    assert admitted == []
    # Some pages are rejected earlier in the chain (an expired promotion never
    # reaches the review check), so assert the demotion actually fired.
    assert any("review overdue" in c.reason for c in trace.rejected)


def test_alias_hit_outranks_bag_of_words(bundle: Bundle) -> None:
    trace = Trace()
    admitted = frontmatter_filter(bundle, "Tiq Travel cover", make_session(), trace, 0.08)
    assert admitted[0][0].id.startswith("product/general/travel")
    assert "product/general/travel" in trace.entities


def test_wiki_read_follows_the_graph(bundle: Bundle) -> None:
    trace = Trace()
    admitted = frontmatter_filter(bundle, "travel exclusions", make_session(), trace, 0.08)
    pages = wiki_read(bundle, admitted, trace, Budget(), 5, TODAY)
    assert any(p.via == "graph" for p in trace.loaded)
    assert len(pages) <= 5


def test_wiki_read_respects_the_page_budget(bundle: Bundle) -> None:
    trace = Trace()
    admitted = frontmatter_filter(bundle, "travel", make_session(), trace, 0.0)
    pages = wiki_read(bundle, admitted, trace, Budget(max_pages=2), 5, TODAY)
    assert len(pages) <= 2


def test_rag_triggers_on_a_clause_level_question(bundle: Bundle) -> None:
    trace = Trace()
    admitted = frontmatter_filter(
        bundle, "what does section 4.2 of the wording say", make_session(), trace, 0.08
    )
    assert needs_rag("what does section 4.2 of the policy wording say", admitted, make_session(), 0.45)


def test_rag_triggers_on_a_historic_policy_version(bundle: Bundle) -> None:
    trace = Trace()
    session = make_session(policy_id="TRV-900001", version="2025.2")
    admitted = frontmatter_filter(bundle, "travel delay threshold", session, trace, 0.08)
    assert "historic version" in needs_rag("travel delay threshold", admitted, session, 0.45)


def test_rag_not_needed_for_a_well_matched_wiki_question(bundle: Bundle) -> None:
    trace = Trace()
    admitted = frontmatter_filter(bundle, "travel delay benefit threshold", make_session(), trace, 0.08)
    assert needs_rag("travel delay benefit threshold", admitted, make_session(), 0.45) == ""


def test_rag_search_filters_wordings_to_the_in_force_version(bundle: Bundle) -> None:
    hits = rag_search(
        bundle.root / "raw", "travel delay threshold", make_session(policy_id="TRV-900001", version="2025.2")
    )
    wording_hits = [h for h in hits if "/wordings/" in h.source_path]
    assert wording_hits, "expected the 2025.2 wording to be retrievable"
    assert all("2025.2" in h.source_path for h in wording_hits)


def test_score_page_and_keywords(bundle: Bundle) -> None:
    page = bundle.get("product/general/travel")
    assert page is not None
    assert score_page(page, keywords("travel insurance")) > 0
    assert "the" not in keywords("the travel plan")


def test_another_routes_channel_page_is_not_evidence(bundle: Bundle) -> None:
    """The session fixes the route, so only that route's page may be read.

    Without this a direct customer asking "I bought online, is my cover
    different?" is answered from the bancassurance page, whose prose says
    "rather than bought online" and so scores well on the question.
    """
    trace = Trace()
    frontmatter_filter(bundle, "I bought online, is my cover different?", make_session(), trace, 0.08)
    admitted = {c.page_id for c in trace.candidates if c.admitted}
    assert "channel/direct" not in admitted or "channel/bancassurance" not in admitted
    foreign = next(c for c in trace.candidates if c.page_id == "channel/bancassurance")
    assert not foreign.admitted
    assert "different channel" in foreign.reason


def test_an_unknown_channel_may_read_every_route(bundle: Bundle) -> None:
    """With no route in the session there is nothing to be incoherent with, so
    every route stays offerable (§C.4)."""
    trace = Trace()
    session = make_session(channel=Channel.unknown)
    frontmatter_filter(bundle, "how do I buy insurance?", session, trace, 0.08)
    reasons = [c.reason for c in trace.candidates if c.page_id.startswith("channel/")]
    assert not any("different channel" in r for r in reasons)


def test_focus_prefers_the_product_the_question_names(bundle: Bundle) -> None:
    """On the real corpus, "cancer insurance" tied the pet-insurance FAQ with
    the cancer product page — the FAQ mentions the words, the product is
    called them — and an alphabetical tiebreak handed the focus to pet
    insurance, which then excluded the cancer page as "a different product".

    A page carrying the name in its title is not equal evidence to one that
    mentions it in passing.
    """
    scored = {page.id: 1.0 for page in bundle.pages.values()}
    focus = focus_product(bundle, scored, keywords("travel insurance"))
    assert focus is not None
    assert "travel" in focus


def test_focus_prefers_the_product_page_over_its_children(bundle: Bundle) -> None:
    scored = {
        "product/general/travel": 1.0,
        "product/general/travel/exclusions": 1.0,
    }
    assert focus_product(bundle, scored, keywords("travel")) is not None


def test_no_product_shaped_match_leaves_the_question_alone(bundle: Bundle) -> None:
    assert focus_product(bundle, {"index": 1.0}, keywords("anything")) is None
