"""Bundle lint (§6.1.2). All violations are collected, not fail-fast."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import pairwise

from contracts.okf import Audience, OkfBlock


@dataclass
class LintReport:
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def add(self, message: str) -> None:
        self.violations.append(message)


def lint_bundle(
    blocks: list[OkfBlock],
    previous_ids: set[str] | None = None,
    public_index_links: set[str] | None = None,
) -> LintReport:
    report = LintReport()
    ids: dict[tuple[str, str], list[OkfBlock]] = defaultdict(list)
    all_ids: set[str] = set()

    for block in blocks:
        fm = block.frontmatter
        ids[(fm.id, fm.language.value)].append(block)
        all_ids.add(fm.id)

    # unique (id, language) — languages share the id, exact duplicates do not
    for (block_id, language), group in ids.items():
        if len(group) > 1:
            windows = sorted((b.frontmatter.effective_from, b.frontmatter.effective_to) for b in group)
            for (start_a, end_a), (start_b, _end_b) in pairwise(windows):
                if end_a is None or start_b < end_a:
                    report.add(
                        f"{block_id} [{language}]: overlapping effective windows "
                        f"({start_a}..{end_a} vs {start_b}..)"
                    )

    # related links resolve
    for block in blocks:
        for ref in block.frontmatter.related:
            if ref not in all_ids:
                report.add(f"{block.frontmatter.id}: related link {ref!r} does not resolve")

    # internal blocks never linked from public navigation
    if public_index_links:
        internal_ids = {b.frontmatter.id for b in blocks if b.frontmatter.audience == Audience.internal}
        for linked in public_index_links & internal_ids:
            report.add(f"public index.md links internal block {linked!r}")

    # ids immutable versus previous bundle manifest
    if previous_ids:
        missing = previous_ids - all_ids
        for block_id in sorted(missing):
            report.add(f"block id {block_id!r} disappeared from bundle (ids are immutable; retire instead)")

    return report
