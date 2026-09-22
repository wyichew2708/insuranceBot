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
from collections.abc import Sequence
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
            with path.open(newline="", encoding="utf-8") as fh:
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

    def across_tiers(
        self,
        product: str,
        version: str,
        benefit: str,
        attribute: str,
        order: Sequence[str] = (),
    ) -> list[TableRow]:
        """Every plan's row for one benefit, in the table's own plan order.

        A limit that varies by plan has no single answer, and the honest reply
        is not "it depends on your plan tier" — the figures are published, one
        per plan, and a customer comparing plans is asking for exactly that
        list. This returns it; `fetch` still answers when the plan is known.
        """
        rows = [
            r
            for r in self._rows
            if r.product == product
            and r.version == version
            and r.benefit_code == benefit
            and r.attribute == attribute
            and r.tier != "ALL"
        ]
        # The plans read in the order the product page lists them — Entry,
        # Savvy, Luxury, cheapest first — not alphabetically, which puts
        # Luxury in the middle and makes the list look unordered.
        ranking = list(order) or self.tiers_for(product, version)
        return sorted(
            rows, key=lambda r: (ranking.index(r.tier) if r.tier in ranking else len(ranking), r.tier)
        )

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


def plan_label(tier: str) -> str:
    """`enhanced-gold` as a customer reads it."""
    return " ".join(word.capitalize() for word in tier.split("-"))


def resolve_transclusions(
    body: str,
    tables: BenefitTables,
    product: str,
    version: str,
    tier: str,
    *,
    spell_out_tiers: bool = False,
    tier_order: Sequence[str] = (),
) -> Transclusion:
    figures: list[ResolvedFigure] = []
    unresolved: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        benefit, attribute = match.group(1), match.group(2)
        if spell_out_tiers and tier in ("", "UNKNOWN"):
            # No plan known and none named. Rather than "[unavailable]", say
            # what each plan pays: every one of these is a published figure
            # bound to its own row, so the gates see the same evidence they
            # would for a single-plan answer.
            rows = tables.across_tiers(product, version, benefit, attribute, tier_order)
            if len(rows) > 1:
                for row in rows:
                    figures.append(
                        ResolvedFigure(
                            token=match.group(0),
                            row_id=row.row_id,
                            text=row.rendered(),
                            benefit_code=benefit,
                            attribute=attribute,
                            source_ref=row.source_ref,
                        )
                    )
                # Separated by semicolons, not commas: a comma straight after
                # "$5,000" reads as a thousands separator to every regex that
                # scans for money, so the figure came out as "$5,000," and
                # matched nothing in the table it had just been read from —
                # which the groundedness gate then refused, correctly, for a
                # figure it could not find.
                return "; ".join(f"{plan_label(r.tier)} {r.rendered()}" for r in rows)
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


#: Words in a benefit code that carry no meaning on their own. A code is
#: matched on what distinguishes it, so "loss of deposit" does not match
#: every benefit with "of" in its name.
_CODE_FILLER = frozenset({"and", "or", "of", "the", "a", "an", "in", "for", "to", "s", "ren"})


def _code_words(code: str) -> list[str]:
    return [w for w in code.split("_") if w and w not in _CODE_FILLER]


def benefit_codes_in(question: str, codes: Sequence[str], minimum: int = 2) -> set[str]:
    """The product's own benefit codes that the question names.

    The vocabulary maps a customer's words to a *concept* — "suitcase" becomes
    `baggage_loss`. The benefit table names rows something else entirely:
    `trip_cancellation_and_loss_of_deposit`, `medical_expenses_incurred_in_singapore`.
    A question matched only against concepts therefore reached no row, and the
    customer was told the pages do not address a benefit that is published in
    the table two lines down.

    This matches the other way round: against the codes this product actually
    has. A code counts as named when the question contains `minimum` of its
    distinguishing words — "trip cancellation" reaches
    `trip_cancellation_and_loss_of_deposit`, and "cancel my policy" reaches
    nothing, which is the distinction that matters for cancellation.
    """
    text = re.sub(r"[^a-z0-9]+", " ", (question or "").lower())
    words = set(text.split())
    found = set()
    for code in codes:
        parts = _code_words(code)
        if not parts:
            continue
        hits = sum(1 for w in parts if w in words)
        # A one-word code ("child") needs its whole word; a longer one needs
        # enough of it that a single common word cannot carry the match.
        if hits >= min(minimum, len(parts)) and (len(parts) > 1 or parts[0] in words):
            found.add(code)
    return found
