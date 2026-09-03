"""Traces — the harness's own input (§F.4).

Records pages loaded AND pages considered-and-rejected by the frontmatter
filter. The rejected-candidates log is the underrated signal: when the filter
repeatedly rejects the page a human would have wanted, the taxonomy is wrong —
and you only see that if you log rejections, not just selections.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

from harness.contracts import GateResult


class StageTiming(BaseModel):
    name: str
    ms: float
    detail: dict[str, Any] = Field(default_factory=dict)


class Candidate(BaseModel):
    """A page the frontmatter filter considered."""

    page_id: str
    title: str = ""
    admitted: bool = True
    reason: str = ""
    score: float = 0.0
    # 1-based position among admitted candidates. Recorded explicitly because
    # candidates are logged in page-id order for readability, and retrieval
    # metrics must score the ranking, not the alphabet.
    rank: int | None = None


class LoadedPage(BaseModel):
    page_id: str
    title: str = ""
    via: str = "filter"  # filter | graph | index
    hop: int = 0
    chars: int = 0


class RagHit(BaseModel):
    source_path: str
    locator: str = ""
    score: float = 0.0
    excerpt: str = ""


#: (stage name, "start" | "end", milliseconds). Called on the thread that
#: runs the turn; a listener that needs another thread must hop itself.
StageListener = Callable[[str, str, float], None]


class Trace(BaseModel):
    _on_stage: StageListener | None = PrivateAttr(default=None)

    def listen(self, listener: StageListener | None) -> None:
        self._on_stage = listener

    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    question: str = ""
    session_id: str = ""
    channel: str = ""
    created_at: float = Field(default_factory=time.time)

    entities: list[str] = Field(default_factory=list)
    #: Product keys the lexical layer could not choose between. Recorded rather
    #: than resolved: a tie broken alphabetically is a fact about the alphabet,
    #: and the caller is better placed to decide whether to ask the customer.
    ambiguous_products: list[str] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    loaded: list[LoadedPage] = Field(default_factory=list)
    rag_used: bool = False
    rag_reason: str = ""
    #: Which retrieval served the turn: `lexical` or `hybrid`. Recorded so an
    #: evaluation can refuse to score a "hybrid" run that was served lexically
    #: because the database was down — the same refusal the batch runner makes
    #: for a dead model.
    retrieval_mode: str = "lexical"
    #: Why the vector layer did not run, when it was configured and did not.
    vector_degraded: str = ""
    rag_hits: list[RagHit] = Field(default_factory=list)
    sor_calls: list[str] = Field(default_factory=list)

    figures_resolved: list[dict[str, str]] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)

    gates: list[GateResult] = Field(default_factory=list)
    stages: list[StageTiming] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    composer: str = ""
    notes: list[str] = Field(default_factory=list)
    # The draft a gate refused to deliver. Kept for the debug console — a
    # blocked answer you cannot inspect teaches you nothing.
    blocked_draft: str = ""
    delivered: bool = True
    # The delivered contract, so a stored trace replays faithfully in the console.
    answer: dict[str, Any] | None = None

    @property
    def rejected(self) -> list[Candidate]:
        return [c for c in self.candidates if not c.admitted]

    @property
    def total_ms(self) -> float:
        return round(sum(s.ms for s in self.stages), 2)

    @contextmanager
    def stage(self, name: str, **detail: Any) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        payload: dict[str, Any] = dict(detail)
        if self._on_stage is not None:
            self._on_stage(name, "start", 0.0)
        try:
            yield payload
        finally:
            ms = round((time.perf_counter() - started) * 1000, 2)
            self.stages.append(StageTiming(name=name, ms=ms, detail=payload))
            if self._on_stage is not None:
                self._on_stage(name, "end", ms)

    def note(self, message: str) -> None:
        self.notes.append(message)


class TraceStore:
    """In-memory ring buffer of recent traces for the debug console. A real
    deployment ships these to Langfuse (§H.1); the console reads the same shape."""

    def __init__(self, capacity: int = 200) -> None:
        self.capacity = capacity
        self._order: list[str] = []
        self._items: dict[str, Trace] = {}

    def put(self, trace: Trace) -> None:
        self._items[trace.trace_id] = trace
        self._order.append(trace.trace_id)
        while len(self._order) > self.capacity:
            evicted = self._order.pop(0)
            self._items.pop(evicted, None)

    def get(self, trace_id: str) -> Trace | None:
        return self._items.get(trace_id)

    def recent(self, limit: int = 25) -> list[Trace]:
        return [self._items[t] for t in reversed(self._order[-limit:])]

    def all(self) -> list[Trace]:
        return [self._items[t] for t in self._order]

    def clear(self) -> None:
        self._order.clear()
        self._items.clear()
