"""Prompt-injection screen shared by the gateway (user input) and the
orchestrator (web-index text at retrieval time) (§9.2).

Matches are neutralised, never obeyed; callers log the flag.
"""

from __future__ import annotations

import re

_INJECTION_RE = re.compile(
    r"(ignore (all )?(previous|prior|above) (instructions?|prompts?)"
    r"|disregard (your|the) (instructions?|system prompt)"
    r"|you are now\b"
    r"|new system prompt"
    r"|reveal (your|the) (system )?prompt"
    r"|act as (if|though) you (are|were))",
    re.IGNORECASE,
)


def screen_injection(text: str) -> tuple[str, bool]:
    """Returns (screened_text, flagged)."""
    flagged = bool(_INJECTION_RE.search(text))
    screened = _INJECTION_RE.sub("[removed-instruction]", text)
    return screened, flagged
