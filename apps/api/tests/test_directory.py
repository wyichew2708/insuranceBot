"""The customer who is shopping rather than asking.

Every case here is from a real reported conversation. "what life products"
returned the Products Liability page, having matched on the word "products";
"looking for ci product" returned an investment-linked plan, because the
tokeniser drops two-letter words and "ci" never reached scoring at all.
"""

from __future__ import annotations

from api.directory import answer, line_asked_for, lookup
from api.pipeline import answer_question
from api.settings import Settings
from harness import Verdict
from harness.intent import Intent, classify

from conftest import make_session
from okf import Bundle


def ask(bundle: Bundle, settings: Settings, question: str):  # type: ignore[no-untyped-def]
    return answer_question(bundle, question, make_session(), settings)


# --- the intent ---


def test_shopping_is_not_asking() -> None:
    for q in (
        "what life products",
        "looking for ci product",
        "do you have pet insurance",
        "show me your travel plans",
        "what plans do you have",
    ):
        assert classify(q) is Intent.browse, q


def test_a_question_about_a_product_is_not_shopping() -> None:
    """The gap between "what" and the noun is what keeps these out — four
    words of question sit in "what is not covered by fire insurance"."""
    for q in (
        "What is not covered by fire insurance?",
        "what does travel insurance cover",
        "how much does travel cost",
        "is my laptop covered",
    ):
        assert classify(q) is not Intent.browse, q


# --- the lookup ---


def test_the_line_of_business_comes_from_the_customer_s_words() -> None:
    assert line_asked_for("what life products") == "protection"
    assert line_asked_for("looking for critical illness cover") == "protection"
    assert line_asked_for("any car insurance") == "motor"
    assert line_asked_for("what do you have") is None


def test_critical_illness_beats_illness() -> None:
    """Longest term wins, or "critical illness" lands on health-medical."""
    assert line_asked_for("critical illness plans") == "protection"


def test_a_directory_answer_cites_every_product_it_names(bundle: Bundle) -> None:
    listing = answer(bundle, "what travel products do you have")
    assert listing is not None
    assert listing.claims
    for claim in listing.claims:
        assert bundle.get(claim.source_id) is not None
        assert claim.text in listing.answer


def test_child_pages_are_not_listed_as_products(bundle: Bundle) -> None:
    """An exclusions page is part of a product, not one of its own."""
    found = lookup(bundle, "what travel products do you have")
    assert all(p.id.count("/") == 2 for p in found.products), [p.id for p in found.products]


def test_a_line_this_insurer_does_not_write_returns_nothing(bundle: Bundle) -> None:
    """None is a real outcome — the caller must refuse rather than reach for
    the nearest thing."""
    assert answer(bundle, "looking for marine cargo products") is None


def test_the_listing_carries_no_count(bundle: Bundle) -> None:
    """A count is a true fact the bundle computed and an unbound figure to the
    numeric-binding gate. There is no carve-out narrow enough to admit "19
    products" that would not also admit a limit."""
    listing = answer(bundle, "what travel products do you have")
    assert listing is not None
    assert not any(ch.isdigit() for ch in listing.answer.replace("2026", ""))


# --- end to end ---


def test_shopping_is_answered_with_a_directory(bundle: Bundle, settings: Settings) -> None:
    env, trace = ask(bundle, settings, "what travel products do you have")
    assert env.delivered
    assert "directory" in [s.name for s in trace.stages]
    assert not [g for g in env.gates if g.verdict is Verdict.fail]


def test_a_directory_answer_is_gated_like_any_other(bundle: Bundle, settings: Settings) -> None:
    """It carries claims, so reference-integrity and groundedness have real
    work to do — unlike a greeting, which asserts nothing."""
    env, _ = ask(bundle, settings, "what travel products do you have")
    verdicts = {g.gate: g.verdict for g in env.gates}
    assert verdicts["reference-integrity"] is Verdict.pass_
    assert verdicts["groundedness"] is Verdict.pass_


# --- requirements drive retrieval (DESIGN-answering.md §4.2) ---


def test_the_requirement_fetches_the_page_that_holds_the_answer(bundle: Bundle, settings: Settings) -> None:
    """ "how to buy" was answered from three FAQ entries that repeat the word
    "buy", while the product's own "How to buy" section sat unread on a page
    that was already loaded. `REQUIREMENTS` knew which page settles an
    application question and was only ever consulted to reject."""
    from api.compose import evidence_pages
    from harness.intent import Intent

    product = bundle.get("product/general/travel")
    assert product is not None
    named = evidence_pages(Intent.application, product, {"product/general/travel"})
    assert "product/general/travel" in named


def test_an_intent_the_bundle_cannot_serve_steers_nothing(bundle: Bundle) -> None:
    """A suffix the bundle does not carry must not steer. The seed bundle has
    no `/cover` child, and boosting a page that does not exist while docking
    every page that does starved the benefits page on any coverage question."""
    from api.compose import evidence_pages
    from harness.intent import Intent

    product = bundle.get("product/general/travel")
    assert product is not None
    assert evidence_pages(Intent.definition, product, {"product/general/travel"}) == frozenset()


def test_a_requirement_that_demands_nothing_cannot_refuse() -> None:
    """`holds_answer` steers retrieval and asks nothing of the result. An
    entry carrying only that must not make the answerability gate refuse —
    adding one failed about a hundred cases with "asked for coverage; the
    answer shows none of it"."""
    from harness.intent import REQUIREMENTS, Intent, Requirement

    assert not Requirement(holds_answer=("/x",)).checkable
    assert REQUIREMENTS[Intent.exclusion].checkable
