"""Conflict detection — the heart of the merge (§D.2).

The two sites will disagree, and marketing copy drifts from policy wordings
continuously. Authority order is declared in the manifest and enforced
mechanically: the compiler writes from the higher authority, files the
discrepancy, and routes it to the content owner **as a website defect ticket**,
not as a wiki problem. That turns the assistant into a continuous consistency
auditor of both websites.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import yaml

from compiler.facts import Fact, SourceDoc, extract_facts, load_sources
from okf import BenefitTables

DEFAULT_AUTHORITY = [
    "raw/wordings",
    "raw/product-summaries",
    "raw/benefit-tables",
    "raw/web/etiqa-sg",
    "raw/web/tiq-sg",
    "raw/blog",
]


def authority_rank(source_path: str, order: list[str]) -> int:
    for rank, prefix in enumerate(order):
        if source_path.startswith(prefix):
            return rank
    return len(order)  # unknown sources are least authoritative


@dataclass
class Conflict:
    benefit_code: str
    attribute: str
    winner: Fact
    loser: Fact
    product: str = ""

    @property
    def slug(self) -> str:
        loser = self.loser.source_path.replace("/", "-").replace(".md", "")
        return f"{self.benefit_code}-{self.attribute}-{loser}"

    def as_markdown(self, today: dt.date) -> str:
        return f"""# Conflict — {self.benefit_code}.{self.attribute}

- **Detected:** {today.isoformat()}
- **Status:** open
- **Route to:** content owner of `{self.loser.source_path}` (website defect, not a wiki defect)

| Source | Authority | Value |
|---|---|---|
| `{self.winner.source_path}` | higher | {self.winner.value} {self.winner.unit} |
| `{self.loser.source_path}` | lower | {self.loser.value} {self.loser.unit} |

## Winner's statement

> {self.winner.claim}

## Contradicting statement

> {self.loser.claim}

The wiki page was compiled from the higher-authority source. The lower-authority
copy is quietly wrong today and should be corrected at source.
"""


def load_authority_order(bundle_root: Path) -> list[str]:
    manifest = bundle_root / "okf.yaml"
    if not manifest.exists():
        return list(DEFAULT_AUTHORITY)
    data = yaml.safe_load(manifest.read_text()) or {}
    order = data.get("authority_order")
    return [str(item) for item in order] if order else list(DEFAULT_AUTHORITY)


def table_facts(tables: BenefitTables) -> dict[tuple[str, str], list[Fact]]:
    """Benefit tables are the numeric authority. A benefit may legitimately
    carry several values (one per tier), so the comparison is set membership:
    a source is in conflict when it states a number that appears nowhere in
    the table for that benefit."""
    facts: dict[tuple[str, str], list[Fact]] = {}
    for row in tables.rows:
        fact = Fact(
            claim=f"{row.product} {row.benefit_code}.{row.attribute} = {row.rendered()} ({row.tier})",
            value=row.value,
            unit=row.unit,
            source_path="raw/benefit-tables",
            locator=row.row_id,
            benefit_code=row.benefit_code,
            attribute=row.attribute,
            confidence=1.0,
        )
        facts.setdefault(fact.key, []).append(fact)
    return facts


def detect_conflicts(
    docs: list[SourceDoc], order: list[str], tables: BenefitTables | None = None
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    authoritative = table_facts(tables) if tables is not None else {}

    by_key: dict[tuple[str, str], list[Fact]] = {}
    for doc in docs:
        for fact in extract_facts(doc):
            by_key.setdefault(fact.key, []).append(fact)

    for key, facts in sorted(by_key.items()):
        benefit, attribute = key
        table_rows = authoritative.get(key, [])

        if table_rows:
            allowed = {f.value for f in table_rows}
            for fact in facts:
                if fact.source_path.startswith("raw/benefit-tables") or fact.value in allowed:
                    continue
                if authority_rank(fact.source_path, order) <= authority_rank("raw/benefit-tables", order):
                    continue  # a wording outranks the table; that is a table defect, handled below
                conflicts.append(
                    Conflict(benefit_code=benefit, attribute=attribute, winner=table_rows[0], loser=fact)
                )
            continue

        ranked = sorted(facts, key=lambda f: authority_rank(f.source_path, order))
        winner = ranked[0]
        for loser in ranked[1:]:
            if loser.value == winner.value:
                continue
            if authority_rank(loser.source_path, order) == authority_rank(winner.source_path, order):
                continue  # same tier disagreeing is a content-ops question, not authority
            conflicts.append(Conflict(benefit_code=benefit, attribute=attribute, winner=winner, loser=loser))
    return conflicts


def write_conflicts(bundle_root: Path, conflicts: list[Conflict], today: dt.date | None = None) -> list[Path]:
    today = today or dt.date.today()
    out_dir = bundle_root / "conflicts"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for conflict in conflicts:
        path = out_dir / f"{conflict.slug}.md"
        path.write_text(conflict.as_markdown(today))
        written.append(path)
    return written


def scan(bundle_root: Path) -> list[Conflict]:
    docs = load_sources(bundle_root / "raw")
    tables = BenefitTables.from_dir(bundle_root / "raw" / "benefit-tables")
    return detect_conflicts(docs, load_authority_order(bundle_root), tables)
