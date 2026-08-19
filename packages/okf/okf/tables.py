"""Benefit tables — the single source for every number (§C.3 rule 2).

Numbers never live in prose and are never produced by the language model.
Pages carry transclusion tokens like `{{table:travel_delay.payout_per_block}}`;
the harness resolves them against (product, version, tier) with a deterministic
row fetch, and every rendered figure keeps its `row_id` so the numeric-binding
gate (§F.2) can prove where it came from.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

TOKEN_RE = re.compile(r"\{\{table:([a-z0-9_]+)\.([a-z0-9_]+)\}\}")


@dataclass(frozen=True)
class TableRow:
    product: str
    version: str
    tier: str
    benefit_code: str
    attribute: str
    value: str
    unit: str
    source_ref: str

    @property
    def row_id(self) -> str:
        return f"{self.product}:{self.version}:{self.tier}:{self.benefit_code}.{self.attribute}"

    def rendered(self) -> str:
        """Display form. `unit` carries the currency/suffix so the number
        itself is never assembled by a model."""
        value = self.value
        if value.isdigit() and len(value) > 3:
            value = f"{int(value):,}"
        if not self.unit:
            return value
        if self.unit.endswith(("$", "£", "€")):
            return f"{self.unit}{value}"
        if self.unit == "%":
            return f"{value}%"
        return f"{value} {self.unit}"


class MissingRow(LookupError):
    pass


class BenefitTables:
    def __init__(self, rows: list[TableRow]) -> None:
        self._rows = rows
        self._by_key: dict[tuple[str, str, str, str, str], TableRow] = {
            (r.product, r.version, r.tier, r.benefit_code, r.attribute): r for r in rows
        }

    @classmethod
    def from_dir(cls, directory: Path) -> BenefitTables:
        rows: list[TableRow] = []
        for path in sorted(directory.glob("*.csv")):
            with path.open(newline="") as fh:
                for record in csv.DictReader(fh):
                    rows.append(
                        TableRow(
                            product=record["product"].strip(),
                            version=record["version"].strip(),
                            tier=record["tier"].strip(),
                            benefit_code=record["benefit_code"].strip(),
                            attribute=record["attribute"].strip(),
                            value=record["value"].strip(),
                            unit=record.get("unit", "").strip(),
                            source_ref=record.get("source_ref", "").strip(),
                        )
                    )
        return cls(rows)

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def rows(self) -> list[TableRow]:
        return list(self._rows)

    def fetch(self, product: str, version: str, tier: str, benefit: str, attribute: str) -> TableRow:
        """Deterministic row fetch. Falls back to the `ALL` tier for benefits
        that do not vary by tier; raises rather than guessing."""
        for candidate_tier in (tier, "ALL"):
            row = self._by_key.get((product, version, candidate_tier, benefit, attribute))
            if row is not None:
                return row
        raise MissingRow(f"no row for {product}:{version}:{tier}:{benefit}.{attribute}")

    def tiers_for(self, product: str, version: str) -> list[str]:
        return sorted({r.tier for r in self._rows if r.product == product and r.version == version})

    def benefits_for(self, product: str, version: str) -> list[str]:
        return sorted({r.benefit_code for r in self._rows if r.product == product and r.version == version})


@dataclass
class ResolvedFigure:
    token: str
    row_id: str
    text: str
    benefit_code: str
    attribute: str
    source_ref: str


@dataclass
class Transclusion:
    """Result of resolving `{{table:...}}` tokens in a page body."""

    text: str
    figures: list[ResolvedFigure]
    unresolved: list[str]


def resolve_transclusions(
    body: str, tables: BenefitTables, product: str, version: str, tier: str
) -> Transclusion:
    figures: list[ResolvedFigure] = []
    unresolved: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        benefit, attribute = match.group(1), match.group(2)
        try:
            row = tables.fetch(product, version, tier, benefit, attribute)
        except MissingRow:
            # Honest degradation (§F.1 `unresolved`) — never invent the number.
            unresolved.append(f"{benefit}.{attribute}")
            return "[unavailable]"
        rendered = row.rendered()
        figures.append(
            ResolvedFigure(
                token=match.group(0),
                row_id=row.row_id,
                text=rendered,
                benefit_code=benefit,
                attribute=attribute,
                source_ref=row.source_ref,
            )
        )
        return rendered

    return Transclusion(text=TOKEN_RE.sub(substitute, body), figures=figures, unresolved=unresolved)


def find_tokens(body: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in TOKEN_RE.finditer(body)]
