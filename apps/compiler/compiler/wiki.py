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
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from harness.intent import classify
from okf.channels import ALL_CHANNELS, channel_for_host

# The compiler writes what the linter checks; sharing the patterns is what
# stops it from emitting a page the build then rejects.
from okf.linter import ALLOW_NUMBER, NUMBER_IN_PROSE_RE, ROUTE_RE, SOURCE_REF_RE
from okf.page import (
    UNCOMPILED_MARK,
    ChannelBinding,
    Confidence,
    Frontmatter,
    Lifecycle,
    Links,
    Page,
    PageType,
    Status,
    parse_page,
    render_page,
)
from okf.sources import PRODUCT_PAGE_TYPES

from compiler.catalogue import Catalogue, Entry, load_catalogue
from compiler.documents import (
    COMPILED_ROLES,
    TIERS,
    Document,
    _keys,
    campaign_documents,
    load_documents,
    match_documents,
)
from compiler.snapshots import Section, Snapshot, Table, load_snapshots, slugify

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
# Word boundaries are load-bearing here, not tidiness. Unbounded, `car` matched
# inside "Essential Cancer **Car**e" and `van` inside "Ad**van**ced CI Rider" —
# so a cancer plan and a CI rider were both filed under motor, and a customer
# asking what car insurance we sell was offered them. Where a rule should match
# a family of words the boundary is on the left only: `\binvest` is meant to
# catch "investment" and "investment-linked".
LOB_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:car|motor|motorcycle|vehicle|van|fleet|driving)\b"), "motor"),
    (re.compile(r"\b(?:business|sme|commercial|work-injury|employer|employee|corporate)\b"), "business"),
    (re.compile(r"\b(?:invest|ilp|unit-trust|fund)"), "investments"),
    (re.compile(r"\b(?:save|saver|saving|endowment|retirement|annuity|legacy)"), "savings-retirement"),
    (re.compile(r"\b(?:life|cancer|critical|terminal|protection|ci)\b"), "protection"),
    (re.compile(r"\b(?:medical|health|hospital|shield|dental|clinic)"), "health-medical"),
    (re.compile(r"\b(?:premier|prestige)\b"), "premier"),
]

ADVICE_RE = re.compile(
    r"licensed financial adviser|financial advice|advised product|this plan is advised", re.I
)
ALIAS_RE = re.compile(r"also known as ([^.]+)\.", re.I)
PHONE_RE = re.compile(r"\+65[\s-]?\d{4}[\s-]?\d{4}")

#: A number the page presents as something *other* than a general contact.
#: Publishing one of these as the channel hotline sends every customer down a
#: line reserved for something else — the direct channel shipped
#: `+65 9695 1338` for months, which the corpus names as "HDB Basic Fire Claims
#: Only" and attributes to two loss adjusters by name.
NOT_A_HOTLINE_RE = re.compile(
    r"claims only|fire claims|emergency|assistance hotline|24[\s-]?hours? helpline"
    r"|loss adjuster|surveyor|\(\s*[A-Z][a-z]+ [A-Z][a-z]+",
    re.I,
)
#: ...and one it presents as exactly that.
IS_A_HOTLINE_RE = re.compile(
    r"customer (care|service)|general enquir|contact us|hotline|switchboard|^t\b", re.I
)
#: Singapore numbering: mobile lines begin 8 or 9, fixed lines 6. A hotline a
#: company publishes is a fixed line, so this alone separates a switchboard
#: from somebody's handphone.
LANDLINE_RE = re.compile(r"\+65[\s-]?6")


def _hotline(texts: list[str]) -> str | None:
    """The best general contact number in these pages, or None.

    Ranked rather than first-found. `next(PHONE_RE.search(...))` reads whichever
    number the crawler happened to reach first, which is how a claims-only
    mobile became the number on every direct-channel answer.
    """
    best: tuple[int, str] | None = None
    for text in texts:
        for match in PHONE_RE.finditer(text):
            window = text[max(0, match.start() - 120) : match.end() + 120]
            if NOT_A_HOTLINE_RE.search(window):
                continue
            score = 2 if LANDLINE_RE.match(match.group()) else 0
            score += 1 if IS_A_HOTLINE_RE.search(window) else 0
            if best is None or score > best[0]:
                best = (score, match.group())
    return best[1] if best else None


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


#: Section headings that are page furniture rather than a description of
#: the product: referral schemes, navigation, and calls to action.
_OVERVIEW_SKIP_RE = re.compile(
    r"refer and earn|you might also|related|share this|follow us|sign up|get a quote"
    r"|start securing|contact us|need help|other products",
    re.I,
)

PHRASE = {
    "limit": "The {label} limit for the plan tier held is",
    "rate": "The {label} is",
    "period": "The {label} is",
    "value": "The {label} is",
}


def benefit_phrase(code: str, attribute: str) -> str:
    label = code.replace("_", " ")
    return PHRASE.get(attribute, PHRASE["value"]).format(label=label)


#: Leading enumeration on a schedule row — "1.", "(a)", "8)". Part of the
#: layout, not part of the benefit's name.
_ENUMERATION_RE = re.compile(r"^\s*[\(\[]?\s*(\d{1,2}|[a-z])\s*[\)\].]\s+", re.I)

#: Trailing qualification a schedule attaches to a benefit name: "excess $200
#: for each and every claim except fire". The benefit is what precedes it.
_QUALIFIER_RE = re.compile(r"\s+(?:\(|-\s|,\s)?(?:excess|except|including|subject to|per\b|up to)\b.*$", re.I)


def benefit_code(label: str) -> str:
    """A row label reduced to the benefit it names.

    Real schedules number their rows and qualify their benefits inline, so the
    raw label is "9. Product Liability (a) accidental bodily injury to or
    illness of any person (b) accidental loss of or damage to property…". All
    of that is one benefit — product liability — wearing its policy wording.
    Taken verbatim it became a 250-character identifier that no question could
    ever match.
    """
    label = _ENUMERATION_RE.sub("", label.strip())
    label = _QUALIFIER_RE.sub("", label)
    # A bare number is a row index, not a benefit. "1 | 14 km | 30%" is a
    # ranking table whose first column counts the rows.
    if re.fullmatch(r"[\d\W]*", label):
        return ""
    code = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    # Keep it to something a question could plausibly contain.
    return "_".join(code.split("_")[:6])


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
    #: Names the product is listed under elsewhere — the title of a listing
    #: folded into this group. "Tiq Home Insurance" for Home Insurance: a
    #: customer types the name on the site they bought from, and losing it
    #: with the folded listing sent "tiq home" to a fire-safety event page.
    names: list[str] = field(default_factory=list)
    #: The catalogue entry this group is, where a catalogue drives the build.
    entry: Entry | None = None
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
    #: Policy documents read from the wordings and product-summary tiers.
    documents: int = 0

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


#: A heading flattened into the intro and then repeated by the subheading:
#: "Protect what belongs to you Protect what belongs to you Give your family…".
_REPEATED_OPENING_RE = re.compile(r"^(.{6,80}?)\s+\1\s+")
#: Button labels the extractor flattened into offer copy.
_BUTTON_LABEL_RE = re.compile(
    r"\b(?:Buy Now|Read More|Learn More|Find Out More|Apply Now|Get (?:a )?Quote|T&Cs? apply)\b\.?", re.I
)


def _drop_repeated_opening(text: str) -> str:
    """ "Safeguard your pet's wellbeing Safeguard your pet's wellbeing We…" —
    a heading and a subheading flattened into the intro, compared word by
    word with punctuation ignored, since the two copies can differ by an
    apostrophe."""
    words = text.split()
    norm = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in words]
    for n in range(min(12, len(words) // 2), 1, -1):
        if norm[:n] == norm[n : 2 * n]:
            return " ".join(words[n:])
    return text


def _first_sentence(text: str) -> str:
    text = _drop_repeated_opening(_REPEATED_OPENING_RE.sub(r"\1 ", " ".join(text.split())))
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


#: A URL that is editorial however the crawl labelled it. The recorded
#: `page_type` is a crawl-time judgement, and a crawl is expensive to repeat —
#: this is the compile's own read of the same URL, so a classifier fix takes
#: effect on the next compile instead of the next crawl. Measured: Etiqa files
#: its blog under `/blog_tags/life`, the crawler's blog rule wanted a `/` where
#: the underscore is, and the product rule then matched "life" — so a blog tag
#: index compiled into a product called "Blog Tag: Life" and was offered to
#: customers asking what life products exist.
NOT_A_PRODUCT_URL = re.compile(
    r"/(?:blog|articles?|stories|news|tags?|categor(?:y|ies)|authors?)(?:/|-|_|$)", re.I
)


def group_products(
    snapshots: list[Snapshot], report: CompileReport, catalogue: Catalogue | None = None
) -> dict[str, ProductGroup]:
    if catalogue is not None:
        return _group_by_catalogue(snapshots, report, catalogue)
    groups: dict[str, ProductGroup] = {}
    for snapshot in snapshots:
        if snapshot.page_type != "product":
            continue
        if NOT_A_PRODUCT_URL.search(snapshot.url):
            report.skip("editorial page the crawl labelled a product")
            continue
        if snapshot.slug in SECTION_ROOTS:
            report.skip("section index, not a product")
            continue
        group = groups.setdefault(snapshot.slug, ProductGroup(snapshot.slug))
        group.product[snapshot.host] = snapshot
        # The shortest title across hosts is the least brand-decorated one.
        if not group.title or len(snapshot.title) < len(group.title):
            group.title = snapshot.title

    folded = merge_duplicate_groups(groups, report)

    for snapshot in snapshots:
        attached = groups.get(folded.get(snapshot.slug, snapshot.slug))
        if attached is None or snapshot.slug in SECTION_ROOTS:
            continue
        if snapshot.page_type == "claims":
            attached.claims.setdefault(snapshot.host, snapshot)
        elif snapshot.page_type == "faq":
            attached.faq.setdefault(snapshot.host, snapshot)
    return groups


def _group_by_catalogue(
    snapshots: list[Snapshot], report: CompileReport, catalogue: Catalogue
) -> dict[str, ProductGroup]:
    """One group per catalogue entry, from the pages the entry lists.

    A crawled page the catalogue does not list is not a product, whatever
    the crawl called it — that is the whole point of the catalogue. A page
    shared by several entries (a category page) is attached to none. A
    claims or FAQ page attaches to the entry whose names it shares.
    """
    groups: dict[str, ProductGroup] = {}
    for entry in catalogue.entries:
        group = ProductGroup(entry.slug, title=entry.name, entry=entry)
        group.names = [a for a in entry.aliases if a.lower() != entry.name.lower()]
        groups[entry.slug] = group

    for snapshot in snapshots:
        if snapshot.page_type != "product":
            continue
        listed = catalogue.entry_for_url(snapshot.url)
        if listed is None:
            shared = catalogue.entries_for_url(snapshot.url)
            if shared:
                report.skip(
                    f"category page shared by {len(shared)} products — attached to none: {snapshot.url}"
                )
            else:
                report.skip("product page not in the catalogue — not compiled as a product")
            continue
        group = groups[listed.slug]
        current = group.product.get(snapshot.host)
        if current is None or len(snapshot.text) > len(current.text):
            group.product[snapshot.host] = snapshot

    # Claims and FAQ pages, by shared name keys.
    keysets = {slug: _group_keys(group) for slug, group in groups.items()}
    for snapshot in snapshots:
        if snapshot.page_type not in ("claims", "faq") or snapshot.slug in SECTION_ROOTS:
            continue
        forms = _keys(snapshot.slug)
        hits = [slug for slug, keys in keysets.items() if forms & keys]
        if len(hits) != 1:
            continue
        target = groups[hits[0]]
        if snapshot.page_type == "claims":
            target.claims.setdefault(snapshot.host, snapshot)
        else:
            target.faq.setdefault(snapshot.host, snapshot)
    return groups


def _group_keys(group: ProductGroup) -> set[str]:
    keys = set(_keys(group.slug))
    for name in (group.title, *group.names):
        keys |= _keys(slugify(name))
    if group.entry is not None:
        for url in group.entry.urls:
            keys |= _keys(url.rstrip("/").rsplit("/", 1)[-1].lower())
    return {k for k in keys if len(k) >= 3}


#: The words a shopfront puts in front of a product's name. Stripped, not
#: folded into the legal name as `_normalise_brands` does for prose: this
#: is identity, and "Tiq Home Insurance" and "Home Insurance" are one product.
_BRAND_PREFIX_RE = re.compile(r"\b(?:tiq|etiqa)\b", re.I)
_GENERIC_SUFFIX_RE = re.compile(r"\b(?:insurance|plan|policy)\b", re.I)


def product_identity(title: str) -> str:
    """What a product is called once the brand and the category word are
    taken off — the key two listings of one product share."""
    bare = _GENERIC_SUFFIX_RE.sub(" ", _BRAND_PREFIX_RE.sub(" ", title))
    return re.sub(r"[^a-z0-9]+", "", bare.lower())


def merge_duplicate_groups(groups: dict[str, ProductGroup], report: CompileReport) -> dict[str, str]:
    """Fold the same product listed under two slugs into one group.

    etiqa.com.sg lists Home Insurance twice — `personal-home-insurance` and
    `personal-home-insurance-tiq-home-insurance` — and tiq.com.sg spells the
    investment product `tiqinvest` where etiqa.com.sg spells it `tiq-invest`.
    Grouped by slug, each pair became two products: two pages, two benefit
    tables, two sets of aliases, and a customer asking about one was answered
    from the other. Six products were compiled twice on the real bundle, and
    twenty-nine of the FAQ suite's failures were exactly that.

    Two groups are one product when their titles agree once the brand and
    the category word are taken off. The group whose slug is the plainer one
    — no brand prefix, then shorter — keeps its slug and page id; the other's
    listings join it as further sources, one per host, the longer listing
    kept where a host already has one. Returns the slugs folded away, mapped
    to the slug that absorbed them.
    """
    by_identity: dict[str, list[str]] = {}
    for slug, group in groups.items():
        identity = product_identity(group.title or slug)
        if identity:
            by_identity.setdefault(identity, []).append(slug)

    def plainness(slug: str) -> tuple[bool, int, str]:
        return (bool(_BRAND_PREFIX_RE.match(slug.replace("-", " "))), len(slug), slug)

    folded: dict[str, str] = {}
    for slugs in by_identity.values():
        if len(slugs) < 2:
            continue
        keep, *others = sorted(slugs, key=plainness)
        target = groups[keep]
        for slug in others:
            other = groups.pop(slug)
            for host, snapshot in other.product.items():
                # The canonical group's own listing stands. "Longer wins" was
                # the first rule, and on Home Insurance the longer listing
                # was a post-application page whose one section is 419 words
                # of marketing-consent terms; it replaced the real product
                # page and "Marketing Consent Terms & Conditions" became what
                # the plan is.
                if host in target.product:
                    report.skip("duplicate listing of a product on the same host — the canonical one kept")
                else:
                    target.product[host] = snapshot
            for host, snapshot in other.claims.items():
                target.claims.setdefault(host, snapshot)
            for host, snapshot in other.faq.items():
                target.faq.setdefault(host, snapshot)
            for name in (other.title, *other.names):
                if name and name not in target.names and name != target.title:
                    target.names.append(name)
            if not target.title or (other.title and len(other.title) < len(target.title)):
                target.title = other.title
            folded[slug] = keep
            report.skip(f"same product listed under two slugs — {slug} folded into {keep}")
    return folded


def rank_hosts(hosts: list[str], order: list[str]) -> list[str]:
    """Authority order (§D.2). Anything unranked sorts last, alphabetically,
    so the result is stable rather than dependent on crawl order."""

    def key(host: str) -> tuple[int, str]:
        return (order.index(host) if host in order else len(order), host)

    return sorted(hosts, key=key)


#: A column header that names a measure rather than a plan tier. When most of
#: them do, the table is transposed: its rows are variants (flat types, age
#: bands) and its columns are the attributes.
MEASURE_HEADER_RE = re.compile(
    r"premium|sum insured|limit|excess|benefit|amount|coverage|payout|rate\b|price|cost|fee|charge",
    re.I,
)

#: The longest a row label can be and still name a benefit. Beyond this it is a
#: sentence from a policy schedule, and `1_all_risks_excess_200_for_each_every_
#: claim_except_f` is what came of accepting one.
MAX_LABEL_WORDS = 6


def looks_like_a_benefit_table(table: Table) -> bool:
    """Whether this table states benefit values at all.

    The compiler used to take the largest table on the page. On these sites the
    largest table is routinely a blog comparison grid ("An EV may suit you
    if…"), a fund price list, or a promotion ladder — and trying to read benefit
    rows out of one produced most of the 258 uninterpretable cells and 143
    ragged rows the compile reported, plus benefit codes that were whole
    sentences.

    A benefit table is recognisable: its value cells are mostly *values*, and
    its row labels are short enough to name something. Both have to hold, since
    a fund price list passes the first test on its own.
    """
    if len(table.header) < 2 or not table.rows:
        return False
    values = parseable = 0
    for cells in table.rows:
        for cell in cells[1:]:
            if not cell.strip():
                continue
            values += 1
            if parse_cell(cell) is not None:
                parseable += 1
    if values < 2 or parseable / values < 0.6:
        return False
    labels = [cells[0] for cells in table.rows if cells and cells[0].strip()]
    if not labels:
        return False
    usable = sum(1 for label in labels if len(label.split()) <= MAX_LABEL_WORDS)
    return usable / len(labels) >= 0.6


def pick_benefit_table(tables: list[Table]) -> Table | None:
    """The best benefit table on a page, or none.

    Largest-wins was the bug. Among tables that actually state benefits, more
    rows is still the right tie-break — but a page with no benefit table should
    contribute no benefit rows rather than its blog grid.
    """
    candidates = [t for t in tables if looks_like_a_benefit_table(t)]
    return max(candidates, key=lambda t: len(t.rows)) if candidates else None


def benefit_rows(
    group: ProductGroup, version: str, hosts: list[str], report: CompileReport
) -> list[BenefitRow]:
    """Every table cell on every host, reconciled by authority."""
    kept: dict[tuple[str, str, str], BenefitRow] = {}
    for host in hosts:
        snapshot = group.product.get(host)
        if snapshot is None or not snapshot.tables:
            continue
        table = pick_benefit_table(snapshot.tables)
        if table is None:
            report.skip("no table on the page states benefit values")
            continue
        columns = table.header[1:]
        # Transposed: the rows are variants and the columns are the measures.
        # "Flat Types | Premium | Sum Insured" reads the other way round from
        # "Benefit | Basic | Premier", and reading it the wrong way made the
        # flat type the benefit and the premium column the plan tier.
        transposed = sum(1 for c in columns if MEASURE_HEADER_RE.search(c)) > len(columns) / 2
        tiers = ["ALL"] if len(table.header) == 2 else [slugify(h) for h in columns]
        if not group.tiers and tiers != ["ALL"] and not transposed:
            group.tiers = tiers
        for cells in table.rows:
            if len(cells) != len(table.header):
                report.skip("ragged table row")
                continue
            label = cells[0]
            if len(label.split()) > MAX_LABEL_WORDS:
                report.skip("row label is a sentence, not a benefit name")
                continue
            code = benefit_code(label)
            if not code:
                continue
            for tier, cell in zip(tiers, cells[1:], strict=False):
                parsed = parse_cell(cell)
                if parsed is None:
                    report.skip("uninterpretable table cell")
                    continue
                value, unit, attribute = parsed
                if transposed:
                    # The column names the measure; the row names the variant.
                    code, tier, attribute = (
                        benefit_code(columns[tiers.index(tier)]),
                        slugify(label),
                        attribute,
                    )
                    code = code or "benefit"
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


#: A `##` heading is a label, not a claim. Two things leak into one and both
#: reach the customer: a `[src:...]` marker, when a heading is built from text
#: that already carried its reference, and an undecoded HTML entity from the
#: crawl. 275 headings across 155 pages carried a marker on the real bundle.
#: The composer binds a claim to its heading, so a mangled heading becomes a
#: mangled claim — "tiq travel" was refused because of one.
_HEADING_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " "}


def _heading(text: str) -> str:
    """One `##` heading, cleaned. Every heading the compiler writes goes here."""
    text = SOURCE_REF_RE.sub("", text)
    for entity, char in _HEADING_ENTITIES.items():
        text = text.replace(entity, char)
    return " ".join(text.split()).strip(" .,;:")


def _page(fm: Frontmatter, body: list[str]) -> Page:
    return Page(frontmatter=fm, body="\n\n".join(b for b in body if b).strip() + "\n")


def _write(config: CompileConfig, page: Page, report: CompileReport) -> None:
    path = config.dest_root / "wiki" / f"{page.id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_page(page))
    if page.id not in report.pages:
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


#: Where a product page stops describing the cover and starts selling extras,
#: answering questions, or listing articles. Everything from here down is
#: either an add-on — which is *not* included cover, and reading it as though
#: it were is the misleading answer this section exists to prevent — or
#: navigation. "Level up your plan" opens that block; "Flexible add-ons" does
#: not, it is a tile inside the cover block naming that add-ons exist, and
#: ending the scan on the word cost the page every tile that followed it.
_COVER_END_RE = re.compile(
    r"level up|value added|useful information|frequently asked|faq"
    r"|customers say|featured|follow us|claims process|questions",
    re.I,
)
#: A heading that describes something other than the cover, above that line.
#: "Apply for business insurance today" is a call to action, not a benefit,
#: and it was compiled onto eight pages as what the product covers.
_COVER_SKIP_RE = re.compile(r"advisory|about us|sitemap|overview of|apply (?:for|now)|start your", re.I)
#: Sentence-level noise the tile shape does not exclude: a navigation bar the
#: extractor flattened into prose ("Coverage | Resources | FAQs"), and a
#: closure notice, which is a fact about the *shopfront* and not about cover.
_COVER_NOISE_RE = re.compile(
    r"\||thank you for your support|fully subscribed|privacy policy|terms (?:of use|and conditions|& conditions)"
    r"|all rights reserved|cookie|read more|learn more|click here|sign up|log ?in|i consent to|marketing consent"
    r"|^- |leave your contacts|by submitting|buy online|buy now|promo code|launches|available for signup"
    r"|^\d+ in \d+|get in touch|it is usually detrimental|underwritten by|protection scheme|protected under"
    r"|:\s*$",
    re.I,
)
#: The tile has to actually say the product covers something. Marketing that
#: asserts nothing ("Your journey, your way") is not a benefit.
_COVER_PROSE_RE = re.compile(
    r"\b(cover(?:s|ed|age)?|protect(?:s|ed|ion)?|insured|reimburse\w*|payouts?)\b", re.I
)
#: A benefit tile is a heading and a line or two. Anything longer is a
#: different kind of section — the travel advisory runs to 163 words.
_COVER_TILE_WORDS = 60
_COVER_TILES = 6


def _cover_sentence(text: str) -> str:
    """The sentence that says what is covered, not merely the first one.

    A tile usually opens on a hook — "Don't let your medical history hold you
    back" — and states the cover in the sentence after it. The hook asserts
    nothing, and an answer built from hooks is a page of slogans.
    """
    sentences = [s.rstrip() for s in re.split(r"(?<=\.)\s+", " ".join(text.split())) if s.strip()]
    kept: list[str] = []
    for sentence in sentences:
        if ALIAS_RE.match(sentence):
            continue
        # A slogan is not a statement of cover: "Save more with longer
        # protection!" matched on "protection" and became what Home
        # Insurance covers. An exclamation, or fewer than six words, is a
        # tagline.
        if sentence.endswith("!") or len(sentence.split()) < 6:
            continue
        if _COVER_PROSE_RE.search(sentence):
            kept.append(sentence)
        # Two at most. "Why Tiq Home Insurance?" says what fire insurance
        # does *not* cover in its first sentence and what this plan covers in
        # its second; one sentence gave the customer the wrong half.
        if len(kept) == 2:
            break
    return " ".join(kept)


def _cover_summary(ordered: list[Snapshot], report: CompileReport) -> list[str]:
    """What the plan covers, in the words the product page uses.

    "Headline benefits" is generated from benefit-table rows, so it is figures
    and nothing else — and for a customer who is not signed in, every one of
    those figures is tier-varying and resolves to "depends on your plan tier".
    Asked for a summary of Tiq Travel Insurance the answer was two unknowns,
    while the product page itself said "whether it's a GP, specialist, or TCM,
    you're covered" and five more like it. Those tiles were parsed, and then
    never read by anything.

    Ordinary rule 2 applies, and it costs real lines here: "be covered for
    travel delays starting from just 3 hours" carries a number no table row
    binds — the schedule says six — so it is dropped rather than published.
    A contested figure in marketing prose is exactly what rule 2 is for.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for snapshot in ordered:
        for section in snapshot.sections:
            # The extractor leaves zero-width spaces in headings, and one of
            # them sat between "Home Insurance" and its question mark.
            heading = section.heading.replace("\u200b", "").strip()
            if not heading:
                continue
            if _COVER_END_RE.search(heading):
                break
            # `_OVERVIEW_SKIP_RE` is the existing list of headings that do not
            # describe the product, and it has to apply here too: without it
            # the maid page's "You might also be interested in" cross-sell
            # block — a teaser for a life-protection article — was compiled in
            # as what maid insurance covers.
            if _COVER_SKIP_RE.search(heading) or _OVERVIEW_SKIP_RE.search(heading):
                continue
            # A question heading is a FAQ. "Why Tiq Home Insurance?" was
            # allowed for a while: its one usable sentence says what *fire*
            # insurance does not cover, and the wording's sections of cover
            # now say what this plan does, which is the better answer.
            if heading.endswith("?"):
                continue
            if len(section.text.split()) > _COVER_TILE_WORDS:
                continue
            if not _COVER_PROSE_RE.search(section.text):
                continue
            key = slugify(heading)
            if key in seen:
                continue
            sentence = _cover_sentence(section.text)
            if not sentence or _COVER_NOISE_RE.search(sentence):
                continue
            grounded = _grounded(
                _normalise_brands(f"{heading}: {sentence}"), snapshot.ref(section.anchor), report
            )
            if not grounded:
                continue
            seen.add(key)
            lines.append(grounded)
            if len(lines) == _COVER_TILES:
                return lines
    return lines


#: A cover-page heading that names a section of cover: "Section 1 - Building",
#: "Section I - Your Car", "5.1 Death Benefit", "2. Personal Accident Benefits".
_COVER_SECTION_HEADING_RE = re.compile(
    r"^(?:section\s+[\divx]+\s*[-\u2013:]\s*|\d+(?:\.\d+)*\.?\s+|\([a-z]\)\s*)(.+)$", re.I
)
#: A numbered heading that is a benefit, not a clause of administration.
_COVER_WORD_RE = re.compile(
    r"benefit|cover|protection|liabilit|expense|loss|damage|death|illness|disabilit|allowance|income"
    r"|accident|assistance|baggage|delay|cancellation|valuables|money|cash|renovation|building|contents",
    re.I,
)
#: Headings that group or summarise rather than name cover.
_COVER_GROUP_HEADING_RE = re.compile(
    r"^(summary of cover|policy benefits|other benefits|optional benefits|scale of benefits|what is covered"
    r"|sum insured|geographical|basis of settlement|definitions|general conditions|premium)",
    re.I,
)
_COVER_LIST_MAX = 12


def cover_sections_from_documents(cover_markdown: str) -> tuple[list[str], str]:
    """The sections of cover a policy wording sets out, and the ref to cite.

    The product page says what a product is in the site's words; the wording
    says what it covers, section by section, and on most products the wording
    is the only place that list exists — the tiq.com.sg home page has no
    benefit tiles, only "Why Tiq Home Insurance?" and a promotion. The cover
    page is compiled from the wording already; this reads its headings back.
    """
    names: list[str] = []
    seen: set[str] = set()
    ref = ""
    for line in cover_markdown.splitlines():
        if not ref:
            match = SOURCE_REF_RE.search(line)
            if match and match.group(1).startswith(
                ("raw/wordings/", "raw/product-summaries/", "raw/brochures/")
            ):
                ref = match.group(1)
        if not line.startswith("## "):
            continue
        heading = line[3:].strip()
        if _COVER_GROUP_HEADING_RE.match(heading):
            continue
        match = _COVER_SECTION_HEADING_RE.match(heading)
        if not match:
            continue
        name = match.group(1).strip().rstrip(".:")
        # "5 What is Covered?" is a group heading with a number in front.
        if _COVER_GROUP_HEADING_RE.match(name):
            continue
        numbered = not heading.lower().startswith("section")
        if numbered and not _COVER_WORD_RE.search(name):
            continue
        if NUMBER_IN_PROSE_RE.search(name) or len(name.split()) > 8:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) == _COVER_LIST_MAX:
            break
    return names, ref


def augment_cover_from_documents(config: CompileConfig, report: CompileReport) -> int:
    """Add the wording's sections of cover to every product page that has a
    compiled cover page. Runs after every page is written; edits the product
    page in place. Returns how many pages were augmented."""
    wiki = config.dest_root / "wiki" / "product"
    augmented = 0
    for cover in sorted(wiki.glob("*/*/cover.md")):
        product = cover.parent.with_suffix(".md")
        if not product.exists():
            continue
        names, ref = cover_sections_from_documents(cover.read_text())
        if len(names) < 3 or not ref:
            continue
        line = f"The policy wording sets out cover under: {'; '.join(names)} [src:{ref}]."
        text = product.read_text()
        if line in text:
            continue
        if "\n## What it covers\n" in text:
            head, rest = text.split("\n## What it covers\n", 1)
            body, _, tail = rest.partition("\n## ")
            body = body.rstrip() + "\n\n" + line + "\n"
            text = head + "\n## What it covers\n" + body + ("\n## " + tail if tail else "")
        else:
            marker: str | None = next(
                (m for m in ("\n## Headline benefits\n", "\n## What is not covered\n") if m in text), None
            )
            if marker is None:
                continue
            text = text.replace(marker, "\n## What it covers\n\n" + line + "\n" + marker, 1)
        product.write_text(text)
        augmented += 1
    if augmented:
        report.skip(f"product pages given the wording's sections of cover: {augmented}")
    return augmented


def _sentences(text: str) -> list[str]:
    text = _drop_repeated_opening(" ".join(text.split()))
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


#: Words a product name shares with every other product name.
_NAME_STOPWORDS = frozenset(
    [
        "insurance",
        "plan",
        "policy",
        "etiqa",
        "direct",
        "personal",
        "claim",
        "discount",
        "cover",
        "coverage",
        "protection",
        "insure",
    ]
)


def _describes_plan(sentence: str, names: set[str]) -> bool:
    """A sentence that says what the plan is: eight words or more, no page
    furniture, not shouted, and either a cover verb or the product's own
    name in it."""
    if len(sentence.split()) < 8 or sentence.endswith("!") or ALIAS_RE.match(sentence):
        return False
    letters = [c for c in sentence if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.5:
        return False
    if _COVER_NOISE_RE.search(sentence) or NUMBER_IN_PROSE_RE.search(sentence):
        return False
    lower = sentence.lower()
    if _COVER_PROSE_RE.search(sentence):
        return True
    return any(name in lower for name in names)


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
    # The shopfront's own name for the product. The canonical title is the
    # underwriter's — "Travel Insurance" — and the prose folds Tiq into Etiqa
    # by design (§B.1); but customers type the name on the site they bought
    # from, and "tiq travel" named nothing while "tiq travel covid" named the
    # add-on. The flagship was the one product unreachable by its own name.
    # Only name-shaped titles: an SEO sentence ("Motorcycle Insurance with up
    # to $500,000 coverage") is not a name.
    # The catalogue's names are trusted verbatim; a crawled title only when
    # it is name-shaped.
    for alias in group.names if group.entry is not None else []:
        aliases.append(alias.lower())
    for shop in [" ".join(s.title.split()) for s in ordered] + (
        [] if group.entry is not None else list(group.names)
    ):
        if not shop or len(shop.split()) > 5 or re.search(r"[.,$|]", shop):
            continue
        aliases.append(shop.lower())
        bare = re.sub(r"\s+(?:insurance|plan)$", "", shop, flags=re.I).lower()
        if bare != shop.lower() and len(bare.split()) >= 2:
            aliases.append(bare)
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
        phone = _hotline([s.text for s in snaps])
        landings = list(dict.fromkeys(s.url for s in snaps))
        channels.append(
            ChannelBinding(
                ref=channel_id,
                name=name,
                purchase=purchase,
                landing=landings[0],
                hotline=phone,
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
        lifecycle=(
            Lifecycle.closed_to_new_business
            if group.entry is not None and group.entry.legacy
            else Lifecycle.on_sale
        ),
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

    # What the plan *is*, in the site's own words. The intro is where that
    # usually lives, but a product page often opens with a promo banner — Term
    # Life opens "Get up to S$300 cashback on annual premium!" — and rule 2
    # drops it for the unbound figure. Falling through to the first section
    # that says something is the difference between a page that describes the
    # product and a page whose only content is a note about channels.
    body: list[str] = ["## What this plan is"]
    # Every listing's intro first, in authority order, then every listing's
    # sections. The etiqa.com.sg listing outranks the tiq.com.sg one, and on
    # CashSaver and Tiq Invest its intro is a marketing-consent checkbox —
    # "I consent to Etiqa and its related, its agents…" — which became what
    # the plan is. A consent form, a navigation bar, a closure notice, a
    # bullet or a slogan is never the opening line; the next listing's is.
    # Positive selection, not a blacklist. Every listing opens on something
    # else first — a promotion ("Get Apple Watch Ultra 3 with min."), a
    # consent checkbox, "This policy is underwritten by", a renewal
    # reminder — and excluding each shape by name never converged. A
    # sentence describes the plan when it is long enough to say something
    # and either uses a cover verb or names the product.
    names = {
        w for n in (group.title, *group.names) for w in re.findall(r"[a-z]{5,}", n.lower())
    } - _NAME_STOPWORDS
    intro = None
    for snapshot in ordered:
        candidates = [snapshot.intro] + [
            p
            for s in snapshot.sections
            if s.heading and not _OVERVIEW_SKIP_RE.search(s.heading)
            for p in s.paragraphs
        ]
        anchors = ["body"] + [
            s.anchor
            for s in snapshot.sections
            if s.heading and not _OVERVIEW_SKIP_RE.search(s.heading)
            for _ in s.paragraphs
        ]
        for text, anchor in zip(candidates, anchors):
            for sentence in _sentences(text):
                if not _describes_plan(sentence, names):
                    continue
                intro = _grounded(_normalise_brands(sentence), snapshot.ref(anchor), report)
                if intro:
                    break
            if intro:
                break
        if intro:
            break
    if intro:
        body.append(intro)
        # The channel note used to be appended here as a qualifier, on 104
        # pages. An emission-time guard cannot make a sentence subordinate:
        # retrieval selects spans, not paragraphs-in-context, so it surfaced
        # on its own — "Being a sandwich generation can be stressful, and
        # cover, limits and exclusions are identical on every channel since a
        # channel is a route to market rather than a separate product" was a
        # real answer to "which plan is best for my family". It is a fact about
        # distribution, it belongs on the channel pages, and `channel/*.md`
        # already carries it.
    else:
        report.skip("no publishable description of the product on any host")

    # Before the figures: the figures are tier-varying and say nothing at all
    # to a customer who has not signed in.
    covers = _cover_summary(ordered, report)
    if covers:
        body.append("## What it covers")
        body += covers

    if rows:
        body.append("## Headline benefits")
        seen: set[str] = set()
        for row in rows:
            if row.benefit_code in seen:
                continue
            seen.add(row.benefit_code)
            phrase = benefit_phrase(row.benefit_code, row.attribute)
            # A schedule row headed "Adult aged below 70 years old" or "Sum
            # insured effective 16 August 2024" names a *band*, not a benefit,
            # and turning it into a sentence types its digits into prose. The
            # row keeps its place in the benefit table — only the generated
            # sentence goes, because there is no wording of it that is both
            # faithful and number-free.
            if NUMBER_IN_PROSE_RE.search(phrase):
                report.skip("benefit label carries a figure — no headline sentence")
                continue
            body.append(f"{phrase} {{{{table:{row.benefit_code}.{row.attribute}}}}} [src:{row.source_ref}].")
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
        body.append(f"## {_heading(f'{label[:1].upper()}{label[1:]}')}")
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
        body.append(f"## {_heading(item)}")
        grounded = _grounded(f"{item} are excluded under this policy", ref, report)
        if grounded:
            body.append(grounded)
    if not body:
        # Nothing to publish. The sentence still has to be one a customer can
        # be shown, because retrieval will find this page and the composer
        # will read from it: "could not be extracted" is an engineering note,
        # and it was reaching people.
        body = [
            "## Exclusions",
            "The exclusions for this product are set out in its policy wording, which is "
            f"{UNCOMPILED_MARK} [src:{ordered[0].ref()}].",
        ]
    _write(config, _page(fm, body), report)


# --- published FAQs ---------------------------------------------------------

#: Words that identify the seller rather than the product. "Tiq Travel
#: Insurance" and "Travel" are the same thing on two front doors, which is the
#: whole premise of the channel model, so they have to normalise together.
_FAQ_NOISE = re.compile(r"\b(tiq|etiqa|eprotect|insurance|plan|cover|singapore|the)\b", re.I)


def _faq_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _FAQ_NOISE.sub(" ", text.lower()))


def _match_faq_product(name: str, index: dict[str, str], slugs: list[str]) -> str | None:
    """Which compiled product a published FAQ set belongs to.

    Exact on the normalised name, then a single distinctive token — "ePROTECT
    maid" and "Tiq Maid" are one product under two brands, and `maid` is enough
    to say so. The token rule requires exactly one candidate: "Dash PET" shares
    `pet` with `pet-insurance` but is a different product sold through a partner
    app, and attaching its answers to Pet Insurance would be worse than leaving
    them out.
    """
    key = _faq_key(name)
    if key in index:
        return index[key]
    tokens = [t for t in re.findall(r"[a-z0-9]{4,}", _FAQ_NOISE.sub(" ", name.lower()))]
    for token in tokens:
        hits = [s for s in slugs if token in s]
        if len(hits) == 1:
            return hits[0]
    return None


def emit_faqs(
    config: CompileConfig,
    groups: dict[str, ProductGroup],
    page_ids: dict[str, str],
    report: CompileReport,
) -> list[str]:
    """Published question/answer pairs, one page per product.

    These are the only questions in the corpus a customer actually asked. They
    are also the only ones that arrive with the insurer's own answer attached,
    which makes them the sole source here with real ground truth.

    Each question becomes its own `##` section, because that is the unit the
    retriever scores — a customer asking "can I apply if I am over 70" should
    match the published heading that says almost exactly that.

    Numbers in these answers stay prose. The compiler already drops a prose
    figure with no table row behind it, and that is the right treatment: a
    policy wording owns limits and a marketing FAQ does not, however
    conveniently it states one.
    """
    # `source_root` is the bundle root, so raw/ is explicit here — the FAQ
    # index sits beside the web snapshots the rest of the compiler reads.
    source = config.source_root / "raw" / "faq" / "faq-pairs.json"
    if not source.exists():
        return []
    try:
        pairs = json.loads(source.read_text())
    except json.JSONDecodeError:
        report.skip("faq index is not readable JSON")
        return []

    # Two slugs can normalise to one key — this corpus carries both `travel`
    # and `travel-insurance`, a thin landing page and the real product. First
    # writer wins would attach 36 published answers to whichever sorted first,
    # and the FAQ would sit on a page no customer question retrieves. So the
    # richer page wins: more source text is the one the crawl actually found
    # something on.
    index: dict[str, str] = {}
    for slug, group in sorted(groups.items(), key=lambda kv: -len(kv[1].text)):
        index.setdefault(_faq_key(slug), slug)
        index.setdefault(_faq_key(group.title), slug)
        for name in group.names:
            index.setdefault(_faq_key(name), slug)
    slugs = sorted(groups, key=lambda s: -len(groups[s].text))

    by_product: dict[str, list[dict[str, str]]] = {}
    unmatched: dict[str, int] = {}
    for pair in pairs:
        matched = _match_faq_product(pair.get("product", ""), index, slugs)
        if matched is None:
            unmatched[pair.get("product", "?")] = unmatched.get(pair.get("product", "?"), 0) + 1
            continue
        by_product.setdefault(matched, []).append(pair)

    for name, count in sorted(unmatched.items(), key=lambda kv: -kv[1]):
        report.skip(f"published FAQ for {name!r} matches no compiled product ({count} pairs)")

    written: list[str] = []
    for slug, group in sorted(groups.items()):
        entries = by_product.get(slug)
        page_id = page_ids.get(slug)
        if not entries or not page_id:
            continue
        seen: set[str] = set()
        body: list[str] = []
        intents: list[str] = []
        for entry in entries:
            question = (entry.get("question") or "").strip()
            answer = (entry.get("answer") or "").strip()
            if not question or not answer or question.lower() in seen:
                continue
            seen.add(question.lower())
            intent = classify(question).value
            if intent not in intents:
                intents.append(intent)
            ref = f"raw/faq/{slugify(entry.get('product', ''))}.md"
            # The insurer's own published answer, reproduced rather than
            # rewritten — so it goes through the same verbatim path as a
            # policy wording. Published answers quote figures and hotlines
            # freely; on this corpus that was 318 of the bundle's 320 lint
            # errors, every one of them a number the compiler had typed into
            # prose it did not own.
            rendered = [
                line
                for paragraph in re.split(r"\n\s*\n", answer)
                # A published FAQ answers "Yes, within the free-look period."
                # in five words; the document minimum exists to drop
                # extraction debris, which a curated answer is not.
                if (line := _verbatim(paragraph, ref, report, min_words=2)) is not None
            ]
            if not rendered:
                continue
            body += [f"## {question}", "", *rendered, ""]
        if not body:
            continue
        fm = _common(
            config,
            f"{page_id}/faq",
            f"{group.title} — Published FAQs",
            PageType.product,
            [f"raw/faq/{slugify(entries[0].get('product', ''))}.md"],
            lifecycle=Lifecycle.on_sale,
            underwriter=LEGAL_NAME,
            line_of_business=line_of_business(group.slug, group.title),
            aliases=[f"{group.slug} faq", f"{group.slug} questions", f"common questions about {group.slug}"],
            effective_from=config.today,
            # Recorded so the intents this page answers are visible without
            # reading it — a wording owns limits, and this owns the questions a
            # wording never addresses.
            faq_intents=sorted(intents),
            confidence=Confidence.medium,
        )
        _write(config, _page(fm, body), report)
        written.append(f"{page_id}/faq")
    return written


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
            body.append(f"## {_heading(section.heading)}")
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
        phone = _hotline([s.text for s in snaps])
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
            hotline=phone,
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
    paragraph = _BUTTON_LABEL_RE.sub(" ", paragraph)
    for sentence in re.split(r"(?<=\.)\s+", " ".join(paragraph.split())):
        cleaned = DATE_CLAUSE_RE.sub("", sentence)
        cleaned = DATE_RE.sub("", cleaned).strip()
        cleaned = re.sub(r"\s+([.,])", r"\1", cleaned).rstrip(".").strip()
        if TRAILING_PREPOSITION_RE.search(cleaned) or len(cleaned.split()) < 4:
            continue
        kept.append(f"{cleaned}.")
    return " ".join(kept)


_PROMO_NOISE_WORDS = frozenset(
    [
        "tiq",
        "etiqa",
        "insurance",
        "plan",
        "policy",
        "promo",
        "promotion",
        "promotions",
        "offer",
        "offers",
        "off",
        "discount",
        "new",
        "customers",
        "get",
    ]
)


def _promotion_product(heading: str, products: dict[str, str]) -> str | None:
    """The product an offer is for, read from the offer's own heading.

    "Tiq Travel Insurance" and "Tiq Travel Promo 45% off" are both about
    Travel Insurance; "6 weeks of surprises" is about nothing in particular.
    A product matches when every word of its name, brand and category word
    aside, appears in the heading; the longest such name wins.
    """
    words = set(re.findall(r"[a-z]+", heading.lower())) - _PROMO_NOISE_WORDS
    best: tuple[int, str] | None = None
    for slug, title in products.items():
        name = set(re.findall(r"[a-z]+", title.lower())) - _PROMO_NOISE_WORDS
        if not name or not name <= words or max(len(w) for w in name) < 4:
            continue
        if best is None or len(name) > best[0]:
            best = (len(name), slug)
    return best[1] if best else None


def emit_promotions(
    config: CompileConfig,
    snapshots: list[Snapshot],
    report: CompileReport,
    products: dict[str, str] | None = None,
) -> list[str]:
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
            # The product the offer is for, so the retrieval focus keeps the
            # page when the customer asks "is there a promo for travel
            # insurance". Without it every promotion keyed to its own slug,
            # the focus filter dropped them all, and the offer question was
            # answered from a policy clause about other insurance.
            product_key = _promotion_product(section.heading, products or {})
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
                **({"product_key": product_key} if product_key else {}),
            )
            _write(config, _page(fm, lines), report)
            emitted.append(page_id)
            if product_key is None:
                report.skip("promotion names no product — kept, unattached")
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
        body.append(f"## Products — {_heading(lob)}")
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


# --- the document tiers -----------------------------------------------------

#: Role → (page suffix, page title, alias templates). The suffix is the graph
#: handle a question traverses to, so it names the *question*, not the
#: insurer's section title: a customer asks what is excluded, never what
#: "Section 7 (b)" says.
DOC_PAGES: dict[str, tuple[str, str, list[str]]] = {
    "exclusions": (
        "exclusions",
        "Exclusions",
        ["{slug} exclusions", "what is not covered by {slug}", "{slug} not covered"],
    ),
    "definitions": (
        "definitions",
        "Definitions",
        ["{slug} definitions", "what does it mean in {slug}", "{slug} glossary"],
    ),
    "benefits": (
        "cover",
        "What is covered",
        ["what does {slug} cover", "{slug} coverage", "{slug} benefits explained"],
    ),
    "claims": (
        "claims",
        "Making a claim",
        ["how to claim on {slug}", "{slug} claim procedure", "{slug} claim"],
    ),
    "eligibility": (
        "eligibility",
        "Eligibility",
        ["who can buy {slug}", "{slug} eligibility", "{slug} entry age"],
    ),
    "conditions": (
        "conditions",
        "Policy conditions",
        ["{slug} policy conditions", "{slug} terms", "{slug} free look", "cancel {slug}"],
    ),
}

#: A page is a reading unit, not an archive. Past this many paragraphs the
#: page stops being retrievable — every section scores the same and the
#: composer picks arbitrarily — so the compiler truncates and *says so* in the
#: report rather than quietly serving the first half of a contract.
DOC_PARAGRAPH_CAP = 60
DOC_MIN_WORDS = 6

#: Two or more bare enumerators in a row, at either end of a paragraph.
#: Two, because a lone `(30)` is a figure inside a clause, not a label.
_ENUM_RUN = r"(?:\s*\(?[a-z0-9]{1,2}[).]\s*){2,}"
_DANGLING_ENUM_RE = re.compile(rf"^{_ENUM_RUN}|{_ENUM_RUN}$", re.I)


def _published_sections(config: CompileConfig, page_id: str) -> tuple[list[str], list[str]]:
    """What an earlier pass of this compile wrote to `page_id`, if anything.

    Read back rather than threaded through, because the web-derived page is
    produced product by product and the document pass runs over a different
    grouping entirely. The placeholder is dropped: a page that says the
    exclusions are not compiled has nothing to preserve.
    """
    path = config.dest_root / "wiki" / f"{page_id}.md"
    if not path.is_file():
        return [], []
    try:
        page = parse_page(path.read_text(encoding="utf-8"))
    except ValueError:
        return [], []
    if UNCOMPILED_MARK in page.body or not page.body.strip():
        return [], []
    return [page.body.strip()], list(page.frontmatter.authority)


def _verbatim(text: str, locator: str, report: CompileReport, min_words: int = DOC_MIN_WORDS) -> str | None:
    """One paragraph of a published source, ready to publish.

    Two shapes, and which one applies is decided by the text, not by taste:

    * no figures — ordinary compiled prose carrying its reference, linted like
      any other claim;
    * figures — a **verbatim quotation**. The contract's numbers cannot be
      paraphrased away without changing what was agreed, and they cannot be
      lifted into a benefit table either (a notice period is not a benefit).
      Quoting is the honest third option: the wiki reproduces the clause and
      names the document and page it came from, and the numeric-binding gate
      re-reads that document to confirm the figure is really there.
    """
    # A leading `#` run is extraction noise, not content: the raw line was a
    # heading the segmenter did not accept, and quoting it verbatim puts
    # `> ## Exclusions applicable to Section 10` inside a paragraph. The
    # composer then binds that paragraph to a claim, and the claim reads
    # "This coverage is effective only: ## Exclusions applicable to…" — which
    # the entailment judge calls a contradiction, correctly. 22 of these on
    # the real bundle.
    text = re.sub(r"^#{1,6}\s+", "", text.strip())
    text = _normalise_brands(" ".join(text.split()).strip())
    # A schedule reads "does not cover" on one line and "(a)", "(b)", "(c)" on
    # the next three; rebuilding the paragraph glues the labels together and
    # leaves their contents on lines of their own. The stranded run says
    # nothing, so it goes — and if that is all the paragraph was, so does it.
    text = _DANGLING_ENUM_RE.sub("", text).strip()
    if len(text.split()) < min_words:
        return None
    # Contact details and deep links vary by distribution route; the renderer
    # substitutes the session's own. Baking one into a product page is the
    # merge-over-flattening failure the linter's bare-route rule exists for.
    if ROUTE_RE.search(text.replace(LEGAL_NAME, "")):
        report.skip("document paragraph carrying a channel-varying contact — dropped")
        return None
    if NUMBER_IN_PROSE_RE.search(text):
        return f"> {text} [src:{locator}]"
    return f"{text.rstrip('.')} [src:{locator}]."


def _document_body(
    documents: list[Document], role: str, report: CompileReport
) -> tuple[list[str], list[str]]:
    """(body lines, source refs) for one role across a product's documents."""
    body: list[str] = []
    refs: list[str] = []
    seen: set[str] = set()
    kept = 0
    dropped = 0

    for document in documents:
        for section in document.by_role(role):
            locator = document.locator(section)
            rendered: list[str] = []
            for paragraph in section.paragraphs:
                key = re.sub(r"[^a-z0-9]+", "", paragraph.lower())[:180]
                if not key or key in seen:
                    continue
                line = _verbatim(paragraph, locator, report)
                if line is None:
                    continue
                seen.add(key)
                if kept + len(rendered) >= DOC_PARAGRAPH_CAP:
                    dropped += 1
                    continue
                rendered.append(line)
            if not rendered:
                continue
            body.append(f"## {_heading(section.heading)}")
            body.extend(rendered)
            kept += len(rendered)
            if document.ref not in refs:
                refs.append(document.ref)
    if dropped:
        report.skip(f"paragraphs past the {DOC_PARAGRAPH_CAP}-paragraph page cap — not compiled")
    return body, refs


def emit_document_pages(
    config: CompileConfig,
    page_id: str,
    title: str,
    slug: str,
    lob: str,
    documents: list[Document],
    report: CompileReport,
    concepts: list[str],
    version: str | None = None,
    skip_roles: frozenset[str] = frozenset(),
) -> list[tuple[str, str]]:
    """One page per role the documents actually cover, hung off `page_id`."""
    emitted: list[tuple[str, str]] = []
    # Deliberately not `report.pages`: the crawl already wrote a placeholder
    # exclusions page under this id, and replacing it with the contract's own
    # exclusions is the point of this pass, not a collision to avoid.
    written: set[str] = set()
    words = slug.replace("-", " ")
    for role in COMPILED_ROLES:
        if role in skip_roles:
            continue
        suffix, role_title, alias_forms = DOC_PAGES[role]
        body, refs = _document_body(documents, role, report)
        if not body:
            continue
        # The crawl may already have written this page — an exclusions page
        # summarised from the product's own marketing copy. The wording
        # outranks it (§D.1) but does not refute it, so the crawled sections
        # are kept *below* the contract's, still carrying their own refs.
        # Composition reads from the top, so authority becomes page order.
        carried, carried_refs = _published_sections(config, f"{page_id}/{suffix}")
        body.extend(carried)
        refs.extend(r for r in carried_refs if r not in refs)
        child_id = f"{page_id}/{suffix}"
        if child_id in written:
            continue
        written.add(child_id)
        fm = _common(
            config,
            child_id,
            f"{title} — {role_title}",
            PageType.product,
            refs,
            lifecycle=Lifecycle.on_sale,
            underwriter=LEGAL_NAME,
            line_of_business=lob,
            aliases=[a.format(slug=words) for a in alias_forms],
            links=Links(concepts=concepts),
            # The contract is the source; nothing outranks it.
            confidence=Confidence.high,
            **({"version_in_force": version} if version else {}),
        )
        _write(config, _page(fm, body), report)
        emitted.append((child_id, f"{title} — {role_title}"))
    return emitted


def _document_title(documents: list[Document], plan: str) -> str:
    """A product name a customer would recognise, from the file name.

    The document's own first line is not it: a policy contract opens with
    "This is a group insurance policy issued to...", and a product summary
    opens with a disclaimer.
    """
    words = plan.replace("-", " ").split()
    keep = {"ci", "pa", "ii", "iii", "sme", "hdb", "tcm", "ilp", "gio"}
    titled = " ".join(w.upper() if w in keep else w.capitalize() for w in words)
    if not re.search(r"insurance|policy|plan|rider|cover|waiver|package", titled, re.I):
        titled = f"{titled} Insurance"
    return titled


def emit_document_products(
    config: CompileConfig,
    documents: list[Document],
    report: CompileReport,
    concepts: list[str],
    named: dict[str, tuple[str, list[Document], Entry | None]] | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """Products that exist only as a PDF.

    A third of this insurer's book — commercial fire, contractors' all risks,
    fidelity guarantee, the rider range — has a policy wording and a product
    summary but no crawlable product page. Before this, asking about any of
    them retrieved nothing and the bot said so. The contract is a better
    source than the marketing page anyway; the only thing missing is a
    purchase route, and there is no honest way to invent one.
    """
    # Grouped by identity, not by plan name: the wording is filed as
    # `heart-and-neurological-disorder-rider` and the product summary as
    # `tiq-product-summary-heart-and-neurological-disorder-rider`, and those
    # are one rider. The plainer plan name — no brand prefix, then shorter —
    # becomes the slug.
    by_identity: dict[str, list[Document]] = {}
    for document in documents:
        by_identity.setdefault(product_identity(document.plan.replace("-", " ")) or document.plan, []).append(
            document
        )
    by_plan: dict[str, list[Document]] = {}
    for found in by_identity.values():
        plans = sorted(
            {d.plan for d in found},
            key=lambda p: (bool(_BRAND_PREFIX_RE.match(p.replace("-", " "))), len(p), p),
        )
        if len(plans) > 1:
            report.skip(
                f"same document product under two names — {', '.join(plans[1:])} folded into {plans[0]}"
            )
        by_plan[plans[0]] = found
    titles: dict[str, str] = {}
    entries: dict[str, Entry | None] = {}
    for slug, (title, found, entry) in (named or {}).items():
        if found:
            by_plan[slug] = list(found)
            titles[slug] = title
            entries[slug] = entry

    products: dict[str, list[tuple[str, str]]] = {}
    for plan, found in sorted(by_plan.items()):
        found.sort(key=lambda d: (TIERS.index(d.tier), -sum(s.words for s in d.sections)))
        title = titles.get(plan) or _document_title(found, plan)
        entry = entries.get(plan)
        lob = line_of_business(plan, title)
        page_id = f"product/{lob}/{plan}"
        if page_id in report.pages:
            continue
        summary, refs = _document_body(found, "benefits", report)
        if not summary:
            # A wording with no cover section is still a product. Etiqa
            # Homeowners Enhanced has exclusions, conditions and a basis of
            # settlement and never says "what is covered" in so many words;
            # requiring one dropped the product from the bundle. Open with the
            # first compiled role that has content, and say what it is.
            for role in ("conditions", "exclusions", "claims", "eligibility", "definitions"):
                summary, refs = _document_body(found, role, report)
                if summary:
                    report.skip(f"document product opened from its {role} section — no cover section")
                    break
        if not summary:
            report.skip("document product with no compilable section at all — no page")
            continue
        linked = [c for c in concepts if _concept_pattern(c).search(" ".join(summary))]
        fm = _common(
            config,
            page_id,
            title,
            PageType.product,
            refs or [d.ref for d in found],
            lifecycle=Lifecycle.closed_to_new_business
            if entry is not None and entry.legacy
            else Lifecycle.on_sale,
            underwriter=LEGAL_NAME,
            uen=UEN,
            line_of_business=lob,
            aliases=sorted(
                {
                    plan.replace("-", " "),
                    title.lower(),
                    *([a.lower() for a in entry.aliases] if entry else []),
                }
            ),
            links=Links(concepts=linked),
            # No crawled page means no marketing claim to cross-check against,
            # and no channel binding either. High authority, low corroboration.
            confidence=Confidence.medium,
        )
        # No compiled-from preamble. It described the build rather than the
        # product, promised "the sections below" that live on other pages, and
        # on 50 products it was the whole answer: asked simply "insurance", the
        # bot replied "This product is compiled from its policy documents. The
        # wording is the contract; the sections below quote it."
        _write(config, _page(fm, [f"## About {title}", *summary]), report)
        products.setdefault(lob, []).append((page_id, title))
        products[lob].extend(
            emit_document_pages(
                config,
                page_id,
                title,
                plan,
                lob,
                found,
                report,
                linked,
                skip_roles=frozenset({"benefits"}),
            )
        )
    return products


def _clear_compiled_pages(config: CompileConfig) -> None:
    """Remove pages a previous compile wrote, before this one writes its own.

    The wiki is a build output and a product can move: fixing the
    line-of-business rules moved Marine Cargo from `motor` to `general`, and
    the old file stayed where it was — approved, retrievable, and absent from
    the index the same run rewrote. The bundle then failed its own linter.

    Only pages carrying `compiled_from_commit` are removed. A hand-authored
    bundle — the seed wiki is written by people, not compiled — has none, so
    pointing the compiler at one cannot delete anybody's work.
    """
    wiki = config.dest_root / "wiki"
    if not wiki.is_dir():
        return
    for path in wiki.rglob("*.md"):
        try:
            page = parse_page(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if page.frontmatter.compiled_from_commit:
            path.unlink()


def compile_bundle(config: CompileConfig) -> CompileReport:
    report = CompileReport()
    snapshots = load_snapshots(config.source_root)
    if not snapshots:
        return report

    # Before anything is written, not after: the concept pages are emitted a
    # few lines below, and clearing later deleted them and left 198 broken
    # links behind.
    _clear_compiled_pages(config)

    hosts = config.authority_hosts or rank_hosts(
        sorted({s.host for s in snapshots}),
        [h for h in sorted({s.host for s in snapshots}) if "etiqa" in h],
    )
    catalogue = load_catalogue(config.source_root)
    groups = group_products(snapshots, report, catalogue)
    if catalogue is not None:
        report.skip(f"catalogue: {len(catalogue.entries)} products listed by the owner")
    # Sentences for the concept and channel pages are chosen from the
    # product's own pages. A blog post defined "excess" before this, and a
    # blog post is not the insurer's statement of anything.
    supporting = [s for s in snapshots if s.page_type in PRODUCT_PAGE_TYPES]
    versions = versions_from_documents(config.source_root)
    concepts = emit_concepts(config, supporting, report)

    products: dict[str, list[tuple[str, str]]] = {}
    journeys: list[tuple[str, str]] = []
    product_page_ids: dict[str, str] = {}

    # Benefit tables are build output keyed by product. A product that stops
    # producing rows — because its "table" turned out to be a blog comparison
    # grid — must stop having a CSV, or the bundle keeps loading figures no
    # source still supports. Measured: a compile reporting 12 products and 151
    # rows loaded as 19 products and 233 rows, and the extra 82 were read as
    # current by every gate and every eval.
    tables_dir = config.dest_root / "raw" / "benefit-tables"
    if tables_dir.exists():
        for stale in tables_dir.glob("*.csv"):
            stale.unlink()

    for slug in sorted(groups):
        group = groups[slug]
        if not group.product:
            # A catalogue entry whose page was not crawled, or whose only page
            # is a shared category page: built from its documents below.
            continue
        version = versions.get(slug, str(config.today.year))
        group_hosts = rank_hosts(group.hosts, hosts)
        rows = benefit_rows(group, version, group_hosts, report)
        linked = [c for c in concepts if _concept_pattern(c).search(group.text)]

        if rows:
            write_benefit_table(config.dest_root, slug, rows)
            report.tables[slug] = len(rows)

        page_id = emit_product(config, group, version, group_hosts, rows, linked, report)
        lob = page_id.split("/")[1]
        product_page_ids[slug] = page_id
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

    # The document tiers, after the web-derived pages they attach to. A
    # wording outranks every website (§D.1 authority order), so where both
    # exist the document decides — but it decides on *its own page*, cited to
    # the PDF and the printed page, rather than by overwriting crawled prose
    # and losing the provenance that made it trustworthy.
    documents = load_documents(config.source_root)
    for _ in campaign_documents(config.source_root):
        report.skip("campaign paperwork the ingest filed as a wording — not a product")
    if catalogue is not None:
        # The catalogue says which documents are whose. A document matching
        # no entry is reported by name and compiled as nothing.
        matched: dict[str, list[Document]] = {}
        unmatched: list[Document] = []
        for document in documents:
            claimants = catalogue.entries_for_document(document.plan)
            if not claimants:
                unmatched.append(document)
                continue
            for claimant in claimants:
                matched.setdefault(claimant.slug, []).append(document)
        for document in unmatched:
            report.skip(f"document matches no catalogue product — not compiled: {document.plan}")
        unmatched = []
    else:
        matched, unmatched = match_documents(documents, sorted(groups))
    # The identity rule from `merge_duplicate_groups`, on the wordings path:
    # a document whose plan name is a web product's title once the brand and
    # the category word are taken off belongs to that product. `ELASTIQ` on
    # the web and `elastiq` in the wording were two products before this.
    by_identity = {product_identity(groups[s].title or s): s for s in groups}
    still_unmatched: list[Document] = []
    for document in unmatched:
        owner = by_identity.get(product_identity(document.plan.replace("-", " ")))
        if owner is None:
            still_unmatched.append(document)
        else:
            matched.setdefault(owner, []).append(document)
            report.skip(f"document matched a product by identity — {document.plan} → {owner}")
    unmatched = still_unmatched
    report.documents = len(documents)
    for slug, found in sorted(matched.items()):
        product_page = product_page_ids.get(slug)
        if product_page is None:
            continue
        group = groups[slug]
        lob = product_page.split("/")[1]
        linked = [c for c in concepts if _concept_pattern(c).search(group.text)]
        # The crawled exclusions page already exists, and on this corpus it
        # says the exclusions could not be extracted. The wording is where
        # they actually live, so that role is compiled here in preference.
        for child_id, child_title in emit_document_pages(
            config,
            product_page,
            group.title,
            slug,
            lob,
            found,
            report,
            linked,
            version=versions.get(slug, str(config.today.year)),
        ):
            listing = products.setdefault(lob, [])
            if (child_id, child_title) not in listing:
                listing.append((child_id, child_title))
    for lob, pages in emit_document_products(config, unmatched, report, concepts).items():
        products.setdefault(lob, []).extend(pages)
    if catalogue is not None:
        # Entries with no crawled page: a product from its documents, under
        # the entry's own name and slug.
        named: dict[str, tuple[str, list[Document], Entry | None]] = {
            slug: (group.title, matched.get(slug, []), group.entry)
            for slug, group in groups.items()
            if not group.product and group.entry is not None
        }
        for lob, pages in emit_document_products(config, [], report, concepts, named=named).items():
            products.setdefault(lob, []).extend(pages)
            # So the FAQ pages below can hang off a document-backed product.
            for page_id, _ in pages:
                if page_id.count("/") == 2 and page_id.rsplit("/", 1)[-1] in named:
                    product_page_ids[page_id.rsplit("/", 1)[-1]] = page_id
        for slug, (_title, found, _entry) in named.items():
            if not found:
                report.skip(f"catalogue product with no page and no documents — nothing to compile: {slug}")

    # After the product pages, because a FAQ page hangs off one.
    for faq_page in emit_faqs(config, groups, product_page_ids, report):
        lob = faq_page.split("/")[1]
        title = groups[next(s for s in groups if product_page_ids.get(s) == faq_page.rsplit("/", 1)[0])].title
        products.setdefault(lob, []).append((faq_page, f"{title} — Published FAQs"))

    channels = emit_channels(config, groups, hosts, supporting, report)
    promotions = emit_promotions(config, snapshots, report, {slug: groups[slug].title for slug in groups})
    governance = next((s for s in snapshots if s.page_type == "governance"), snapshots[0])
    entity = emit_entity(config, governance.ref(), report)
    emit_index(config, products, concepts, journeys, channels, entity, promotions, report)
    write_manifest(config, hosts)
    write_conflicts(config, report)
    # After every page exists: the product page borrows the wording's
    # sections of cover, cited to the wording.
    augment_cover_from_documents(config, report)
    return report


def _concept_pattern(page_id: str) -> re.Pattern[str]:
    for concept_id, _, pattern, _ in CONCEPT_TERMS:
        if concept_id == page_id:
            return pattern
    return re.compile(re.escape(page_id.split("/")[-1]), re.I)
