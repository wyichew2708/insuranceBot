import pytest
from orchestrator.router import Route, route_message

EMERGENCY = [
    "Help, I'm in hospital in Bangkok after an accident",
    "There is an emergency, my husband collapsed abroad",
    "We need urgent medical evacuation from Bali",
    "my passport was stolen in Paris, what do I do right now",
]

SERVICING = [
    "How do I cancel my travel policy?",
    "I want to update my address on my policy",
    "How can I check my claim status?",
    "change my payment method to GIRO",
]

COVERAGE = [
    "Does travel insurance cover pre-existing conditions?",
    "What is the maximum medical benefit?",
]

DISCOVERY = [
    "Can you compare the travel plans?",
    "What's the difference between Entry and Luxury tiers?",
]

SMALLTALK = ["hi", "Hello!", "thanks", "bye"]


@pytest.mark.parametrize("msg", EMERGENCY)
def test_emergency_first(msg: str) -> None:
    assert route_message(msg).route == Route.emergency


@pytest.mark.parametrize("msg", SERVICING)
def test_servicing(msg: str) -> None:
    assert route_message(msg).route == Route.servicing


@pytest.mark.parametrize("msg", COVERAGE)
def test_coverage_qa(msg: str) -> None:
    assert route_message(msg).route == Route.coverage_qa


@pytest.mark.parametrize("msg", DISCOVERY)
def test_discovery(msg: str) -> None:
    assert route_message(msg).route == Route.discovery


@pytest.mark.parametrize("msg", SMALLTALK)
def test_smalltalk_out_of_scope(msg: str) -> None:
    assert route_message(msg).route == Route.out_of_scope


def test_emergency_beats_servicing_keywords() -> None:
    # "claim" keyword present, but the ongoing-emergency signal must win.
    msg = "I'm hospitalised overseas, do I submit a claim now? This is an emergency, I need help"
    assert route_message(msg).route == Route.emergency
