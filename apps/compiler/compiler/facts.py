"""Fact extraction (§D.1).

Extraction is structured, not free-form: this step emits typed Facts with a
source path and locator, and the *writing* step composes prose only from
approved Facts. That separation is what stops the compiler hallucinating during
compilation — the classic failure of naive wiki-building.

The pattern extractor here is the deterministic stand-in for guided decoding
against the extraction model; the Fact contract is the part that matters and
does not change when the model is wired in.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


class Fact(BaseModel):
    claim: str
    value: str
    unit: str = ""
    source_path: str
    locator: str = ""
    benefit_code: str = ""
    attribute: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @property
    def key(self) -> tuple[str, str]:
        return (self.benefit_code, self.attribute)


# Benefit vocabulary — maps source prose to the canonical benefit codes used by
# the tables. Extending this list is a Loop 4 "retrieval gap" fix.
BENEFIT_VOCAB: dict[str, str] = {
    "travel delay": "travel_delay",
    "delay": "travel_delay",
    "medical expenses": "medical_expenses",
    "medical": "medical_expenses",
    "baggage": "baggage_loss",
    "trip cancellation": "trip_cancellation",
    "cancellation": "trip_cancellation",
    "contents": "contents",
    "excess": "own_damage",
    "no-claim discount": "ncd",
}

MONEY_RE = re.compile(r"S?\$\s?(\d[\d,]*(?:\.\d+)?)")
HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(hours?|days?)\b", re.IGNORECASE)
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


@dataclass
class SourceDoc:
    path: str
    text: str

    @property
    def content_hash(self) -> str:
        """Content-addressed sources: same inputs must give the same page, or
        the pipeline is broken (§D.1)."""
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]


def load_sources(raw_root: Path) -> list[SourceDoc]:
    docs: list[SourceDoc] = []
    for path in sorted(raw_root.rglob("*.md")):
        docs.append(SourceDoc(path=f"raw/{path.relative_to(raw_root)}", text=path.read_text(errors="ignore")))
    return docs


def _benefit_for(line: str) -> str:
    lowered = line.lower()
    for phrase, code in sorted(BENEFIT_VOCAB.items(), key=lambda kv: -len(kv[0])):
        if phrase in lowered:
            return code
    return ""


def extract_facts(doc: SourceDoc) -> list[Fact]:
    facts: list[Fact] = []
    section = ""
    for line in doc.text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip()
            continue
        if not stripped or stripped.startswith("<!--"):
            continue
        benefit = _benefit_for(stripped)
        if not benefit:
            continue
        for match in MONEY_RE.finditer(stripped):
            facts.append(
                Fact(
                    claim=stripped,
                    value=match.group(1).replace(",", ""),
                    unit="S$",
                    source_path=doc.path,
                    locator=section,
                    benefit_code=benefit,
                    attribute="limit",
                    confidence=0.6,
                )
            )
        for match in HOURS_RE.finditer(stripped):
            facts.append(
                Fact(
                    claim=stripped,
                    value=match.group(1),
                    unit=match.group(2).lower().rstrip("s") + "s",
                    source_path=doc.path,
                    locator=section,
                    benefit_code=benefit,
                    attribute="threshold_hours",
                    confidence=0.6,
                )
            )
        for match in PERCENT_RE.finditer(stripped):
            facts.append(
                Fact(
                    claim=stripped,
                    value=match.group(1),
                    unit="%",
                    source_path=doc.path,
                    locator=section,
                    benefit_code=benefit,
                    attribute="max_percentage",
                    confidence=0.5,
                )
            )
    return facts
