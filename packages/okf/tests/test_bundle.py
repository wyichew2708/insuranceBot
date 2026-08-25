import datetime as dt

from okf import Bundle, PageType, Status, normalise

TODAY = dt.date(2026, 8, 19)


def test_seed_bundle_loads_without_errors(bundle: Bundle) -> None:
    assert bundle.load_errors == []
    assert len(bundle.pages) >= 15
    assert len(bundle.tables) >= 20


def test_every_link_resolves(bundle: Bundle) -> None:
    assert bundle.broken_links() == []


def test_alias_resolution_is_the_entity_resolver(bundle: Bundle) -> None:
    # Customers type any of these for one canonical page (§B.3).
    for phrasing in ["Tiq Travel", "etiqa travel insurance", "my travel plan"]:
        assert "product/general/travel" in bundle.resolve_aliases(phrasing), phrasing


def test_graph_traversal_reaches_exclusions_from_the_product(bundle: Bundle) -> None:
    # This is the deterministic replacement for multi-hop RAG (§E.1).
    reached = bundle.traverse("product/general/travel", max_pages=8)
    assert "product/general/travel/exclusions" in reached
    assert "product/general/travel/benefits" in reached
    assert "concept/pre-existing-condition" in reached


def test_product_key_inherits_from_parent(bundle: Bundle) -> None:
    sub = bundle.get("product/general/travel/benefits")
    assert sub is not None
    # Benefit tables are keyed by "travel", not "benefits".
    assert bundle.product_key(sub) == "travel"


def test_retrievable_requires_approved_and_in_window(bundle: Bundle) -> None:
    page = bundle.get("product/general/travel")
    assert page is not None
    assert bundle.retrievable(page, TODAY)
    # Past its review_due, the page is demoted out of wiki-first retrieval.
    assert not bundle.retrievable(page, dt.date(2026, 12, 1))


def test_expired_promotion_is_not_retrievable(bundle: Bundle) -> None:
    expired = bundle.get("promotion/travel-jun-2026")
    live = bundle.get("promotion/travel-aug-2026")
    assert expired is not None and live is not None
    assert not bundle.retrievable(expired, TODAY)
    assert bundle.retrievable(live, TODAY)


def test_products_declare_channel_bindings_not_forked_pages(bundle: Bundle) -> None:
    travel = bundle.get("product/general/travel")
    assert travel is not None
    refs = {c.ref for c in travel.frontmatter.channels}
    assert refs == {"channel/direct", "channel/agency"}
    # The direct route reaches the same product at either address.
    direct = next(c for c in travel.frontmatter.channels if c.ref == "channel/direct")
    assert {u.split("/")[2] for u in direct.landings} == {"www.etiqa.com.sg", "www.tiq.com.sg"}
    assert travel.frontmatter.status is Status.approved
    assert travel.frontmatter.type is PageType.product


def test_normalise() -> None:
    assert normalise("Tiq  Travel!") == "tiq travel"
