"""The router's three layers, the product scope, and asking when unsure.

Three rules from the product owner: retrieval reads only the product asked
about; the router has layers; an unsure reading asks. These hold each one
where it can be held on the seed bundle, and the pieces that need the real
corpus skip cleanly when it is not present.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from api.pipeline import answer_question
from api.router import NEED_RE, Decision, Layer1, Layer2, Layer3, route
from api.settings import Settings
from harness.ask import Ask, read_ask
from harness.intent import Intent

from conftest import make_session
from okf import SHARED, UNKNOWN, Bundle, Scope, raw_product_index

REAL = Path(__file__).resolve().parents[3] / "okf-real"


def ask(bundle: Bundle, settings: Settings, question: str, **kw: object):  # type: ignore[no-untyped-def]
    return answer_question(bundle, question, make_session(**kw), settings)  # type: ignore[arg-type]


# --- the scope ---------------------------------------------------------------


def test_a_scope_admits_its_own_product_and_the_shared_pages(bundle: Bundle) -> None:
    travel = bundle.get("product/general/travel")
    home = bundle.get("product/general/home")
    assert travel is not None and home is not None
    scope = Scope.for_product(bundle.product_key(travel))
    assert scope.allows_page(bundle, travel)
    assert not scope.allows_page(bundle, home)
    # Concepts, channels, journeys belong to no product and every product needs them.
    for page in bundle.pages.values():
        if not page.id.startswith("product/"):
            assert scope.allows_page(bundle, page), page.id


def test_an_open_scope_admits_everything(bundle: Bundle) -> None:
    scope = Scope.open()
    assert not scope.scoped
    assert all(scope.allows_page(bundle, p) for p in bundle.pages.values())
    assert scope.allows_raw(bundle, "raw/anything.md")


def test_a_bundle_that_tags_nothing_does_not_drop_its_raw_sources(bundle: Bundle) -> None:
    """A filter that cannot tell products apart must admit, not drop."""
    index = raw_product_index(bundle)
    if any(tag not in (UNKNOWN,) for tag in index.values()):
        pytest.skip("this bundle tags its raw sources; the open-fallback case does not apply")
    assert Scope.for_product("travel").allows_raw(bundle, "raw/wordings/travel.md")


@pytest.mark.skipif(not (REAL / "catalogue.yaml").exists(), reason="real bundle not in this checkout")
def test_the_real_corpus_tags_its_documents_by_product() -> None:
    real = Bundle.load(REAL)
    index = raw_product_index(real)
    tagged = {rel: tag for rel, tag in index.items() if tag not in (SHARED, UNKNOWN)}
    assert len(tagged) > 200, "expected the wordings, summaries and brochures to be tagged"
    # The one that started this: a commercial vehicle question must not read a travel wording.
    cv = Scope.for_product("commercial-vehicle")
    travel_docs = [rel for rel, tag in tagged.items() if tag == "travel-insurance"]
    cv_docs = [rel for rel, tag in tagged.items() if tag == "commercial-vehicle"]
    assert travel_docs and cv_docs
    assert all(not cv.allows_raw(real, rel) for rel in travel_docs)
    assert all(cv.allows_raw(real, rel) for rel in cv_docs)
    # Corporate pages are shared: a scoped turn may still read the contact page.
    assert any(tag == SHARED for tag in index.values())
    assert all(cv.allows_raw(real, rel) for rel, tag in index.items() if tag == SHARED)


# --- layer 1 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "layer1"),
    [
        ("hi", Layer1.smalltalk),
        ("where is my claim now?", Layer1.account_state),
        ("let me speak to someone", Layer1.account_state),
        ("which plan should I buy?", Layer1.advice),
        ("what do you sell", Layer1.browse),
        ("I need some insurance for my family", Layer1.browse),
        ("who underwrites this", Layer1.entity),
        ("what does travel insurance cover?", Layer1.product),
    ],
)
def test_layer_one_names_the_kind_of_turn(bundle: Bundle, question: str, layer1: Layer1) -> None:
    decision = route(bundle, read_ask(bundle, question), question)
    assert decision.layer1 is layer1, decision


def test_a_need_is_shopping_unless_a_product_is_named() -> None:
    assert NEED_RE.search("I need life insurance")
    assert NEED_RE.search("I'm looking for some travel cover")
    assert not NEED_RE.search("what does it cover")


# --- layer 2: named, carried, guessed, none ----------------------------------


def _ask(**kw: Any) -> Ask:
    return Ask(**{"question": "q", "intent": Intent.coverage, **kw})


def test_a_named_product_is_scoped_and_answered(bundle: Bundle) -> None:
    d = route(bundle, _ask(product="travel", product_page="product/general/travel", named_by="title"), "q")
    assert d.layer2 is Layer2.named and not d.clarify
    assert d.scope.scoped and d.scope.product == "travel"


def test_a_carried_product_is_scoped_and_answered(bundle: Bundle) -> None:
    d = route(bundle, _ask(product="travel", product_page="product/general/travel", named_by="history"), "q")
    assert d.layer2 is Layer2.carried and not d.clarify
    assert d.scope.product == "travel"


def test_a_flagship_guess_asks_instead_of_answering(bundle: Bundle) -> None:
    """The customer named a category; the code picked the one whose title
    matched. That is a reading, and an unsure reading asks."""
    d = route(
        bundle,
        _ask(
            product="travel",
            product_page="product/general/travel",
            named_by="flagship",
            family=("product/general/travel", "product/general/home"),
            family_phrase="travel insurance",
        ),
        "q",
    )
    assert d.layer2 is Layer2.guessed
    assert d.clarify
    assert d.options == ("product/general/travel", "product/general/home")
    assert not d.scope.scoped, "a guess is asked about, never scoped to"


def test_no_product_on_a_product_specific_handler_defers_to_the_corpus(bundle: Bundle) -> None:
    """Not asked yet — the corpus may settle it on one product. `needs_product`
    is the pipeline's instruction to find out before it answers."""
    for intent in (Intent.coverage, Intent.exclusion, Intent.limit, Intent.claim, Intent.eligibility):
        d = route(bundle, _ask(intent=intent), "q")
        assert d.layer2 is Layer2.none and d.needs_product and not d.clarify, intent
    settled = route(bundle, _ask(intent=Intent.limit), "q").inferred("travel")
    assert settled.layer2 is Layer2.inferred and settled.scope.product == "travel"


def test_no_product_on_a_definition_answers_from_the_concepts(bundle: Bundle) -> None:
    d = route(bundle, _ask(intent=Intent.definition), "what is an excess")
    assert d.layer3 is Layer3.definition and not d.clarify


def test_no_product_on_price_offer_or_documents_stays_on_the_routed_path(bundle: Bundle) -> None:
    """Those owe a handoff with a destination; asking first would trade a
    correct handoff for a question."""
    for intent in (Intent.price, Intent.offer, Intent.document):
        d = route(bundle, _ask(intent=intent), "q")
        assert not d.clarify and not d.needs_product, intent


def test_layer_three_is_the_intent_handler(bundle: Bundle) -> None:
    pairs = {
        Intent.exclusion: Layer3.exclusions,
        Intent.claim: Layer3.claims,
        Intent.renewal: Layer3.conditions,
        Intent.limit: Layer3.limits,
        Intent.unknown: Layer3.general,
    }
    for intent, handler in pairs.items():
        assert route(bundle, _ask(intent=intent, product="travel", named_by="title"), "q").layer3 is handler


def test_the_decision_is_on_the_trace() -> None:
    d = Decision(
        Layer1.product, Layer2.named, Layer3.coverage, product="travel", scope=Scope.for_product("travel")
    )
    assert d.as_trace() == {
        "layer1": "product",
        "layer2": "named",
        "layer3": "coverage",
        "product": "travel",
        "scope": "travel",
        "reason": "",
    }


# --- end to end --------------------------------------------------------------


def test_a_bare_claim_question_the_corpus_can_settle_is_answered(bundle: Bundle, settings: Settings) -> None:
    """On the seed only one product carries a claims journey, so the corpus
    settles "how do I make a claim?" without a name — and the seed's golden
    suite expects exactly that answer. Unsure means the corpus cannot decide,
    not that no name was typed."""
    env, trace = ask(bundle, settings, "how do I make a claim?")
    assert env.delivered and not env.answer.clarifying
    assert trace.route["layer2"] == "inferred"


def test_an_unsupported_line_is_still_refused_not_asked_about(bundle: Bundle, settings: Settings) -> None:
    """ "Crop insurance" names a line this insurer does not write. The honest
    reply is that we do not carry it — a handoff — never "which policy?"."""
    env, _ = ask(bundle, settings, "does crop insurance cover hail damage?")
    assert env.answer.handoff
    assert not env.answer.clarifying


def test_when_several_products_tie_after_retrieval_the_customer_is_asked(
    bundle: Bundle, settings: Settings
) -> None:
    """The branch that exists for the real corpus, where "how much can I claim
    for a lost bag?" loads four travel products. The seed rarely ties; where
    it does not, the test says so rather than pretending."""
    env, trace = ask(bundle, settings, "what is the excess?")
    if not env.answer.clarifying:
        pytest.skip("the seed resolves this to one product; the branch is exercised on okf-real")
    assert trace.route["layer2"] in ("none", "ambiguous")


def test_shopping_with_no_line_lists_the_lines_rather_than_asking(bundle: Bundle, settings: Settings) -> None:
    env, trace = ask(bundle, settings, "What insurance products do you offer?")
    assert env.delivered and not env.answer.clarifying and not env.answer.handoff
    assert trace.route["layer1"] == "browse"
    assert env.answer.claims, "every product named resolves to a page"


def test_an_unnamed_product_the_corpus_can_settle_is_inferred_and_scoped(
    bundle: Bundle, settings: Settings
) -> None:
    env, trace = ask(bundle, settings, "What is the overseas medical expenses limit?")
    assert env.delivered and env.answer.figures
    assert trace.route["layer1"] == "product"
    # Nothing was named; the corpus settled it — one product carries that benefit.
    assert trace.route["layer2"] == "inferred"
    assert trace.route["scope"] == "travel"


def test_a_definition_answers_without_a_product(bundle: Bundle, settings: Settings) -> None:
    env, trace = ask(bundle, settings, "what does excess mean?")
    assert not env.answer.clarifying
    assert trace.route["layer3"] == "definition"


# --- a category typed as an alias is a guess -------------------------------


@pytest.mark.skipif(not (REAL / "catalogue.yaml").exists(), reason="real bundle not in this checkout")
def test_a_bare_category_alias_is_asked_about_and_a_branded_name_is_not() -> None:
    """ "travel insurance" is one product's alias and four products' category.
    The customer typed the category. "Tiq Travel Insurance" keeps its brand
    word and is that product's name."""
    real = Bundle.load(REAL)
    q = "does travel insurance cover skiing?"
    generic = route(real, read_ask(real, q), q)
    assert generic.layer2 is Layer2.guessed and generic.clarify
    assert len(generic.options) >= 3 and generic.options[0].endswith("/travel-insurance")
    branded_q = "What does Tiq Travel Insurance cover?"
    branded = route(real, read_ask(real, branded_q), branded_q)
    assert branded.layer2 is Layer2.named and not branded.clarify
    single_q = "does home insurance cover flood?"
    single = route(real, read_ask(real, single_q), single_q)
    assert single.layer2 is Layer2.named, "one product carries 'home'; the alias is a name"
    # A *need* for the category is shopping, and is listed rather than asked.
    need_q = "I need travel insurance"
    need = route(real, read_ask(real, need_q), need_q)
    assert need.layer1 is Layer1.browse and not need.clarify


@pytest.mark.skipif(not (REAL / "catalogue.yaml").exists(), reason="real bundle not in this checkout")
def test_a_tie_with_nothing_read_asks_openly_rather_than_listing_junk() -> None:
    import datetime as dt

    real = Bundle.load(REAL)
    settings = Settings(bundle_path=REAL)
    session = make_session(today=dt.date(2026, 9, 4))
    env, trace = answer_question(real, "how much can I claim for a lost bag?", session, settings)  # type: ignore[arg-type]
    assert env.answer.clarifying
    assert not env.answer.claims, "no product was read, so none is named"
    assert trace.route["layer2"] == "none"
