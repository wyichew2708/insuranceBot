"""PII redaction pre-model (§9.2).

v1 is regex-based (NRIC/FIN, passport, policy number, email, SG phone);
Presidio slots in behind the same interface in Phase 4 hardening. Both the
raw (encrypted at rest) and redacted forms are stored per §4.2 messages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters: more specific patterns first so placeholders don't overlap.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("NRIC", re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)),
    ("PASSPORT", re.compile(r"\b[EK]\d{7}[A-Z]\b", re.IGNORECASE)),
    (
        "POLICY_NO",
        re.compile(r"\b(?:policy\s*(?:no\.?|number)?\s*[:#]?\s*)([A-Z]{1,4}[-/]?\d{6,12})\b", re.IGNORECASE),
    ),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("PHONE", re.compile(r"(?<!\w)(?:\+\d{1,3}[\s-]?)?[3689]\d{3}[\s-]?\d{4}(?!\w)")),
]


@dataclass
class RedactionResult:
    redacted: str
    entities: list[tuple[str, str]]  # (kind, original)


def redact(text: str) -> RedactionResult:
    entities: list[tuple[str, str]] = []
    redacted = text
    for kind, pattern in _PATTERNS:

        def _sub(m: re.Match[str], kind: str = kind) -> str:
            value = m.group(1) if m.groups() else m.group()
            entities.append((kind, value))
            replaced = m.group().replace(value, f"[{kind}]")
            return replaced

        redacted = pattern.sub(_sub, redacted)
    return RedactionResult(redacted=redacted, entities=entities)


# Prompt-injection screen: strip/flag imperative-to-assistant patterns in user
# input AND in web-index text at retrieval time (§9.2).
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
    """Returns (screened_text, flagged). Matches are neutralised, not obeyed."""
    flagged = bool(_INJECTION_RE.search(text))
    screened = _INJECTION_RE.sub("[removed-instruction]", text)
    return screened, flagged
