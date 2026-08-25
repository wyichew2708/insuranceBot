"""Loop 2 — Compile (§G): crawl snapshots become OKF wiki pages.

This is where "knowledge is compiled once, not re-discovered per query" stops
being a slogan. The crawl leaves dated snapshots under `raw/web/`; this module
turns them into the canonical wiki:

* **one page per product, not per brand** (§B.1). Two hosts selling the same
  plan collapse into a single `product/<line>/<slug>` page carrying both
  channel bindings; only the deep link and hotline differ.
* **numbers leave prose and become table rows** (§C.3). Every benefit-table
  cell on a crawled page is written to `raw/benefit-tables/<slug>.csv` with a
  `source_ref`, and the page keeps only a `{{table:...}}` transclusion.
* **sections become graph edges** (§E.1). "What is not covered" is lifted onto
  its own exclusions page and linked, so a coverage question can be answered by
  traversal instead of hoping a chunk surfaced.
* **disagreement is recorded, not silently resolved** (§D.2). Where two hosts
  publish different values for the same benefit, the higher-authority host
  wins and the loser is reported as a website defect.

Compiled pages are written `draft`. Promotion to `approved` requires an
explicit sign-off, because an approved page is one a human has read.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from okf.channels import ALL_CHANNELS, channel_for_host
from okf.page import (
    ChannelBinding,
    Confidence,
    Frontmatter,
    Lifecycle,
    Links,
    Page,
    PageType,
    Status,
    render_page,
)

from compiler.snapshots import Section, Snapshot, load_snapshots, slugify

LEGAL_NAME = "Etiqa Insurance Pte. Ltd."
UEN = "201331905K"

# Names a crawled page may use for the shopfront. They are the same insurer;
# on a canonical product page they all normalise to the legal name.
SHOPFRONT_NAMES = ("Tiq", "Etiqa")

# Host → the distribution channel it is a surface of. The mapping lives in the
# okf channel registry, which is contract: the harness's Channel enum binds
# sessions to those exact refs. Both the Etiqa and Tiq hosts are surfaces of
# the *same* direct channel — they are front doors, not brands.

# §B.3 product roots. First match wins, so the more specific rules lead.
LOB_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"car|motor|vehicle|motorcycle|van|fleet"), "motor"),
    (re.compile(r"business|sme|commercial|work-injury|employer|employee"), "business"),
    (re.compile(r"invest|ilp|unit-trust|fund"), "investments"),
    (re.compile(r"saver|saving|endowment|retirement|annuity|legacy"), "savings-retirement"),
    (re.compile(r"life|cancer|critical|terminal|protection"), "protection"),
    (re.compile(r"medical|health|hospital|shield|dental|clinic"), "health-medical"),
    (re.compile(r"premier|prestige"), "premier"),
]

ADVICE_RE = re.compile(
    r"licensed financial adviser|financial advice|advised product|this plan is advised", re.I
)
ALIAS_RE = re.compile(r"also known as ([^.]+)\.", re.I)
PHONE_RE = re.compile(r"\+65[\s-]?\d{4}[\s-]?\d{4}")
CURRENCY_RE = re.compile(r"^(S?\$)\s?([\d,]+(?:\.\d+)?)$")
PERCENT_RE = re.compile(r"^([\d.]+)\s?%$")
QUANTITY_RE = re.compile(r"^([\d,.]+)\s?([A-Za-z][A-Za-z ]*)$")
DATE_RE = re.compile(
    r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
    re.I,
)
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}

# Terms worth a page of their own: the graph target for "what does X mean?".
# A concept page is only written if the crawl actually defines the term, so the
# definition is quotable rather than assumed.
CONCEPT_TERMS: list[tuple[str, str, re.Pattern[str], list[str]]] = [
    (
        "concept/free-look",
        "Free-look period",
        re.compile(r"free-look period", re.I),
        ["free look", "cooling off", "cancel a new policy"],
    ),
    (
        "concept/policy-schedule",
        "Policy schedule",
        re.compile(r"policy schedule", re.I),
        ["schedule", "policy documents"],
    ),
    (
        "concept/commencement-date",
        "Commencement date",
        re.compile(r"commencement date", re.I),
        ["cover start date", "when cover starts"],
    ),
    ("concept/nomination", "Nomination", re.compile(r"nominee|nomination", re.I), ["nominee", "beneficiary"]),
    (
        "concept/pro-rata-refund",
        "Pro-rata refund",
        re.compile(r"pro-rata refund", re.I),
        ["partial refund", "refund on cancellation"],
    ),
    ("concept/excess", "Excess", re.compile(r"\bexcess\b", re.I), ["deductible", "own damage excess"]),
]


def channel_for(host: str) -> tuple[str, str, str]:
    """(display name, channel id, purchase route) for a crawled host."""
    spec = channel_for_host(host)
    if spec is not None:
        return spec.name, spec.ref.value, spec.purchase
    label = host.split(".")[1] if host.count(".") > 1 else host
    return label.title(), f"channel/{slugify(label)}", "direct_online"


def line_of_business(slug: str, title: str) -> str:
    haystack = f"{slug} {title}".lower()
    for pattern, root in LOB_RULES:
        if pattern.search(haystack):
            return root
    return "general"


PHRASE = {
    "limit": "The {label} limit for the plan tier held is",
    "rate": "The {label} is",
    "period": "The {label} is",
    "value": "The {label} is",
}


def benefit_phrase(code: str, attribute: str) -> str:
    label = code.replace("_", " ")
    return PHRASE.get(attribute, PHRASE["value"]).format(label=label)


def benefit_code(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def parse_cell(cell: str) -> tuple[str, str, str] | None:
    """A table cell becomes (value, unit, attribute). Returns None when the
    cell is prose — a number the compiler cannot type is a number it refuses
    to publish."""
    cell = cell.strip()
    if match := CURRENCY_RE.match(cell):
        return match.group(2).replace(",", ""), match.group(1), "limit"
    if match := PERCENT_RE.match(cell):
        return match.group(1), "%", "rate"
    if match := QUANTITY_RE.match(cell):
        return match.group(1).replace(",", ""), match.group(2).strip().lower(), "period"
    if re.fullmatch(r"[\d,]+", cell):
        return cell.replace(",", ""), "", "value"
    return None


@dataclass
class BenefitRow:
    product: str
    version: str
    tier: str
    benefit_code: str
    attribute: str
    value: str
    unit: str
    source_ref: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.tier, self.benefit_code, self.attribute)


@dataclass
class SourceConflict:
    product: str
    coordinate: str
    kept: str
    kept_source: str
    dropped: str
    dropped_source: str


@dataclass
class ProductGroup:
    slug: str
    title: str = ""
    product: dict[str, Snapshot] = field(default_factory=dict)  # host → snapshot
    claims: dict[str, Snapshot] = field(default_factory=dict)
    faq: dict[str, Snapshot] = field(default_factory=dict)
    tiers: list[str] = field(default_factory=list)  # column order, set while reading the table

    @property
    def hosts(self) -> list[str]:
        return list(self.product)

    @property
    def text(self) -> str:
        pages = [*self.product.values(), *self.claims.values(), *self.faq.values()]
        return " ".join(s.text for s in pages)


@dataclass
class CompileConfig:
    source_root: Path
    dest_root: Path
    version: str = ""
    today: dt.date = field(default_factory=dt.date.today)
    sign_off: list[str] = field(default_factory=list)
    review_months: int = 3
    authority_hosts: list[str] = field(default_factory=list)


@dataclass
class CompileReport:
    pages: list[str] = field(default_factory=list)
    tables: dict[str, int] = field(default_factory=dict)
    conflicts: list[SourceConflict] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    for sentence in re.split(r"(?<=\.)\s+", text):
        if ALIAS_RE.match(sentence) or not sentence:
            continue
        return sentence.rstrip()
    return ""


def _normalise_brands(text: str) -> str:
    """A canonical product page names the underwriter, not the shopfront
    (§B.1). Historic shopfront names are folded into the legal name so no page
    reads as though Tiq and Etiqa were two different insurers."""
    for shopfront in SHOPFRONT_NAMES:
        text = re.sub(rf"\b{shopfront}\b(?!\s+Insurance Pte)", LEGAL_NAME, text)
    # Collapse "Etiqa Insurance Pte. Ltd. Insurance Pte. Ltd." if the source
    # already spelled part of the legal name.
    return re.sub(rf"(?:{re.escape(LEGAL_NAME)}\s+)+", f"{LEGAL_NAME} ", text)


def _sentence_with(term: re.Pattern[str], snapshots: list[Snapshot]) -> tuple[str, str] | None:
    for snapshot in snapshots:
        for section in snapshot.sections:
            for sentence in re.split(r"(?<=\.)\s+", " ".join(section.text.split())):
                if term.search(sentence) and len(sentence.split()) >= 6:
                    return sentence.strip(), snapshot.ref(section.anchor)
    return None


SECTION_ROOTS = {
    "personal",
    "business",
    "claims",
    "faqs",
    "faq",
    "policy-services",
    "promotions",
    "products",
    "product",
    "plans",
    "insurance",
    "blog",
    "",
    "servicing",
}
VERSION_RE = re.compile(r"/([a-z0-9-]+?)-(?:summary-)?(\d{4}(?:\.\d+)?)\.pdf$", re.I)


def versions_from_documents(source_root: Path) -> dict[str, str]:
    """The wording PDF names the in-force version. It is recorded rather than
    parsed (§D.1: documents are source material, not web copy), so the file
    name is the only version signal a crawl alone can give."""
    manifest = source_root / "raw" / "web" / "crawl-manifest.json"
    if not manifest.is_file():
        return {}
    import json

    data = json.loads(manifest.read_text())
    versions: dict[str, str] = {}
    for document in data.get("documents", []):
        match = VERSION_RE.search(str(document.get("url", "")))
        if match:
            versions.setdefault(match.group(1), match.group(2))
    return versions


def group_products(snapshots: list[Snapshot], report: CompileReport) -> dict[str, ProductGroup]:
    groups: dict[str, ProductGroup] = {}
    for snapshot in snapshots:
        if snapshot.page_type != "product":
            continue
        if snapshot.slug in SECTION_ROOTS:
            report.skip("section index, not a product")
            continue
        group = groups.setdefault(snapshot.slug, ProductGroup(snapshot.slug))
        group.product[snapshot.host] = snapshot
        # The shortest title across hosts is the least brand-decorated one.
        if not group.title or len(snapshot.title) < len(group.title):
            group.title = snapshot.title

    for snapshot in snapshots:
        attached = groups.get(snapshot.slug)
        if attached is None or snapshot.slug in SECTION_ROOTS:
            continue
        if snapshot.page_type == "claims":
            attached.claims[snapshot.host] = snapshot
        elif snapshot.page_type == "faq":
            attached.faq[snapshot.host] = snapshot
    return groups


def rank_hosts(hosts: list[str], order: list[str]) -> list[str]:
    """Authority order (§D.2). Anything unranked sorts last, alphabetically,
    so the result is stable rather than dependent on crawl order."""

    def key(host: str) -> tuple[int, str]:
        return (order.index(host) if host in order else len(order), host)

    return sorted(hosts, key=key)


def benefit_rows(
    group: ProductGroup, version: str, hosts: list[str], report: CompileReport
) -> list[BenefitRow]:
    """Every table cell on every host, reconciled by authority."""
    kept: dict[tuple[str, str, str], BenefitRow] = {}
    for host in hosts:
        snapshot = group.product.get(host)
        if snapshot is None or not snapshot.tables:
            continue
        table = max(snapshot.tables, key=lambda t: len(t.rows))
        if len(table.header) < 2:
            report.skip("table without a value column")
            continue
        tiers = ["ALL"] if len(table.header) == 2 else [slugify(h) for h in table.header[1:]]
        if not group.tiers and tiers != ["ALL"]:
            group.tiers = tiers
        for cells in table.rows:
            if len(cells) != len(table.header):
                report.skip("ragged table row")
                continue
            label = cells[0]
            code = benefit_code(label)
            if not code:
                continue
            for tier, cell in zip(tiers, cells[1:], strict=False):
                parsed = parse_cell(cell)
                if parsed is None:
                    report.skip("uninterpretable table cell")
                    continue
                value, unit, attribute = parsed
                row = BenefitRow(
                    product=group.slug,
                    version=version,
                    tier=tier,
                    benefit_code=code,
                    attribute=attribute,
                    value=value,
                    unit=unit,
                    source_ref=snapshot.ref("what-is-covered"),
                )
                existing = kept.get(row.key)
                if existing is None:
                    kept[row.key] = row
                elif existing.value != row.value or existing.unit != row.unit:
                    # §D.2 — the website below the winner is the one at fault.
                    report.conflicts.append(
                        SourceConflict(
                            product=group.slug,
                            coordinate=f"{tier}:{code}.{attribute}",
                            kept=f"{existing.unit}{existing.value}",
                            kept_source=existing.source_ref,
                            dropped=f"{unit}{value}",
                            dropped_source=row.source_ref,
                        )
                    )
    return sorted(kept.values(), key=lambda r: (r.benefit_code, r.attribute, r.tier))


def write_benefit_table(dest_root: Path, slug: str, rows: list[BenefitRow]) -> Path:
    import csv

    directory = dest_root / "raw" / "benefit-tables"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["product", "version", "tier", "benefit_code", "attribute", "value", "unit", "source_ref"]
        )
        for row in rows:
            writer.writerow(
                [
                    row.product,
                    row.version,
                    row.tier,
                    row.benefit_code,
                    row.attribute,
                    row.value,
                    row.unit,
                    row.source_ref,
                ]
            )
    return path


NUMBER_IN_PROSE_RE = re.compile(r"(?:S?\$\s?\d[\d,]*(?:\.\d+)?)|(?:\b\d+(?:\.\d+)?\s?%)|(?:\b\d{2,}\b)")
ALLOW_NUMBER = "<!-- okf:allow-number -->"


def _grounded(text: str, ref: str, report: CompileReport, allow_number: bool = False) -> str | None:
    """One paragraph, one reference (§C.3 rule 1). A sentence carrying a number
    the compiler could not bind to a table row is dropped rather than
    published — that is the whole point of rule 2."""
    text = " ".join(text.split()).strip()
    if len(text.split()) < 4:
        return None
    if NUMBER_IN_PROSE_RE.search(text) and not allow_number:
        report.skip("prose number with no table row — dropped")
        return None
    suffix = f" {ALLOW_NUMBER}" if allow_number and NUMBER_IN_PROSE_RE.search(text) else ""
    return f"{text.rstrip('.')} [src:{ref}].{suffix}"


def _bullets(section: Section) -> list[str]:
    items: list[str] = []
    for line in section.text.splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ")):
            items.append(line[2:].strip())
    return items


def _section_body(snapshot: Snapshot, section: Section, report: CompileReport) -> list[str]:
    out: list[str] = []
    ref = snapshot.ref(section.anchor)
    bullets = _bullets(section)
    if bullets:
        for item in bullets:
            grounded = _grounded(item, ref, report)
            if grounded:
                out.append(grounded)
        return out
    for paragraph in section.paragraphs:
        grounded = _grounded(paragraph, ref, report)
        if grounded:
            out.append(grounded)
    return out


def _page(fm: Frontmatter, body: list[str]) -> Page:
    return Page(frontmatter=fm, body="\n\n".join(b for b in body if b).strip() + "\n")


def _write(config: CompileConfig, page: Page, report: CompileReport) -> None:
    path = config.dest_root / "wiki" / f"{page.id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_page(page))
    report.pages.append(page.id)


def _common(
    config: CompileConfig,
    page_id: str,
    title: str,
    page_type: PageType,
    authority: list[str],
    **extra: object,
) -> Frontmatter:
    status = Status.approved if config.sign_off else Status.draft
    review_due = config.today + dt.timedelta(days=30 * config.review_months)
    return Frontmatter(
        id=page_id,
        title=title,
        type=page_type,
        status=status,
        jurisdiction="SG",
        authority=authority,
        compiled_from_commit=config.version or "working-tree",
        compiled_at=dt.datetime.combine(config.today, dt.time(0, 0)),
        reviewed_by=list(config.sign_off),
        review_due=review_due,
        **extra,  # type: ignore[arg-type]
    )


def emit_product(
    config: CompileConfig,
    group: ProductGroup,
    version: str,
    hosts: list[str],
    rows: list[BenefitRow],
    concepts: list[str],
    report: CompileReport,
) -> str:
    lob = line_of_business(group.slug, group.title)
    page_id = f"product/{lob}/{group.slug}"
    ordered = [group.product[h] for h in hosts if h in group.product]
    primary = ordered[0]
    authority = [s.ref() for s in ordered]

    aliases: list[str] = []
    for snapshot in ordered:
        match = ALIAS_RE.search(snapshot.intro)
        if match:
            aliases += [a.strip() for a in match.group(1).split(",") if a.strip()]
    short = re.sub(r"\s+insurance$", "", group.title, flags=re.I)
    if short.lower() != group.title.lower():
        aliases.append(short.lower())
    aliases = sorted({a for a in aliases if a}, key=str.lower)

    regulated = any(ADVICE_RE.search(s.text) for s in ordered)
    # One binding per *distribution channel*, not per host. Several hosts can
    # be surfaces of the same route (etiqa.com.sg and tiq.com.sg are both
    # direct), and the customer must never be asked to pick between them.
    channels: list[ChannelBinding] = []
    by_channel: dict[str, list[Snapshot]] = {}
    for snapshot in ordered:
        _, channel_id, _ = channel_for(snapshot.host)
        by_channel.setdefault(channel_id, []).append(snapshot)
    for channel_id, snaps in by_channel.items():
        name, _, purchase = channel_for(snaps[0].host)
        phone = next((PHONE_RE.search(s.text) for s in snaps if PHONE_RE.search(s.text)), None)
        landings = list(dict.fromkeys(s.url for s in snaps))
        channels.append(
            ChannelBinding(
                ref=channel_id,
                name=name,
                purchase=purchase,
                landing=landings[0],
                hotline=phone.group() if phone else None,
                surfaces=landings[1:],
            )
        )

    tiers = [t for t in group.tiers if any(r.tier == t for r in rows)]
    links = Links(
        benefits=f"{page_id}/benefits" if rows else None,
        exclusions=f"{page_id}/exclusions",
        claims=f"journey/claim/{group.slug}" if group.claims else None,
        concepts=concepts,
    )
    fm = _common(
        config,
        page_id,
        group.title,
        PageType.product,
        authority,
        lifecycle=Lifecycle.on_sale,
        underwriter=LEGAL_NAME,
        uen=UEN,
        line_of_business=lob,
        regulated_advice=regulated,
        aliases=aliases,
        channels=channels,
        plan_tiers=tiers,
        version_in_force=version,
        effective_from=_snapshot_date(primary),
        links=links,
        confidence=Confidence.high if len(ordered) > 1 and rows else Confidence.medium,
    )

    body: list[str] = ["## What this plan is"]
    intro = _grounded(_normalise_brands(_first_sentence(primary.intro)), primary.ref("body"), report)
    if intro:
        body.append(intro)
    body.append(
        "Cover, limits and exclusions are identical on every channel; a channel is a "
        f"route to market rather than a separate product [src:{primary.ref('body')}]."
    )

    if rows:
        body.append("## Headline benefits")
        seen: set[str] = set()
        for row in rows:
            if row.benefit_code in seen:
                continue
            seen.add(row.benefit_code)
            body.append(
                f"{benefit_phrase(row.benefit_code, row.attribute)} "
                f"{{{{table:{row.benefit_code}.{row.attribute}}}}} [src:{row.source_ref}]."
            )
        body.append(f"Full benefit detail is on the [benefits page](./{group.slug}/benefits.md).")

    body.append("## What is not covered")
    body.append(f"The complete list is on the [exclusions page](./{group.slug}/exclusions.md).")

    body.append("## How to buy")
    rows_md = [
        f"| {b.name} ({b.purchase.replace('_', ' ')}) | "
        f"{{{{channel.{b.ref.split('/')[-1]}.landing}}}} | {{{{channel.{b.ref.split('/')[-1]}.hotline}}}} |"
        for b in channels
    ]
    body.append(
        "\n".join(
            [
                "<!-- okf:channel-variant -->",
                "| Channel | Route | Contact |",
                "|---|---|---|",
                *rows_md,
                "<!-- /okf:channel-variant -->",
            ]
        )
    )

    faq = next((group.faq[h] for h in hosts if h in group.faq), None)
    if faq is not None:
        questions = [s for s in faq.sections if s.heading]
        if questions:
            body.append("## Common questions")
        for section in questions:
            body.append(f"### {section.heading}")
            body += _section_body(faq, section, report)

    _write(config, _page(fm, body), report)
    return page_id


def emit_benefits(
    config: CompileConfig,
    group: ProductGroup,
    page_id: str,
    version: str,
    hosts: list[str],
    rows: list[BenefitRow],
    report: CompileReport,
) -> None:
    ordered = [group.product[h] for h in hosts if h in group.product]
    fm = _common(
        config,
        f"{page_id}/benefits",
        f"{group.title} — Benefits",
        PageType.product,
        [s.ref() for s in ordered],
        lifecycle=Lifecycle.on_sale,
        underwriter=LEGAL_NAME,
        line_of_business=line_of_business(group.slug, group.title),
        aliases=[f"{group.slug} benefits", f"{group.slug} limits", f"what does {group.slug} cover"],
        plan_tiers=[t for t in group.tiers if any(r.tier == t for r in rows)],
        version_in_force=version,
        effective_from=_snapshot_date(ordered[0]),
        links=Links(exclusions=f"{page_id}/exclusions"),
        confidence=Confidence.high,
    )
    body: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row.benefit_code in seen:
            continue
        seen.add(row.benefit_code)
        label = row.benefit_code.replace("_", " ")
        body.append(f"## {label[:1].upper()}{label[1:]}")
        body.append(
            f"The amount payable for the plan tier held is "
            f"{{{{table:{row.benefit_code}.{row.attribute}}}}} [src:{row.source_ref}]."
        )
    _write(config, _page(fm, body), report)


def emit_exclusions(
    config: CompileConfig,
    group: ProductGroup,
    page_id: str,
    version: str,
    hosts: list[str],
    concepts: list[str],
    report: CompileReport,
) -> None:
    ordered = [group.product[h] for h in hosts if h in group.product]
    items: list[tuple[str, str]] = []
    for snapshot in ordered:
        section = snapshot.section("What is not covered", "Exclusions", "What is excluded")
        if section is None:
            continue
        for item in _bullets(section) or section.paragraphs:
            if item.lower() not in {i.lower() for i, _ in items}:
                items.append((item, snapshot.ref(section.anchor)))
    if not items:
        report.skip("no exclusions section on any host")

    fm = _common(
        config,
        f"{page_id}/exclusions",
        f"{group.title} — Exclusions",
        PageType.product,
        [s.ref() for s in ordered],
        lifecycle=Lifecycle.on_sale,
        underwriter=LEGAL_NAME,
        line_of_business=line_of_business(group.slug, group.title),
        aliases=[
            f"{group.slug} exclusions",
            f"{group.slug} not covered",
            f"what is not covered by {group.slug}",
        ],
        version_in_force=version,
        effective_from=_snapshot_date(ordered[0]),
        links=Links(concepts=concepts),
        confidence=Confidence.high if items else Confidence.low,
    )
    body: list[str] = []
    for item, ref in items:
        body.append(f"## {item}")
        grounded = _grounded(f"{item} are excluded under this policy", ref, report)
        if grounded:
            body.append(grounded)
    if not body:
        body = [
            "## Exclusions",
            f"The published exclusions could not be extracted from the source page [src:{ordered[0].ref()}].",
        ]
    _write(config, _page(fm, body), report)


def _snapshot_date(snapshot: Snapshot) -> dt.date:
    try:
        return dt.date.fromisoformat(snapshot.path.parent.name)
    except ValueError:
        return dt.date.today()


def emit_journey(
    config: CompileConfig,
    page_id: str,
    title: str,
    snapshot: Snapshot,
    aliases: list[str],
    report: CompileReport,
    concepts: list[str] | None = None,
) -> str | None:
    body: list[str] = []
    for section in snapshot.sections:
        if not section.heading:
            continue
        lines = _section_body(snapshot, section, report)
        if lines:
            body.append(f"## {section.heading}")
            body += lines
    if not body:
        report.skip("journey page had no publishable prose")
        return None
    fm = _common(
        config,
        page_id,
        title,
        PageType.journey,
        [snapshot.ref()],
        aliases=aliases,
        effective_from=_snapshot_date(snapshot),
        links=Links(concepts=concepts or []),
        confidence=Confidence.high,
    )
    _write(config, _page(fm, body), report)
    return page_id


def emit_concepts(config: CompileConfig, snapshots: list[Snapshot], report: CompileReport) -> list[str]:
    """A concept page is written only where the crawl defines the term, so the
    definition is quoted rather than invented."""
    emitted: list[str] = []
    for page_id, title, pattern, aliases in CONCEPT_TERMS:
        found = _sentence_with(pattern, snapshots)
        if found is None:
            report.skip(f"no source sentence defines {title!r}")
            continue
        sentence, ref = found
        grounded = _grounded(sentence, ref, report)
        if grounded is None:
            continue
        fm = _common(
            config,
            page_id,
            title,
            PageType.concept,
            [ref.split("#")[0]],
            aliases=aliases,
            effective_from=config.today,
            confidence=Confidence.medium,
        )
        _write(config, _page(fm, ["## What it means", grounded]), report)
        emitted.append(page_id)
    return emitted


def emit_channels(
    config: CompileConfig,
    groups: dict[str, ProductGroup],
    hosts: list[str],
    snapshots: list[Snapshot],
    report: CompileReport,
) -> list[str]:
    """One page per distribution channel — never one per host.

    Several hosts can be front doors of the same route: etiqa.com.sg and
    tiq.com.sg both answer `channel/direct`. They are folded into a single page
    carrying every surface, so no reader is asked which brand they want.
    Intermediated routes (bank, agency, broker, IFA) have no storefront of
    their own and are emitted only where the crawl actually describes them.
    """
    emitted: list[str] = []

    # --- routes the crawl gives us a storefront for ---------------------
    by_channel: dict[str, list[str]] = {}
    for host in hosts:
        _, channel_id, _ = channel_for(host)
        by_channel.setdefault(channel_id, []).append(host)

    for channel_id, channel_hosts in by_channel.items():
        name, _, purchase = channel_for(channel_hosts[0])
        snaps = [g.product[h] for g in groups.values() for h in channel_hosts if h in g.product]
        if not snaps:
            continue
        phone = next((PHONE_RE.search(s.text) for s in snaps if PHONE_RE.search(s.text)), None)
        landings = [f"https://{h}/" for h in channel_hosts]
        slug = channel_id.split("/")[-1]
        fm = _common(
            config,
            channel_id,
            f"{name} channel (Singapore)",
            PageType.channel,
            sorted({s.ref() for s in snaps})[:4],
            aliases=sorted({name, slug, f"buy {slug}", f"{slug} channel", *channel_hosts}),
            effective_from=_snapshot_date(snaps[0]),
            confidence=Confidence.high,
            purchase=purchase,
            landing=landings[0],
            surfaces=landings[1:],
            hotline=phone.group() if phone else None,
        )
        surface_rows = [f"| {landing} | {{{{channel.{slug}.hotline}}}} |" for landing in landings]
        body = [
            "## How to reach us",
            "\n".join(
                [
                    "<!-- okf:channel-variant -->",
                    "| Route | Contact |",
                    "|---|---|",
                    *surface_rows,
                    "<!-- /okf:channel-variant -->",
                ]
            ),
        ]
        if len(landings) > 1:
            body.append(
                "These addresses are front doors of the same channel, not different "
                "insurers or different products; either one reaches the same cover "
                f"[src:{snaps[0].ref()}]."
            )
        body += [
            "Contact this channel about a policy through a route above; policy servicing "
            f"requests are handled in the customer portal [src:{snaps[0].ref()}].",
            "## Channel binding",
            f"This channel is a route to market for {LEGAL_NAME}, and the products sold "
            f"through it are the same canonical products [src:{snaps[0].ref()}].",
            "The purchase route and the people the customer deals with are the only "
            f"attributes that vary by channel [src:{snaps[0].ref()}].",
        ]
        _write(config, _page(fm, body), report)
        emitted.append(channel_id)

    # --- intermediated routes, if the crawl describes them ---------------
    for spec in ALL_CHANNELS:
        if spec.ref.value in emitted or spec.intermediary is None:
            continue
        found = _sentence_with(re.compile(re.escape(spec.intermediary), re.I), snapshots)
        if found is None:
            report.skip(f"no source sentence describes the {spec.name} channel")
            continue
        sentence, ref = found
        slug = spec.ref.value.split("/")[-1]
        fm = _common(
            config,
            spec.ref.value,
            f"{spec.name} channel (Singapore)",
            PageType.channel,
            [ref],
            aliases=sorted({spec.name, slug, spec.intermediary, f"buy through {spec.intermediary}"}),
            effective_from=config.today,
            confidence=Confidence.medium,
            purchase=spec.purchase,
            landing=spec.landing,
            hotline=spec.hotline,
            intermediary=spec.intermediary,
        )
        body = [
            "## How to reach us",
            "\n".join(
                [
                    "<!-- okf:channel-variant -->",
                    "| Route | Contact |",
                    "|---|---|",
                    f"| {{{{channel.{slug}.landing}}}} | {{{{channel.{slug}.hotline}}}} |",
                    "<!-- /okf:channel-variant -->",
                ]
            ),
            _grounded(sentence, ref, report)
            or f"Cover is arranged through a {spec.intermediary} [src:{ref}].",
            "## Channel binding",
            f"This channel is a route to market for {LEGAL_NAME}, and the products sold "
            f"through it are the same canonical products [src:{ref}].",
            f"A customer on this route deals with a {spec.intermediary} rather than buying "
            f"online; the cover itself is unchanged [src:{ref}].",
        ]
        _write(config, _page(fm, body), report)
        emitted.append(spec.ref.value)

    return emitted


def _parse_window(text: str) -> tuple[dt.date | None, dt.date | None]:
    dates = [dt.date(int(y), MONTHS[m.lower()], int(d)) for d, m, y in DATE_RE.findall(text)]
    if not dates:
        return None, None
    if len(dates) == 1:
        return None, dates[0]
    return min(dates), max(dates)


DATE_CLAUSE_RE = re.compile(
    r"\s*\b(?:before|until|till|from|by|after|on|as of|valid until|ending)\b\s*" + DATE_RE.pattern,
    re.I,
)
TRAILING_PREPOSITION_RE = re.compile(r"\b(?:of|as|before|until|from|by|on|after|to|in)\s*$", re.I)


def _offer_prose(paragraph: str) -> str:
    """Strip the dates out of promotional copy.

    The effective window is structured frontmatter on the promotion page, so a
    date repeated in prose is a second, unbindable copy of a fact the harness
    already holds — and the numeric-binding gate is right to refuse it. What
    survives is the offer itself, whose figure binds to the dated page."""
    kept: list[str] = []
    for sentence in re.split(r"(?<=\.)\s+", " ".join(paragraph.split())):
        cleaned = DATE_CLAUSE_RE.sub("", sentence)
        cleaned = DATE_RE.sub("", cleaned).strip()
        cleaned = re.sub(r"\s+([.,])", r"\1", cleaned).rstrip(".").strip()
        if TRAILING_PREPOSITION_RE.search(cleaned) or len(cleaned.split()) < 4:
            continue
        kept.append(f"{cleaned}.")
    return " ".join(kept)


def emit_promotions(config: CompileConfig, snapshots: list[Snapshot], report: CompileReport) -> list[str]:
    emitted: list[str] = []
    for snapshot in snapshots:
        if snapshot.page_type != "promo":
            continue
        _, channel_id, _ = channel_for(snapshot.host)
        for section in snapshot.sections:
            if not section.heading:
                continue
            start, end = _parse_window(section.text)
            page_id = f"promotion/{channel_id.split('/')[-1]}-{slugify(section.heading)}"
            lines = [
                "## Offer",
                *[
                    line
                    for paragraph in section.paragraphs
                    if (
                        line := _grounded(
                            _offer_prose(paragraph), snapshot.ref(section.anchor), report, allow_number=True
                        )
                    )
                ],
            ]
            if len(lines) < 2:
                report.skip("promotion had no publishable offer text")
                continue
            fm = _common(
                config,
                page_id,
                f"{section.heading} — {snapshot.host}",
                PageType.promotion,
                [snapshot.ref()],
                aliases=[section.heading.lower(), "current promotion", "discount"],
                effective_from=start or _snapshot_date(snapshot),
                effective_to=end,
                confidence=Confidence.medium,
                channel_ref=channel_id,
            )
            _write(config, _page(fm, lines), report)
            emitted.append(page_id)
    return emitted


def emit_entity(config: CompileConfig, source: str, report: CompileReport) -> str:
    fm = _common(
        config,
        "entity/etiqa-sg-legal",
        LEGAL_NAME,
        PageType.entity,
        [source],
        aliases=["underwriter", "who underwrites", "insurer"],
        underwriter=LEGAL_NAME,
        uen=UEN,
        effective_from=config.today,
        confidence=Confidence.high,
    )
    body = [
        "## The underwriting entity",
        f"All products in this knowledge base are underwritten by {LEGAL_NAME}, "
        f"a Singapore-registered insurer [src:{source}].",
        "Brands are distribution surfaces of that single entity, which is why one "
        f"canonical page serves every channel [src:{source}].",
    ]
    _write(config, _page(fm, body), report)
    return "entity/etiqa-sg-legal"


def emit_index(
    config: CompileConfig,
    products: dict[str, list[tuple[str, str]]],
    concepts: list[str],
    journeys: list[tuple[str, str]],
    channels: list[str],
    entity: str,
    promotions: list[str],
    report: CompileReport,
) -> None:
    fm = _common(
        config,
        "index",
        "Etiqa Singapore knowledge base",
        PageType.index_page,
        [],
        underwriter=LEGAL_NAME,
        uen=UEN,
        effective_from=config.today,
        confidence=Confidence.high,
    )
    body = [
        "## How to use this wiki",
        "One canonical page per product. Brand is a channel attribute, not a product "
        "identity — the same product answers identically on every surface.",
    ]
    for lob in sorted(products):
        body.append(f"## Products — {lob}")
        for page_id, title in sorted(products[lob]):
            depth = page_id.count("/")
            body.append(f"- `{page_id}` — [{title}](./{page_id}.md)" if depth else f"- `{page_id}`")
    if concepts:
        body.append("## Concepts")
        body += [f"- [{c.split('/')[-1]}](./{c}.md)" for c in sorted(concepts)]
    if journeys:
        body.append("## Journeys")
        body += [f"- [{title}](./{page_id}.md)" for page_id, title in sorted(journeys)]
    if promotions:
        body.append("## Promotions")
        body += [f"- [{p.split('/')[-1]}](./{p}.md)" for p in sorted(promotions)]
    body.append("## Channels")
    body += [f"- [{c.split('/')[-1]}](./{c}.md)" for c in sorted(channels)]
    body.append("## Entities")
    body.append(f"- [{LEGAL_NAME}](./{entity}.md)")
    _write(config, _page(fm, body), report)


def write_manifest(config: CompileConfig, hosts: list[str]) -> None:
    manifest = {
        "name": "etiqa-sg-knowledge",
        "okf_version": "0.1",
        "jurisdiction": "SG",
        "underwriter": LEGAL_NAME,
        "uen": UEN,
        "compiled_at": config.today.isoformat(),
        # `.example` is IANA-reserved: a corpus crawled from it is synthetic by
        # construction, and everything downstream should say so out loud.
        "fixture": any(host.endswith(".example") for host in hosts),
        "taxonomy": {
            "product_roots": [
                "protection",
                "health-medical",
                "savings-retirement",
                "investments",
                "general",
                "motor",
                "business",
                "premier",
                "scheme",
            ],
            "page_types": ["product", "concept", "journey", "channel", "entity", "promotion", "index"],
        },
        "authority_order": [
            "raw/wordings",
            "raw/product-summaries",
            "raw/benefit-tables",
            *[f"raw/web/{host}" for host in hosts],
            "raw/blog",
        ],
        "link_rules": {
            "product_requires": ["benefits", "exclusions"],
            "approved_requires": ["reviewed_by", "review_due", "authority"],
        },
    }
    path = config.dest_root / "okf.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Bundle manifest (§C.1) — COMPILED, do not hand-edit.\n"
        "# Written by `compiler.cli wiki` from the crawl snapshots under raw/web/.\n"
        "# Re-run the compile rather than patching a page: the wiki is a build output.\n"
    )
    path.write_text(header + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))


def write_conflicts(config: CompileConfig, report: CompileReport) -> None:
    if not report.conflicts:
        return
    directory = config.dest_root / "conflicts"
    directory.mkdir(parents=True, exist_ok=True)
    for conflict in report.conflicts:
        slug = slugify(f"{conflict.product}-{conflict.coordinate}")
        (directory / f"{slug}.md").write_text(
            f"# Website defect — {conflict.product} {conflict.coordinate}\n\n"
            f"- opened: {config.today.isoformat()}\n"
            f"- kept (higher authority): `{conflict.kept}` from `{conflict.kept_source}`\n"
            f"- contradicted: `{conflict.dropped}` from `{conflict.dropped_source}`\n\n"
            "The wiki carries the higher-authority value. This ticket is against the\n"
            "**website**, not the wiki: two published surfaces disagree about the same\n"
            "benefit and a customer can read either (§D.2).\n"
        )


def compile_bundle(config: CompileConfig) -> CompileReport:
    report = CompileReport()
    snapshots = load_snapshots(config.source_root)
    if not snapshots:
        return report

    hosts = config.authority_hosts or rank_hosts(
        sorted({s.host for s in snapshots}),
        [h for h in sorted({s.host for s in snapshots}) if "etiqa" in h],
    )
    groups = group_products(snapshots, report)
    versions = versions_from_documents(config.source_root)
    concepts = emit_concepts(config, snapshots, report)

    products: dict[str, list[tuple[str, str]]] = {}
    journeys: list[tuple[str, str]] = []

    for slug in sorted(groups):
        group = groups[slug]
        version = versions.get(slug, str(config.today.year))
        group_hosts = rank_hosts(group.hosts, hosts)
        rows = benefit_rows(group, version, group_hosts, report)
        linked = [c for c in concepts if _concept_pattern(c).search(group.text)]

        if rows:
            write_benefit_table(config.dest_root, slug, rows)
            report.tables[slug] = len(rows)

        page_id = emit_product(config, group, version, group_hosts, rows, linked, report)
        lob = page_id.split("/")[1]
        products.setdefault(lob, []).append((page_id, group.title))
        if rows:
            emit_benefits(config, group, page_id, version, group_hosts, rows, report)
            products[lob].append((f"{page_id}/benefits", f"{group.title} — Benefits"))
        emit_exclusions(config, group, page_id, version, group_hosts, linked, report)
        products[lob].append((f"{page_id}/exclusions", f"{group.title} — Exclusions"))

        claim = next((group.claims[h] for h in group_hosts if h in group.claims), None)
        if claim is not None:
            title = f"Making a {group.title} claim"
            emitted = emit_journey(
                config,
                f"journey/claim/{slug}",
                title,
                claim,
                [f"claim {slug}", f"{slug} claim", f"how to claim {slug}"],
                report,
                linked,
            )
            if emitted:
                journeys.append((emitted, title))

    for snapshot in snapshots:
        if snapshot.page_type != "servicing" or snapshot.slug in SECTION_ROOTS:
            continue
        page_id = f"journey/service/{snapshot.slug}"
        if page_id in report.pages:
            continue
        title = snapshot.title
        emitted = emit_journey(
            config,
            page_id,
            title,
            snapshot,
            [snapshot.slug.replace("-", " "), f"how do i {snapshot.slug.replace('-', ' ')}"],
            report,
        )
        if emitted:
            journeys.append((emitted, title))

    channels = emit_channels(config, groups, hosts, snapshots, report)
    promotions = emit_promotions(config, snapshots, report)
    governance = next((s for s in snapshots if s.page_type == "governance"), snapshots[0])
    entity = emit_entity(config, governance.ref(), report)
    emit_index(config, products, concepts, journeys, channels, entity, promotions, report)
    write_manifest(config, hosts)
    write_conflicts(config, report)
    return report


def _concept_pattern(page_id: str) -> re.Pattern[str]:
    for concept_id, _, pattern, _ in CONCEPT_TERMS:
        if concept_id == page_id:
            return pattern
    return re.compile(re.escape(page_id.split("/")[-1]), re.I)
