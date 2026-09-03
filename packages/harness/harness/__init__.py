"""Harness layer: contracts, gates, budgets, tracing."""

from harness.ask import Ask, ask_about, asked_benefits, read_ask
from harness.budget import Budget, BudgetExhausted
from harness.contracts import (
    AnswerEnvelope,
    AnswerRequest,
    AuthLevel,
    Channel,
    ChannelRender,
    Claim,
    Figure,
    GateResult,
    GroundedAnswer,
    PolicyContext,
    Session,
    Verdict,
)
from harness.gates import ALL_GATES, GateContext, Judge, blocked, run_gates
from harness.trace import Candidate, LoadedPage, RagHit, StageTiming, Trace, TraceStore

__all__ = [
    "ALL_GATES",
    "AnswerEnvelope",
    "AnswerRequest",
    "Ask",
    "AuthLevel",
    "Budget",
    "BudgetExhausted",
    "Candidate",
    "Channel",
    "ChannelRender",
    "Claim",
    "Figure",
    "GateContext",
    "GateResult",
    "GroundedAnswer",
    "Judge",
    "LoadedPage",
    "PolicyContext",
    "RagHit",
    "Session",
    "StageTiming",
    "Trace",
    "TraceStore",
    "Verdict",
    "ask_about",
    "asked_benefits",
    "blocked",
    "read_ask",
    "run_gates",
]
