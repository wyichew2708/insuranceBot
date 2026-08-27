"""Saying what is missing, instead of that something is.

"I could not establish that from our approved product pages" is true of every
refusal this system gives and useful in none of them. A customer told the
premium is not published can go and get a quote; a customer told the generic
sentence can only ask again, differently, and get it back.
"""

from __future__ import annotations

from api.compose import NO_ANSWER, shortfall
from api.pipeline import answer_question
from api.settings import Settings

from conftest import make_session
from okf import Bundle


def test_a_price_question_says_where_a_price_comes_from(bundle: Bundle) -> None:
    product = bundle.get("product/general/travel")
    assert product is not None
    said = shortfall("how much does it cost a year", product)
    assert "premium" in said.lower()
    assert said != NO_ANSWER


def test_an_unrecognised_intent_keeps_the_generic_refusal(bundle: Bundle) -> None:
    """Something precise and wrong is worse than something vague and true."""
    product = bundle.get("product/general/travel")
    assert shortfall("what is the airspeed of a swallow", product) == NO_ANSWER


def test_no_product_keeps_the_generic_refusal(bundle: Bundle) -> None:
    assert shortfall("how much does it cost", None) == NO_ANSWER


def test_a_refusal_names_the_product_not_a_child_page(bundle: Bundle) -> None:
    """`_product_page` can settle on `.../conditions`, whose title is
    "Public liability — Policy conditions" — not a product anybody bought."""
    child = bundle.get("product/general/travel/exclusions")
    assert child is not None
    assert child.frontmatter.title == "Travel Insurance — Exclusions"
    said = shortfall("what does it cost", child)
    assert "premiums for Travel Insurance" in said
    assert "Exclusions" not in said


def test_a_price_question_is_not_settled_by_any_old_figure(bundle: Bundle, settings: Settings) -> None:
    """ "how much does travel insurance cost a year" passed on a `$350` quoted
    out of a wording about lost passports. A price question is settled by a
    premium, not by a number."""
    env, _ = answer_question(bundle, "how much does travel insurance cost a year", make_session(), settings)
    assert env.answer.handoff, env.answer.answer
    assert "premium" in env.answer.answer.lower()


def test_a_gate_that_caught_a_problem_does_not_claim_a_gap(bundle: Bundle, settings: Settings) -> None:
    """Only `answerability` means "nothing here settles this". Every other gate
    means "we caught something wrong with the draft", and telling a customer
    that a groundedness failure is a missing premium would be false."""
    from api.compose import shortfall as _s

    assert _s("how much does it cost", bundle.get("product/general/travel")) != NO_ANSWER
