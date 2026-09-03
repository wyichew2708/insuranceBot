"""The presentation layer, the next-question chips, the memory, and the PII rules."""

from __future__ import annotations

from pathlib import Path

import pytest
from api.guardrails import redact_pii, screen_input_rules, screen_output_rules
from api.memory import SessionMemory, summarise_turn
from api.present import present_overview
from api.suggest import closing_question, suggest_next
from harness.ask import Ask, read_ask
from harness.contracts import AnswerEnvelope, GroundedAnswer
from harness.intent import Intent

from okf import Bundle


@pytest.fixture(scope="module")
def real() -> Bundle:
    return Bundle.load(Path("okf-real"))


# --- presentation --------------------------------------------------------------


def test_an_introduction_is_a_line_a_list_and_a_question(real: Bundle) -> None:
    home = real.get("product/general/home-insurance")
    assert home is not None
    text = (
        "The policy wording sets out cover under: Renovation; Home Contents; Personal Legal Liability.\n\n"
        "Protect what belongs to you when you insure your household contents, "
        "renovation and mortgage payments.\n\n"
        "No. Each property can only be covered under one active policy at a time.\n\n"
        "You can continue here: https://www.tiq.com.sg/product/home-insurance or call +65 6336 0477."
    )
    shaped = present_overview(
        text, home, "What would you like to know more about — what's not covered, or how to claim?"
    )
    lines = shaped.split("\n")
    assert lines[0].startswith("**Tiq Home Insurance**")
    assert "What it covers:" in shaped
    assert "- Set out in the policy wording: Renovation; Home Contents; Personal Legal Liability." in shaped
    # The stray FAQ answer is gone; the route and the closing question remain, in that order.
    assert "Each property can only be covered" not in shaped
    assert shaped.index("You can continue here") < shaped.index("What would you like")


def test_presentation_keeps_every_figure_verbatim(real: Bundle) -> None:
    travel = real.get("product/general/travel-insurance")
    assert travel is not None
    text = (
        "Travel delay cover from just 3 hours: Be covered for travel delays starting from just 3 hours, "
        "instead of the usual 6 hours."
    )
    shaped = present_overview(text, travel, "")
    assert "3 hours" in shaped and "6 hours" in shaped


# --- suggestions --------------------------------------------------------------


def test_suggestions_follow_the_question_and_the_corpus(real: Bundle) -> None:
    home = real.get("product/general/home-insurance")
    assert home is not None
    ask = read_ask(real, "tiq home")
    chips = suggest_next(real, ask, home)
    assert 1 <= len(chips) <= 4
    assert all("Tiq Home Insurance" in c for c in chips)
    assert any("not cover" in c for c in chips)
    # After an exclusions question, exclusions are not offered again.
    after = suggest_next(real, read_ask(real, "what does tiq home insurance not cover"), home)
    assert not any("not cover" in c for c in after)


def test_a_clarifying_answer_offers_no_extra_chips(real: Bundle) -> None:
    assert suggest_next(real, None, None, clarifying=True) == []


def test_the_closing_question_matches_the_chips() -> None:
    line = closing_question(
        [
            "What does Tiq Home Insurance not cover?",
            "How do I make a claim on Tiq Home Insurance?",
            "How do I buy Tiq Home Insurance?",
        ]
    )
    assert "what's not covered" in line and "how to make a claim" in line and line.endswith("how to buy?")


# --- memory -------------------------------------------------------------------


def test_memory_remembers_and_recalls(tmp_path: Path) -> None:
    memory = SessionMemory(tmp_path)
    ask = Ask(question="tiq home", intent=Intent.unknown, product="home-insurance", scope="overview")
    envelope = AnswerEnvelope(
        answer=GroundedAnswer(answer="**Tiq Home Insurance** — Protect what belongs to you.")
    )
    line = memory.remember("s1", "tiq home", envelope, ask)
    assert line.startswith("[home-insurance · unknown] asked: tiq home →")
    recalled = memory.recall("s1")
    assert recalled.questions == ["tiq home"]
    assert recalled.last_product == "home-insurance"
    assert recalled.summary == line
    # A second turn, and the record persists on disk for a fresh instance.
    memory.remember(
        "s1",
        "how do I claim",
        AnswerEnvelope(answer=GroundedAnswer(answer="Notify us within 30 days.")),
        None,
    )
    again = SessionMemory(tmp_path).recall("s1")
    assert again.questions == ["tiq home", "how do I claim"]


def test_summary_names_the_outcome_not_just_the_text() -> None:
    envelope = AnswerEnvelope(answer=GroundedAnswer(answer="Which one?", clarifying=True))
    assert "asked which product was meant" in summarise_turn("tiq", envelope, None)


# --- guardrails ---------------------------------------------------------------


def test_pii_is_redacted_and_flagged_on_the_way_in() -> None:
    masked, kinds = redact_pii(
        "my NRIC is S1234567A and card 4111 1111 1111 1111, email me at a.b@example.com"
    )
    assert "S1234567A" not in masked and "4111" not in masked and "example.com" not in masked
    assert set(kinds) == {"nric", "card", "email"}
    screening = screen_input_rules("my NRIC is S1234567A, what does home insurance cover")
    assert any(f.category == "pii" for f in screening.findings)
    assert not screening.blocked  # never a refusal


def test_an_answer_may_not_carry_pii_or_foreign_links_or_abuse() -> None:
    out = screen_output_rules("Your NRIC S1234567A is on file. See https://example.com/deal. You idiot.", [])
    categories = {f.category for f in out.findings}
    assert {"leakage", "external-link", "toxicity"} <= categories
    assert out.blocked
    clean = screen_output_rules("You can continue here: https://www.tiq.com.sg/product/home-insurance", [])
    assert not clean.blocked


def test_the_insurers_own_address_and_a_policy_number_are_not_pii() -> None:
    masked, kinds = redact_pii("Write to nonmotor@etiqa.com.sg quoting policy 1234567890123456789.")
    assert kinds == [] and masked.endswith("1234567890123456789.")
    assert not screen_output_rules("Please email nonmotor@etiqa.com.sg with your claim.", []).blocked
