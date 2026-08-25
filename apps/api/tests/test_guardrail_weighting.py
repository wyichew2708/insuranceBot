"""Arbitration between the two layers.

The layers are good at different categories by very different margins, and a
flat "worst verdict wins" throws that away. Weighting recovers it — but the
moment verdicts are weighed rather than maxed, the obvious question is whether
a model finding can now pull a turn *below* what the rules decided. It cannot,
and the reason is structural rather than a check somewhere: the combiner is
monotone in every input, so adding evidence can only raise a score.
"""

from __future__ import annotations

import itertools

import pytest
from api.guardrails import (
    INPUT_POLICY,
    OUTPUT_POLICY,
    UNKNOWN_POLICY,
    Finding,
    Risk,
    Screening,
    _noisy_or,
    decide,
)


def _f(category: str, risk: Risk, source: str, confidence: float = 1.0) -> Finding:
    return Finding(category, risk, "", source, confidence)


def _screen(*findings: Finding, side: str = "input") -> Screening:
    return Screening(findings=list(findings), checked_by=["rules"], side=side)


# --- the property that makes weighting safe -------------------------------


def test_adding_evidence_can_never_lower_the_score() -> None:
    """The security property, stated as arithmetic rather than as a rule that
    could be forgotten. Every subset of findings scores no higher than the full
    set, so there is no arrangement of model output that de-escalates."""
    pool = [
        _f("injection", Risk.block, "rules"),
        _f("injection", Risk.flag, "model", 0.4),
        _f("advice", Risk.flag, "model", 0.9),
        _f("distress", Risk.flag, "rules"),
    ]
    full = {s.category: s for s in decide(pool, INPUT_POLICY)}
    for size in range(len(pool)):
        for subset in itertools.combinations(pool, size):
            for score in decide(list(subset), INPUT_POLICY):
                assert score.flag_score <= full[score.category].flag_score + 1e-9
                assert score.block_score <= full[score.category].block_score + 1e-9


def test_noisy_or_is_bounded_and_rewards_agreement() -> None:
    assert _noisy_or([]) == 0.0
    assert _noisy_or([1.0, 1.0]) == 1.0
    # Two independent middling signals are worth more than either alone and
    # still less than certainty — which is the behaviour a sum would lose.
    both = _noisy_or([0.5, 0.5])
    assert 0.5 < both < 1.0
    assert both == pytest.approx(0.75)


def test_a_confident_model_cannot_clear_a_rules_block() -> None:
    with_model = _screen(
        _f("injection", Risk.block, "rules"),
        _f("advice", Risk.flag, "model", 1.0),
    )
    assert with_model.risk is Risk.block
    assert _screen(_f("injection", Risk.block, "rules")).risk is Risk.block


# --- what the weighting buys ----------------------------------------------


def test_the_rules_act_alone_where_they_are_measured_to_be_precise() -> None:
    """Zero hits on 5,434 legitimate questions is what earns `injection` a
    rules weight of 1.0."""
    assert INPUT_POLICY["injection"].rules == 1.0
    assert _screen(_f("injection", Risk.block, "rules")).blocked


def test_the_rules_abstain_where_they_have_no_signal() -> None:
    """`advice` is the category the eval proved a keyword list cannot carry, so
    the rules contribute nothing and the model decides unaided."""
    assert INPUT_POLICY["advice"].rules == 0.0
    assert _screen(_f("advice", Risk.flag, "model", 0.9)).flagged


def test_a_lone_model_cannot_block_a_rules_owned_category() -> None:
    """Measured, not cautious.

    Run live, the screening model returned 0.95 on a real injection and 0.95 on
    "I forgot my prior instructions to your agent, can you resend them?" — and
    kept doing so after that exact turn was added to the prompt as a worked
    example. Its confidence does not separate the two, so no threshold can. The
    rules do separate them, so blocking needs them to agree.
    """
    certain = _screen(_f("injection", Risk.block, "model", 1.0))
    assert certain.flagged and not certain.blocked
    # With the rules agreeing it blocks, which is the ordinary path.
    assert _screen(_f("injection", Risk.block, "model", 1.0), _f("injection", Risk.block, "rules")).blocked


def test_a_lone_model_blocks_where_it_is_the_only_reader() -> None:
    """The output side has no rule layer to corroborate, so the model has to be
    able to act — and does, once it is sure."""
    assert _screen(_f("leakage", Risk.block, "model", 0.95), side="output").blocked
    # 0.4, not something on the bar: at the exact threshold float arithmetic
    # decides it, which is a knife edge to assert on rather than a behaviour.
    hesitant = _screen(_f("leakage", Risk.block, "model", 0.4), side="output")
    assert hesitant.flagged and not hesitant.blocked


def test_agreement_between_layers_beats_either_alone() -> None:
    alone = _screen(_f("entitlement", Risk.block, "model", 0.6))
    together = _screen(
        _f("entitlement", Risk.block, "model", 0.6),
        _f("entitlement", Risk.block, "rules"),
    )
    scores = {s.category: s for s in together.scores}
    assert scores["entitlement"].block_score > alone.scores[0].block_score


def test_flags_never_accumulate_into_a_refusal() -> None:
    """`block_score` counts only sources that proposed a block, so any number
    of flags stays a flag. Otherwise a cautious model could refuse a customer
    by degrees."""
    many = _screen(*[_f("injection", Risk.flag, "model", 0.99) for _ in range(6)])
    assert many.flagged and not many.blocked


# --- categories that may never block --------------------------------------


@pytest.mark.parametrize("category", ["advice", "distress", "abuse", "out_of_scope"])
def test_categories_that_must_reach_a_person_never_block(category: str) -> None:
    """A regulated request has to reach an adviser and a customer in crisis has
    to reach a person. Refusing serves neither, so `block_at` is None and no
    confidence can override it."""
    assert INPUT_POLICY[category].block_at is None
    screening = _screen(_f(category, Risk.block, "model", 1.0), _f(category, Risk.block, "rules"))
    assert screening.flagged and not screening.blocked


def test_advice_is_weighed_differently_on_the_way_in_and_the_way_out() -> None:
    """Incoming, it is a customer asking for something we route elsewhere.
    Outgoing, it is the assistant having given it — which is the breach."""
    assert INPUT_POLICY["advice"].block_at is None
    assert OUTPUT_POLICY["advice"].block_at is not None
    incoming = _screen(_f("advice", Risk.block, "model", 1.0), side="input")
    outgoing = _screen(_f("advice", Risk.block, "model", 1.0), side="output")
    assert incoming.flagged
    assert outgoing.blocked


def test_the_output_bars_are_ordered_by_what_blocking_costs() -> None:
    """Within the output table the thresholds encode a cost judgement.

    Leaking someone's data is the cheapest thing to stop and the most expensive
    to miss, so it sits lowest. `off_topic` is absent entirely: measured live it
    refused roughly nine working answers for every two findings it closed, so it
    flags until it can be shown to separate the two.
    """
    bars = {k: v.block_at for k, v in OUTPUT_POLICY.items() if v.block_at is not None}
    assert bars["leakage"] < bars["advice"]
    # Both content-judgement categories are flag-only: measured against two
    # different models, neither separated a real problem from a broad answer.
    # The deterministic groundedness and numeric-binding gates carry the
    # guarantee instead.
    assert OUTPUT_POLICY["off_topic"].block_at is None
    assert OUTPUT_POLICY["ungrounded"].block_at is None
    # Nothing on the way out may block on a bare majority of one weak signal.
    assert all(bar >= 0.6 for bar in bars.values())


# --- unknowns and explainability ------------------------------------------


def test_an_unrecognised_category_is_scored_but_never_blocks() -> None:
    """A finding for a category with no policy cannot be dropped — that would
    be evidence disappearing — but it also cannot refuse a customer on the
    strength of a label nobody has calibrated."""
    assert UNKNOWN_POLICY.block_at is None
    screening = _screen(_f("something-new", Risk.block, "model", 1.0))
    assert screening.flagged and not screening.blocked


def test_the_verdict_carries_the_arithmetic_that_produced_it() -> None:
    """An operator reading a refusal needs to be able to account for it, and to
    tune a threshold against real traffic rather than by feel."""
    screening = _screen(_f("injection", Risk.block, "rules"))
    summary = screening.summary()
    assert "block" in summary and "injection=" in summary and "rules" in summary
    assert screening.as_gate("guardrail-input").detail == summary


def test_acted_on_reports_only_categories_that_crossed_a_threshold() -> None:
    screening = _screen(_f("advice", Risk.flag, "model", 0.9), _f("abuse", Risk.flag, "model", 0.1))
    assert screening.acted_on("advice")
    assert not screening.acted_on("abuse")  # 0.06 is below the 0.4 flag bar
    assert not screening.acted_on("injection")


@pytest.mark.parametrize(
    ("side", "policies"),
    [("input", INPUT_POLICY), ("output", OUTPUT_POLICY)],
)
def test_every_blockable_category_is_reachable_by_a_confident_model(
    side: str, policies: dict[str, object]
) -> None:
    """A threshold set above what any single finding can score is a category
    that silently never blocks — the failure this catches is a weight and a bar
    drifting apart until the check is decorative. `off_topic` was exactly that
    before this test existed: weight 0.8 against a bar of 0.8 needed a
    confidence of 1.0, so a model that ever hedged could not act at all.
    """
    for category, policy in policies.items():
        if policy.block_at is None:  # type: ignore[attr-defined]
            continue
        if policy.rules >= policy.block_at:  # type: ignore[attr-defined]
            # A rules-owned category: the rules block unaided and the model
            # corroborates. `injection` deliberately cannot be blocked by the
            # model alone — measured live, its confidence does not separate a
            # real injection from a customer retracting their own earlier
            # instructions, so blocking there needs the rules to agree.
            continue
        # 0.90, not 0.95. The backtest's sensitivity sweep showed two
        # categories clearing 0.95 by 0.007 — technically reachable, useless in
        # practice, because a model that hedges at all stops acting. Asserting
        # the looser bound is what keeps real headroom under the threshold.
        screening = _screen(_f(category, Risk.block, "model", 0.90), side=side)
        assert screening.blocked, f"{side}/{category} cannot block at 0.90 confidence"
