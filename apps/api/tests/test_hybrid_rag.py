"""The RAG fallback is hybrid, and the dense half is admitted by the same rules.

The fallback fires on the questions the wiki cannot answer — a clause the
compiler did not lift, a customer on a historic version, a phrasing that shares
no vocabulary with the contract. Those are exactly the questions a word-overlap
search is worst at, which is the case for searching `raw/` by similarity too.

What must not follow is a second door into the answer. Every filter the lexical
pass applies — the marketing screen, the in-force version, the word the wiki
had never seen — is applied to the dense list before either can be cited.
"""

from __future__ import annotations

import datetime as dt

from api.retrieval import RRF_K, rag_search
from api.vectors import RawHit
from harness import AuthLevel, Channel, PolicyContext, Session

from okf import Bundle, term_idf

BUNDLE = "okf"
TODAY = dt.date(2026, 8, 19)


def _session(version: str | None = None) -> Session:
    policy = (
        PolicyContext(policy_id="TRV-1", product_id="product/general/travel", version=version, tier="tier-2")
        if version
        else None
    )
    return Session(
        session_id="t",
        channel=Channel("channel/direct"),
        auth_level=AuthLevel("L2") if version else AuthLevel("L0"),
        policy=policy,
        today=TODAY,
    )


def test_no_index_leaves_the_lexical_fallback_exactly_as_it_was(bundle: Bundle) -> None:
    """A deployment without pgvector must not be able to tell this code was
    touched — same hits, same scores, same order."""
    raw = bundle.root / "raw"
    args = (raw, "what is the cancellation excess", _session())
    assert rag_search(*args, idf=term_idf(bundle)) == rag_search(*args, idf=term_idf(bundle), dense=[])
    assert all(h.found_by == "lexical" for h in rag_search(*args, idf=term_idf(bundle)))


def test_a_dense_hit_the_words_missed_is_returned(bundle: Bundle) -> None:
    dense = [
        RawHit(
            source_path="raw/wordings/travel-2026.1.md",
            heading="Baggage",
            similarity=0.88,
            content="We pay for baggage that the carrier loses in transit. [src:x]",
        )
    ]
    hits = rag_search(
        bundle.root / "raw",
        "the airline lost my suitcase",
        _session(),
        idf=term_idf(bundle),
        dense=dense,
    )
    found = {h.locator: h.found_by for h in hits}
    assert found.get("Baggage") in {"dense", "both"}


def test_a_dense_hit_below_the_floor_is_not_a_hit(bundle: Bundle) -> None:
    dense = [
        RawHit(
            source_path="raw/wordings/travel-2026.1.md",
            heading="Baggage",
            similarity=0.2,
            content="We pay for baggage the carrier loses. [src:x]",
        )
    ]
    hits = rag_search(bundle.root / "raw", "anything at all", _session(), idf=term_idf(bundle), dense=dense)
    assert "Baggage" not in {h.locator for h in hits}


def test_similarity_cannot_get_a_blog_post_past_the_marketing_screen(bundle: Bundle) -> None:
    """586 of the crawled pages are blog posts, and this fallback is the one
    path by which a blog sentence could reach a customer as the answer.
    `may_support` refuses them lexically; it refuses them dense too."""
    dense = [
        RawHit(
            source_path="raw/web/tiq-sg/blog-five-tips-for-travel.md",
            heading="Five tips",
            similarity=0.99,
            content="Travel insurance is a great idea! Here are five nostalgic places to visit.",
        )
    ]
    hits = rag_search(
        bundle.root / "raw",
        "what is the baggage limit",
        _session(),
        idf=term_idf(bundle),
        dense=dense,
    )
    assert all("blog" not in h.source_path for h in hits)


def test_similarity_cannot_serve_another_version_of_the_wording(bundle: Bundle) -> None:
    """A historic-version question must retrieve *that* version's wording,
    never a summary of the current one (§E point 2)."""
    dense = [
        RawHit(
            source_path="raw/wordings/travel-2026.1.md",
            heading="Baggage",
            similarity=0.99,
            content="The current wording's baggage clause. [src:x]",
        )
    ]
    hits = rag_search(
        bundle.root / "raw",
        "what does the baggage clause say",
        _session(version="2024.2"),
        idf=term_idf(bundle),
        dense=dense,
    )
    # The rule is about the wordings, which are version-specific documents;
    # the product summaries are filtered by `may_support`, not by version.
    assert all("/wordings/" not in h.source_path for h in hits)


def test_must_include_holds_on_the_dense_side(bundle: Bundle) -> None:
    """The fallback fired because this word matched nothing in the wiki. A raw
    section that does not contain it either is the nearest neighbour, which is
    the failure — however similar it looks."""
    dense = [
        RawHit(
            source_path="raw/wordings/travel-2026.1.md",
            heading="Baggage",
            similarity=0.99,
            content="Nothing here about the thing that was asked. [src:x]",
        )
    ]
    hits = rag_search(
        bundle.root / "raw",
        "what does your kidnap insurance cover",
        _session(),
        idf=term_idf(bundle),
        dense=dense,
        must_include="kidnap",
    )
    assert all(h.locator != "Baggage" for h in hits)


def test_agreement_between_the_two_retrievers_outranks_either_alone() -> None:
    """What reciprocal-rank fusion is for. A section both lists rank second
    beats one that either ranks first, because a BM25-ish ratio and a cosine
    similarity are not on the same scale and their *orders* are what can be
    compared."""
    from api.retrieval import _fuse
    from harness import RagHit

    def hit(name: str) -> RagHit:
        return RagHit(source_path=f"raw/{name}.md", locator=name, score=1.0)

    fused = _fuse([hit("a"), hit("b")], [hit("c"), hit("b")], limit=3)
    assert fused[0].locator == "b"
    assert fused[0].found_by == "both"
    assert fused[0].score == 1.0
    # And the leader reads 1.0 rather than the raw 0.032 the formula produces.
    assert 2 / (RRF_K + 2) < 1.0
