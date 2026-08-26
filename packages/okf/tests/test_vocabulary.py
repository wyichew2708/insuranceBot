"""Customer words for benefits the corpus names differently."""

from __future__ import annotations

from pathlib import Path

from okf import expand_vocabulary, load_vocabulary

ROOT = Path(__file__).resolve().parents[3]


def test_the_seed_bundle_carries_a_vocabulary() -> None:
    vocabulary = load_vocabulary(ROOT / "okf")
    assert vocabulary
    assert "baggage_loss" in vocabulary and "contents" in vocabulary


def test_a_bundle_without_one_still_loads() -> None:
    """Absent is a working state: situational phrasings keep failing the way
    they do today rather than the bundle failing to load."""
    assert load_vocabulary(Path("/nonexistent")) == {}


def test_situations_expand_to_the_benefit_they_describe() -> None:
    """These are the phrasings the eval suite finds hardest, because a customer
    mid-loss describes what happened rather than naming a benefit."""
    vocabulary = load_vocabulary(ROOT / "okf")
    cases = {
        "The airline lost my suitcase. How much can I claim?": "baggage_loss",
        "My place was broken into and things were taken.": "contents",
        "There was a fire and I cannot live in my flat.": "alternative_accommodation",
        "I scraped my car reversing into a pillar.": "own_damage",
        "My flight home was delayed overnight.": "travel_delay",
    }
    for question, benefit in cases.items():
        assert benefit in expand_vocabulary(question, vocabulary), question


def test_a_question_that_names_the_benefit_needs_no_expansion() -> None:
    """The vocabulary is a bridge for customers who lack the corpus's words. A
    question that already has them should not be broadened."""
    vocabulary = load_vocabulary(ROOT / "okf")
    assert expand_vocabulary("What is the baggage limit on Travel Insurance?", vocabulary) == set()


def test_terms_are_specific_enough_not_to_fire_on_ordinary_questions() -> None:
    """A term like "fire" would match "fire insurance". Every entry has to be
    narrow enough that a routine product question does not trip it."""
    vocabulary = load_vocabulary(ROOT / "okf")
    ordinary = [
        "What does Home Insurance cover?",
        "Do you sell fire insurance?",
        "What is the excess on private car insurance?",
        "How do I claim for a delayed flight?",
        "Is there a cap on contents cover?",
    ]
    for question in ordinary:
        assert expand_vocabulary(question, vocabulary) == set(), question
