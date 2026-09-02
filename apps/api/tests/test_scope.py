"""An answer says when the benefit the customer named was not found."""

from __future__ import annotations

from api.pipeline import answer_question
from api.settings import Settings
from harness import AuthLevel, Channel, Session

from okf import Bundle


def test_a_named_benefit_the_pages_do_not_mention_is_called_out(bundle: Bundle, settings: Settings) -> None:
    session = Session(session_id="t", channel=Channel("channel/direct"), auth_level=AuthLevel("L0"))
    # "section 99" names a benefit no seed page has a heading for.
    env, _ = answer_question(bundle, "what does travel insurance say about section 99", session, settings)
    if not env.delivered:
        return
    assert "do not address section 99" in env.answer.answer


def test_a_named_benefit_that_is_addressed_gets_no_disclaimer(bundle: Bundle, settings: Settings) -> None:
    session = Session(session_id="t", channel=Channel("channel/direct"), auth_level=AuthLevel("L0"))
    env, _ = answer_question(bundle, "is my luggage covered under travel insurance", session, settings)
    assert "do not address" not in env.answer.answer
