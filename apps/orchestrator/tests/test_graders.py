"""Table-driven grader tests (§11)."""

import datetime as dt

from orchestrator.verification import (
    Draft,
    Evidence,
    extract_verbatim_tokens,
    grade_advice_boundary,
    grade_audience_leak,
    grade_citation_presence,
    grade_disclaimer_attach,
    grade_no_execution_claims,
    grade_promo_freshness,
    grade_verbatim_digits,
    run_rule_graders,
    verdict,
)

TODAY = dt.date(2026, 7, 31)


def ev(**kw: object) -> Evidence:
    base = Evidence(today=TODAY)
    for k, v in kw.items():
        setattr(base, k, v)
    return base


# --- citation-presence ---


def test_factual_without_citation_fails() -> None:
    r = grade_citation_presence(Draft(text="The limit is high."), ev())
    assert not r.passed


def test_citation_must_be_permitted() -> None:
    d = Draft(text="x", citations=["blk-1"])
    assert not grade_citation_presence(d, ev(permitted_chunk_ids=set())).passed
    assert grade_citation_presence(d, ev(permitted_chunk_ids={"blk-1"})).passed


def test_non_factual_needs_no_citation() -> None:
    assert grade_citation_presence(Draft(text="Hello!", is_factual=False), ev()).passed


# --- verbatim-digits ---


def test_extracts_phones_emails_swift_accounts() -> None:
    text = "Call 1234 5678 or +99 8765 4321, email help@example.test, SWIFT ABCDSGSG, acct 123-45678-9"
    tokens = extract_verbatim_tokens(text)
    assert "help@example.test" in tokens
    assert "ABCDSGSG" in tokens
    assert any("8765" in t for t in tokens)
    assert "123-45678-9" in tokens


def test_verbatim_digit_must_match_cited_source_exactly() -> None:
    d = Draft(text="Please call 1234 5678.", citations=["c1"])
    ok = ev(cited_texts={"c1": "Hotline: 1234 5678 (weekdays)"})
    assert grade_verbatim_digits(d, ok).passed
    drifted = ev(cited_texts={"c1": "Hotline: 1234 5679 (weekdays)"})
    assert not grade_verbatim_digits(d, drifted).passed


def test_verbatim_may_come_from_actions_registry() -> None:
    d = Draft(text="Email us at help@example.test")
    assert grade_verbatim_digits(d, ev(action_values={"a": "help@example.test"})).passed


# --- audience-leak ---


def test_internal_block_in_public_session_fails() -> None:
    d = Draft(text="x", citations=["int-1"])
    e = ev(cited_audiences={"int-1": "internal"}, session_audience="public")
    assert not grade_audience_leak(d, e).passed


def test_internal_session_may_cite_internal() -> None:
    d = Draft(text="x", citations=["int-1"])
    e = ev(cited_audiences={"int-1": "internal"}, session_audience="internal")
    assert grade_audience_leak(d, e).passed


# --- promo-freshness ---


def test_promo_claim_without_live_citation_fails() -> None:
    d = Draft(text="Get 20% off with promo code SAVE", citations=["kb-1"])
    assert not grade_promo_freshness(d, ev()).passed


def test_promo_claim_with_live_window_passes() -> None:
    d = Draft(text="20% off this month", citations=["web-1"])
    e = ev(promo_windows={"web-1": (dt.datetime(2026, 8, 15), dt.date(2026, 7, 1))})
    assert grade_promo_freshness(d, e).passed


def test_expired_promo_fails() -> None:
    d = Draft(text="20% off this month", citations=["web-1"])
    e = ev(promo_windows={"web-1": (dt.datetime(2026, 6, 30), dt.date(2026, 6, 1))})
    assert not grade_promo_freshness(d, e).passed


# --- no-execution-claims ---


def test_execution_claims_fail() -> None:
    for text in [
        "I have updated your address.",
        "I've cancelled the policy for you.",
        "Your request has been submitted.",
        "Your policy is now renewed.",
    ]:
        assert not grade_no_execution_claims(Draft(text=text), ev()).passed, text


def test_guidance_language_passes() -> None:
    for text in [
        "You can update your address in the app under Profile.",
        "To cancel, submit the form via the customer portal.",
        "I can't make this change for you, but here are the steps.",
    ]:
        assert grade_no_execution_claims(Draft(text=text), ev()).passed, text


# --- disclaimer-attach ---


def test_benefit_answer_needs_exactly_one_disclaimer() -> None:
    d0 = Draft(text="Covers up to the plan limit.", is_product_benefit_answer=True)
    assert not grade_disclaimer_attach(d0, ev()).passed
    d1 = Draft(text="Covers up to the plan limit. [disclaimer]", is_product_benefit_answer=True)
    assert grade_disclaimer_attach(d1, ev()).passed
    d2 = Draft(text="[disclaimer] x [disclaimer]", is_product_benefit_answer=True)
    assert not grade_disclaimer_attach(d2, ev()).passed


# --- advice-boundary ---


def test_advice_without_routing_fails() -> None:
    d = Draft(text="You should buy the investment-linked plan, guaranteed returns!")
    assert not grade_advice_boundary(d, ev()).passed


def test_advice_with_get_advice_routing_passes() -> None:
    d = Draft(text="I recommend the savings plan for many customers. [get-advice]")
    assert grade_advice_boundary(d, ev()).passed


# --- pipeline ---


def test_run_all_graders_and_verdict() -> None:
    d = Draft(text="Hello!", is_factual=False)
    results = run_rule_graders(d, ev())
    assert len(results) == 7
    assert verdict(results)
