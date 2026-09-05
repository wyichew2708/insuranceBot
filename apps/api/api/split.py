"""Two questions in one breath are two turns.

"What does Tiq 3 Plus Critical Illness cover and how much does it cost?" was
routed once, as a price question, refused as one, and the coverage half — the
half the corpus answers well — was never asked. The product owner's rule is
the obvious one: route each question to its own handler and put the answers
together.

The split is conservative. It happens only at a conjunction followed by a
word that starts a question ("and how", "and what", "and can"), at a
semicolon, or at a question mark with more question after it; and only when
the halves read as different intents, or each carries its own interrogative.
"Does it cover flood and fire?" has an "and" and stays whole. "Terms and
conditions" stays whole. The parts are answered in order, each with the
earlier parts as history, so "how much does it cost" after "what does X cover"
is about X.
"""

from __future__ import annotations

import re

from harness.intent import Intent, classify, smalltalk_kind

_LEAD = (
    r"(?:how|what|when|where|which|who|why|can|could|do|does|did"
    r"|is|are|will|would|should|may|am|was|were)"
)
_SPLIT_RE = re.compile(
    rf"\s*[,;]?\s*\b(?:and|also|plus|as well as|then)\b\s+(?={_LEAD}\b)"
    r"|\s*;\s*(?=\S)"
    r"|(?<=\?)\s+(?=\S)",
    re.I,
)
_LEAD_RE = re.compile(rf"^{_LEAD}\b", re.I)
MAX_PARTS = 3


def split_questions(question: str) -> list[str]:
    """The questions in a turn, in order — the turn itself where it is one."""
    text = " ".join((question or "").split())
    if not text or smalltalk_kind(text):
        return [question]
    parts = [p.strip(" ,;") for p in _SPLIT_RE.split(text)]
    parts = [p for p in parts if p and len(p.split()) >= 2]
    if len(parts) < 2:
        return [question]
    intents = [classify(p) for p in parts]
    distinct = len({i for i in intents if i is not Intent.unknown}) >= 2
    each_asks = all(_LEAD_RE.match(p) for p in parts)
    if not (distinct or each_asks):
        return [question]
    return parts[:MAX_PARTS]
