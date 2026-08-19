"""Budgets and boundedness (§F.3).

Long-horizon runs fail by drifting, not by crashing. Exhaustion is a *defined
exit* — summarise what was established and hand off — never a loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


class BudgetExhausted(Exception):
    def __init__(self, resource: str, limit: float) -> None:
        super().__init__(f"budget exhausted: {resource} (limit {limit})")
        self.resource = resource
        self.limit = limit


@dataclass
class Budget:
    max_pages: int = 8
    max_tool_calls: int = 6
    max_wall_clock_s: float = 10.0
    max_tokens: int = 20_000

    pages_loaded: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    started: float = field(default_factory=time.perf_counter)
    exhausted_on: str | None = None

    def charge_page(self, count: int = 1) -> None:
        self.pages_loaded += count
        self._check("pages", self.pages_loaded, self.max_pages)

    def charge_tool(self, count: int = 1) -> None:
        self.tool_calls += count
        self._check("tool_calls", self.tool_calls, self.max_tool_calls)

    def charge_tokens(self, count: int) -> None:
        self.tokens_used += count
        self._check("tokens", self.tokens_used, self.max_tokens)

    def check_clock(self) -> None:
        self._check("wall_clock_s", self.elapsed_s, self.max_wall_clock_s)

    @property
    def elapsed_s(self) -> float:
        return time.perf_counter() - self.started

    def _check(self, resource: str, used: float, limit: float) -> None:
        if used > limit:
            self.exhausted_on = resource
            raise BudgetExhausted(resource, limit)

    def would_exceed_pages(self, extra: int = 1) -> bool:
        return self.pages_loaded + extra > self.max_pages

    def snapshot(self) -> dict[str, Any]:
        return {
            "pages_loaded": self.pages_loaded,
            "max_pages": self.max_pages,
            "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "tokens_used": self.tokens_used,
            "max_tokens": self.max_tokens,
            "elapsed_ms": round(self.elapsed_s * 1000, 2),
            "max_wall_clock_ms": self.max_wall_clock_s * 1000,
            "exhausted_on": self.exhausted_on,
        }
