"""The graph layer: typed edges stay typed, containment is real, walks repeat."""

from __future__ import annotations

from pathlib import Path

from okf.graph import DEFAULT_ORDER, LINKED, TYPED

from okf import Bundle, EdgeKind, Manifest, PageGraph, graph_for, parse_page, plan_for


def test_the_graph_is_built_once_and_cached_on_the_bundle(bundle: Bundle) -> None:
    assert graph_for(bundle) is graph_for(bundle)


def test_typed_links_keep_their_kind(bundle: Bundle) -> None:
    graph = graph_for(bundle)
    edges = {e.dst: e.kind for e in graph.out_edges("product/general/travel")}
    assert edges["product/general/travel/exclusions"] is EdgeKind.exclusions
    assert edges["product/general/travel/benefits"] is EdgeKind.benefits
    assert edges["concept/travel-delay"] is EdgeKind.concept


def test_edges_run_both_ways(bundle: Bundle) -> None:
    """The half `Bundle.neighbours` never had. `concept/travel-delay` names no
    product; the products name it."""
    graph = graph_for(bundle)
    assert graph.out_edges("concept/travel-delay") == []
    sources = {e.src for e in graph.in_edges("concept/travel-delay")}
    assert "product/general/travel" in sources


def test_containment_is_derived_from_the_id_path(bundle: Bundle) -> None:
    graph = graph_for(bundle)
    assert graph.owner_of(bundle, "product/general/travel/benefits") == "product/general/travel"
    # A root has no owner, and a concept is not a product's child.
    assert graph.owner_of(bundle, "product/general/travel") is None
    assert graph.owner_of(bundle, "concept/excess") is None
    assert "product/general/travel/exclusions" in graph.children_of("product/general/travel")


def test_a_child_a_typed_link_also_names_is_still_a_child(bundle: Bundle) -> None:
    """The edge set keeps one edge per pair and keeps the *stronger* kind, so
    `product/general/travel/benefits` is a `benefits` edge and not a `child`
    one. Containment is not stored there, and this is why: reading parentage
    off the edges answered "no parent" for exactly the pages with the
    strongest one."""
    graph = graph_for(bundle)
    kinds = {e.dst: e.kind for e in graph.out_edges("product/general/travel")}
    assert kinds["product/general/travel/benefits"] is EdgeKind.benefits
    assert "product/general/travel/benefits" in graph.children_of("product/general/travel")


def _synthetic() -> Bundle:
    """A product with one typed child and one the frontmatter never mentions.

    The real corpus has 150 of the second kind — `/cover`, `/definitions`,
    `/eligibility` pages the compiler writes and links to from nothing — and
    the seed bundle happens to have none, so the case is built here rather
    than left untested on the bundle that does not exercise it.
    """
    pages = {}
    for page_id, links in (
        ("product/general/pet", {"exclusions": "product/general/pet/exclusions"}),
        ("product/general/pet/exclusions", {}),
        ("product/general/pet/cover", {}),
    ):
        pages[page_id] = parse_page(
            "---\n"
            f"id: {page_id}\n"
            f"title: {page_id}\n"
            "type: product\n"
            "status: approved\n"
            + ("links:\n" + "".join(f"  {k}: {v}\n" for k, v in links.items()) if links else "")
            + "---\n\nbody\n"
        )
    return Bundle(root=Path("synthetic"), manifest=Manifest(), pages=pages)


def test_a_child_nothing_links_to_is_still_reachable() -> None:
    """The recall the `child` edge buys. `/cover` is in no `links:` block
    anywhere, so before containment was an edge the only way to it was for its
    own words to outscore everything else."""
    graph = graph_for(_synthetic())
    reached = {hop.page_id: hop.kind for hop in graph.walk("product/general/pet")}
    assert reached["product/general/pet/exclusions"] is EdgeKind.exclusions
    assert reached["product/general/pet/cover"] is EdgeKind.child


def test_containment_is_not_a_link() -> None:
    """`LINKED` is what an author wrote. A backlink list or a delete guard
    built over containment as well would report every child page as linked-to
    and refuse to delete any of them — which is what `store.delete` and the
    studio's backlink panel now read."""
    assert EdgeKind.child not in LINKED
    graph = graph_for(_synthetic())
    assert [e.src for e in graph.in_edges("product/general/pet/exclusions", LINKED)] == [
        "product/general/pet"
    ]
    assert graph.in_edges("product/general/pet/cover", LINKED) == []
    # ...and containment is still there to be asked about directly.
    assert "product/general/pet/cover" in graph.children_of("product/general/pet")


def test_the_walk_is_deterministic_and_bounded(bundle: Bundle) -> None:
    graph = graph_for(bundle)
    first = graph.walk("product/general/travel", max_pages=3)
    second = graph.walk("product/general/travel", max_pages=3)
    assert first == second
    assert len(first) <= 3
    assert all(hop.page_id != "product/general/travel" for hop in first)


def test_the_walk_follows_the_order_it_is_given(bundle: Bundle) -> None:
    graph = graph_for(bundle)
    exclusions_first = graph.walk("product/general/travel", (EdgeKind.exclusions, EdgeKind.benefits), 2)
    benefits_first = graph.walk("product/general/travel", (EdgeKind.benefits, EdgeKind.exclusions), 2)
    assert exclusions_first[0].kind is EdgeKind.exclusions
    assert benefits_first[0].kind is EdgeKind.benefits


def test_a_plan_is_a_permutation_and_never_a_filter() -> None:
    """An intent decides what is read first, never what is read at all — so no
    phrasing can talk the harness out of the exclusions page (§F.2)."""
    for question in (
        "how do i make a claim",
        "what is not covered",
        "how much is the baggage limit",
        "tell me about travel insurance",
        "",
    ):
        plan = plan_for(question)
        assert sorted(plan, key=DEFAULT_ORDER.index) == list(DEFAULT_ORDER)
        assert EdgeKind.exclusions in plan


def test_a_claims_question_puts_the_claims_edge_first() -> None:
    assert plan_for("how do i claim for a delayed bag")[0] is EdgeKind.claims
    assert plan_for("what is not covered")[0] is EdgeKind.exclusions
    assert plan_for("how much is the baggage limit")[0] is EdgeKind.benefits


def test_every_typed_kind_is_one_the_compiler_writes(bundle: Bundle) -> None:
    assert {EdgeKind.exclusions, EdgeKind.benefits, EdgeKind.claims} == TYPED


def test_a_broken_edge_is_not_an_edge(bundle: Bundle) -> None:
    """`Bundle.broken_links` reports refs that resolve to nothing; the graph
    simply does not carry them, so a walk can never load a page that is not
    there."""
    graph = graph_for(bundle)
    for page_id in bundle.pages:
        for edge in graph.out_edges(page_id):
            assert edge.dst in bundle.pages


def test_the_real_corpus_builds(bundle: Bundle) -> None:
    graph = PageGraph.build(bundle)
    assert graph.edge_count > 0
    described = graph.describe("product/general/travel")
    assert described["out"] and all(":" in entry for entry in described["out"])
