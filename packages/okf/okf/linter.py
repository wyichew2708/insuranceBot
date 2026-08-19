"""Bundle linter — the mechanical enforcement of §C.3 and §I.

The linter is what stops a wiki from rotting into confidently-wrong prose:
an unreferenced claim, a number typed into a sentence, or a bare brand name
on a canonical product page all fail the build rather than reaching a
customer. Violations block `approved`; warnings are advisory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from okf.bundle import Bundle
from okf.page import Page, PageType, Status
from okf.tables import TOKEN_RE

SOURCE_REF_RE = re.compile(r"\[src:([^\]#]+)(?:#([^\]]+))?\]")
CHANNEL_VARIANT_RE = re.compile(r"<!--\s*okf:channel-variant\s*-->.*?<!--\s*/okf:channel-variant\s*-->", re.S)
ALLOW_NUMBER = "<!-- okf:allow-number -->"
BRAND_RE = re.compile(r"\b(Tiq|Etiqa)\b")
LEGAL_NAME = "Etiqa Insurance Pte. Ltd."

# Currency amounts, percentages, or any standalone multi-digit quantity.
NUMBER_IN_PROSE_RE = re.compile(r"(?:S?\$\s?\d[\d,]*(?:\.\d+)?)|(?:\b\d+(?:\.\d+)?\s?%)|(?:\b\d{2,}\b)")


class Severity(str, Enum):
    error = "error"
    warning = "warning"


@dataclass
class Violation:
    page_id: str
    rule: str
    message: str
    severity: Severity = Severity.error
    line: int | None = None


@dataclass
class LintReport:
    violations: list[Violation] = field(default_factory=list)

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == Severity.error]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == Severity.warning]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, *args: object, **kwargs: object) -> None:
        self.violations.append(Violation(*args, **kwargs))  # type: ignore[arg-type]


def _strip_variant_blocks(body: str) -> str:
    return CHANNEL_VARIANT_RE.sub("", body)


def _prose_paragraphs(body: str) -> list[tuple[int, str]]:
    """Blank-line separated blocks that assert something. A claim is a
    paragraph, not a line — references and markers routinely wrap."""
    out: list[tuple[int, str]] = []
    block: list[str] = []
    start = 0
    in_fence = False

    def flush() -> None:
        if not block:
            return
        text = "\n".join(block)
        stripped = text.lstrip()
        if not stripped.startswith(("#", "|", "<!--", ">")) and not _all_bare_links(block):
            out.append((start, text))

    for number, raw in enumerate(body.splitlines(), start=1):
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line:
            flush()
            block = []
            continue
        if not block:
            start = number
        block.append(line)
    flush()
    return out


def _is_pointer(paragraph: str) -> bool:
    """A pure cross-reference: it links to another page and asserts nothing of
    substance itself, so the reference lives on the target page (which is
    linted in its own right). Deliberately narrow — anything with real content
    around the link still needs its own source ref."""
    if "](" not in paragraph:
        return False
    residual = re.sub(r"\[([^\]]*)\]\([^)]*\)", " ", paragraph)
    return len(residual.split()) <= 12


def _all_bare_links(lines: list[str]) -> bool:
    return all(re.fullmatch(r"[-*]?\s*\[[^\]]+\]\([^)]+\)\.?", line) for line in lines)


def lint_page(page: Page, bundle: Bundle) -> list[Violation]:
    violations: list[Violation] = []
    fm = page.frontmatter
    body_no_variants = _strip_variant_blocks(page.body)

    def error(rule: str, message: str, line: int | None = None) -> None:
        violations.append(Violation(page.id, rule, message, Severity.error, line))

    def warn(rule: str, message: str, line: int | None = None) -> None:
        violations.append(Violation(page.id, rule, message, Severity.warning, line))

    # Navigation pages assert nothing, so the claim rules do not apply to them.
    asserts_facts = fm.type != PageType.index_page

    # C.3 rule 1 — every factual claim carries an inline source ref.
    for number, line in _prose_paragraphs(body_no_variants) if asserts_facts else []:
        if TOKEN_RE.search(line) and not SOURCE_REF_RE.search(line):
            error("source-ref", "transcluded figure without a [src:...] reference", number)
        elif _is_pointer(line):
            continue
        elif not SOURCE_REF_RE.search(line) and len(line.split()) >= 6:
            error("source-ref", f"unreferenced claim: {line[:70]!r}", number)

    # C.3 rule 2 — numbers never live in prose.
    for number, line in _prose_paragraphs(body_no_variants) if asserts_facts else []:
        if ALLOW_NUMBER in line:
            continue
        stripped = TOKEN_RE.sub("", SOURCE_REF_RE.sub("", line))
        stripped = re.sub(r"`[^`]*`", "", stripped)
        stripped = re.sub(r"\]\([^)]*\)", "", stripped)
        match = NUMBER_IN_PROSE_RE.search(stripped)
        if match:
            error(
                "number-in-prose",
                f"number {match.group()!r} must come from benefit-tables, not prose",
                number,
            )

    # C.3 rule 3 — links are the graph, and must resolve.
    for ref in fm.links.all_refs():
        if ref not in bundle.pages:
            error("broken-link", f"link target {ref!r} does not resolve")

    # C.3 rule 4 — one page, one concept.
    if re.search(r"^##\s+(Also|Separately|Other)\b", page.body, re.M | re.I):
        warn("one-concept", "section suggests this page covers a second concept; split it")

    # I — merge over-flattening: brand names only inside channel-variant blocks.
    if fm.type == PageType.product:
        scrubbed = body_no_variants.replace(LEGAL_NAME, "")
        for match in BRAND_RE.finditer(scrubbed):
            error(
                "bare-brand",
                f"bare brand {match.group()!r} outside a channel-variant block on a product page",
            )

    # Transclusions must resolve against the tables for the page's version.
    if fm.type == PageType.product and fm.version_in_force:
        product_key = bundle.product_key(page)
        for benefit, attribute in {(b, a) for b, a in _tokens(page.body)}:
            if not _table_has(bundle, product_key, fm.version_in_force, benefit, attribute):
                error(
                    "unbound-token",
                    f"{{{{table:{benefit}.{attribute}}}}} has no row for {product_key}:{fm.version_in_force}",
                )

    # Approval hygiene — approved pages carry provenance and a review date.
    if fm.status == Status.approved:
        if not fm.reviewed_by:
            error("approval", "approved page has no reviewed_by sign-off")
        if fm.review_due is None:
            error("approval", "approved page has no review_due date")
        if not fm.authority and fm.type in {PageType.product, PageType.concept, PageType.journey}:
            error("approval", "approved page declares no authority order")

    return violations


def _tokens(body: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in TOKEN_RE.finditer(body)]


def _table_has(bundle: Bundle, product: str, version: str, benefit: str, attribute: str) -> bool:
    for tier in [*bundle.tables.tiers_for(product, version), "ALL"]:
        try:
            bundle.tables.fetch(product, version, tier, benefit, attribute)
            return True
        except LookupError:
            continue
    return False


def lint_bundle(bundle: Bundle) -> LintReport:
    report = LintReport()
    for message in bundle.load_errors:
        report.violations.append(Violation("<bundle>", "load", message))

    for page in sorted(bundle.pages.values(), key=lambda p: p.id):
        report.violations.extend(lint_page(page, bundle))

    # The index is the agent's entry point (§C.1) — it must exist and cover
    # every approved product page, or wiki-first retrieval starts blind.
    index = bundle.get("index")
    if index is None:
        report.violations.append(Violation("<bundle>", "index", "wiki/index.md is missing"))
    else:
        for page in bundle.by_type(PageType.product):
            if page.frontmatter.status == Status.approved and page.id not in index.body:
                report.violations.append(
                    Violation("<bundle>", "index", f"approved page {page.id!r} is not listed in index.md")
                )
    return report
