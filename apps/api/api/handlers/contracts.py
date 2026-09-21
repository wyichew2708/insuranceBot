"""The input and evidence contract shared by registered answer workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from harness import Budget, Session, Trace
from harness.ask import Ask

from api.understand import Understanding
from okf import Bundle

if TYPE_CHECKING:
    from api.guardrails import Guard, Screening
    from api.llm import LLMProvider
    from api.router import Decision
    from api.settings import Settings


class Evidence(str, Enum):
    catalogue = "catalogue"
    wiki = "wiki"
    raw = "raw"
    sor = "sor"
    registry = "registry"


class EvidenceViolation(RuntimeError):
    """A workflow attempted to use a source outside its declared contract."""


@dataclass
class Turn:
    bundle: Bundle
    question: str
    session: Session
    settings: Settings
    trace: Trace
    budget: Budget
    raw_root: Path
    provider: LLMProvider
    guard: Guard
    incoming: Screening
    ask: Ask
    decision: Decision
    understanding: Understanding = field(default_factory=Understanding)
    seeking_advice: bool = False
    carried_from: str | None = None
    evidence: frozenset[Evidence] = frozenset()
    soft_gates: frozenset[str] = frozenset()

    def require(self, source: Evidence) -> None:
        if source not in self.evidence:
            raise EvidenceViolation(f"{self.trace.handler} cannot use {source.value}")
