"""Auto-generate FAQ pairs from the corpus (Loop 3, but sourced rather than
hand-written).

Every case is derived from something already in the bundle — a benefit-table
row, an exclusion section, a concept page, an authored alias, an effective
window, a detected source conflict. Two consequences follow:

1. The suite grows with the corpus. Publish fifty new product pages and the
   generator produces their questions without anyone writing YAML.
2. Coverage is measurable. Any table row or page that produces no question, or
   that no question ever reaches, is a gap the report names explicitly.

Where a vLLM endpoint is configured the phrasings can be widened by the model;
the deterministic templates below are the offline path and the floor.
"""

from __future__ import annotations

import datetime as dt
import itertools
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from api.sor import FIXTURE_POLICIES, policy_for
from okf.linter import CHANNEL_VARIANT_RE, SOURCE_REF_RE
from okf.tables import TOKEN_RE, TableRow

from evalgen.schema import Category, Expectation, GeneratedCase, MergeCase, SessionSpec, Suite
from evalgen.surfaces import (
    BRAND_CONFUSION,
    GAP_PROBES,
    FigureFact,
    Surface,
    advice_surfaces,
    alias_surfaces,
    channel_surfaces,
    concept_surfaces,
    coverage_surfaces,
    entity_surfaces,
    exclusion_surfaces,
    figure_surfaces,
    gap_surfaces,
    section_surfaces,
)
from okf import UNCOMPILED_MARK, Bundle, Page, PageType, brand_for_host, spec_for

# Attribute phrasing. The generic underscore-to-space rendering reads badly for
# a few of these, so they get an explicit label and question form.
ATTRIBUTE_LABEL: dict[str, str] = {
    "limit": "limit",
    "cap": "benefit cap",
    "excess": "excess",
    "payout_per_block": "payout per completed block",
    "threshold_hours": "delay threshold",
    "per_item_limit": "per-item sub-limit",
    "max_percentage": "maximum percentage",
}

ATTRIBUTE_QUESTION: dict[str, str] = {
    "threshold_hours": "How long must {subject} be delayed before the {benefit} benefit applies?",
    "payout_per_block": "How much does {product} pay per completed block for {benefit}?",
    "cap": "What is the {benefit} benefit cap on {product}?",
    "per_item_limit": "What is the per-item sub-limit for {benefit} on {product}?",
}

BENEFIT_SUBJECT: dict[str, str] = {"travel_delay": "my flight"}

# Benefit codes are table keys, not customer language.
BENEFIT_LABEL: dict[str, str] = {
    "ncd": "no-claim discount",
    "own_damage": "own damage",
    "travel_delay": "travel delay",
    "medical_expenses": "overseas medical expenses",
    "baggage_loss": "baggage",
    "trip_cancellation": "trip cancellation",
    "alternative_accommodation": "alternative accommodation",
}


def benefit_label(code: str) -> str:
    return BENEFIT_LABEL.get(code, humanise(code))


def humanise(token: str) -> str:
    return token.replace("_", " ")


def attribute_label(attribute: str) -> str:
    return ATTRIBUTE_LABEL.get(attribute, humanise(attribute))


@dataclass
class TransclusionIndex:
    """Which page transcludes which (benefit, attribute), per product."""

    by_key: dict[tuple[str, str, str], list[str]]

    @classmethod
    def build(cls, bundle: Bundle) -> TransclusionIndex:
        index: dict[tuple[str, str, str], list[str]] = {}
        for page in bundle.pages.values():
            product = bundle.product_key(page)
            for match in TOKEN_RE.finditer(page.body):
                key = (product, match.group(1), match.group(2))
                index.setdefault(key, [])
                if page.id not in index[key]:
                    index[key].append(page.id)
        return cls(by_key=index)

    def pages_for(self, product: str, benefit: str, attribute: str) -> list[str]:
        return self.by_key.get((product, benefit, attribute), [])


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _sections(page: Page) -> list[tuple[str, str]]:
    body = CHANNEL_VARIANT_RE.sub("", page.body)
    matches = list(re.finditer(r"^##\s+(.+)$", body, re.M))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out.append((m.group(1).strip(), body[m.end() : end].strip()))
    return out


def _brands(page: Page) -> tuple[str, ...]:
    """Front doors this product publishes, in the words a customer would use.

    Read off the page's own channel bindings rather than the global registry,
    so a product sold on one surface only is never asked about under a name it
    does not carry.
    """
    out: list[str] = []
    for binding in page.frontmatter.channels:
        for url in binding.landings:
            host = urlparse(url).hostname if url else None
            name = brand_for_host(host) if host else None
            if name and name not in out:
                out.append(name)
    return tuple(out)


def _expand(
    *,
    base_id: str,
    surfaces: list[Surface],
    expect: Expectation,
    session: SessionSpec,
    generated_from: str,
    product: str | None,
) -> list[GeneratedCase]:
    """One fact, one expectation, many questions.

    The canonical surface keeps the bare id, so a family stays diffable against
    an earlier run even when a later surface stops applying; the rest hang off
    it as paraphrases. A loose surface drops `must_cite` — the question
    withholds the vocabulary that would make an exact citation fair — but keeps
    every figure assertion, which is the part that matters.
    """
    # Not every family has a canonical phrasing — the gap probes are a set of
    # peers, no one of which is the plain form of the others — so nothing is
    # marked a paraphrase of an id that was never emitted.
    rooted = any(s.kind == "canonical" for s in surfaces)
    cases: list[GeneratedCase] = []
    for surface in surfaces:
        this = expect.model_copy(deep=True)
        if not surface.strict:
            this.must_cite = []
        if surface.asserts:
            this.must_contain = [*this.must_contain, *surface.asserts]
        if surface.category is Category.brand:
            # Naming one front door must never come back as "which of the two?".
            this.must_not_contain = [*this.must_not_contain, *BRAND_CONFUSION]
        cases.append(
            GeneratedCase(
                id=base_id if surface.kind == "canonical" else f"{base_id}-{surface.kind}",
                question=surface.question,
                category=surface.category,
                generated_from=generated_from,
                session=session,
                expect=this,
                paraphrase_of=base_id if rooted and surface.kind != "canonical" else None,
                product=product,
                surface=surface.kind,
            )
        )
    return cases


def _paragraphs(body: str) -> list[str]:
    """Prose paragraphs with citation markers and tokens stripped.

    An exclusions page states one excluded thing per paragraph, so a paragraph
    is the unit of fact here — finer splitting would have to guess whether the
    "and" in "wear and tear" joins two items or names one.
    """
    body = CHANNEL_VARIANT_RE.sub("", body)
    body = re.sub(r"\[src:[^\]]+\]", "", body)
    body = TOKEN_RE.sub("", body)
    out: list[str] = []
    for chunk in re.split(r"\n\s*\n", body):
        text = " ".join(chunk.split())
        if text and not text.startswith(("#", "|", "<!--", "-", "*")):
            out.append(text)
    return out


def _excluded_subject(paragraph: str) -> str | None:
    """The thing a "X ... are excluded" sentence is about, as a noun phrase the
    question can be built around."""
    match = re.match(r"^(.*?)\s+(?:are|is)\s+excluded", paragraph, re.I)
    if not match:
        return None
    subject = match.group(1).strip().rstrip(",")
    if not subject or len(subject.split()) > 14:
        return None
    return subject[0].lower() + subject[1:]


def _product_pages(bundle: Bundle) -> list[Page]:
    """Canonical product pages — the shallowest id per product key."""
    seen: dict[str, Page] = {}
    for page in bundle.by_type(PageType.product):
        key = bundle.product_key(page)
        if key not in seen or page.id.count("/") < seen[key].id.count("/"):
            seen[key] = page
    return sorted(seen.values(), key=lambda p: p.id)


# --- generators, one per corpus feature -------------------------------------


def figure_cases(bundle: Bundle, index: TransclusionIndex) -> list[GeneratedCase]:
    """Every benefit-table row, asked every way it gets asked.

    One case per row proves numbers are fetched rather than generated. The
    phrasing family around it proves the fetch does not depend on the customer
    happening to use the corpus's own words — which is the failure this suite
    exists to catch, because it is invisible to a single-phrasing suite.
    """
    cases: list[GeneratedCase] = []
    products = {bundle.product_key(p): p for p in _product_pages(bundle)}

    for seed, row in enumerate(sorted(bundle.tables.rows, key=lambda r: r.row_id)):
        product_page = products.get(row.product)
        if product_page is None:
            continue
        citing = index.pages_for(row.product, row.benefit_code, row.attribute)
        current = product_page.frontmatter.version_in_force
        historic = current is not None and row.version != current

        policy = policy_for(row.product, row.version, row.tier)
        if policy is None and row.tier != "ALL":
            continue  # no fixture session holds this tier; coverage report flags it
        if policy is None:
            policy = policy_for(row.product, row.version, "ALL") or _any_policy(row.product, row.version)

        session = SessionSpec(
            channel="channel/direct",
            auth_level="L2" if policy else "L0",
            policy_id=policy.policy_id if policy else None,
        )
        benefit = benefit_label(row.benefit_code)
        title = product_page.frontmatter.title

        if historic:
            # The wiki describes what is on sale; this customer holds an older
            # version, so the turn must not be answered from the current page.
            cases.append(
                GeneratedCase(
                    id=f"fig-hist-{_slug(row.row_id)}",
                    question=f"What is the {benefit} {attribute_label(row.attribute)} on my {title}?",
                    category=Category.historic,
                    generated_from=row.row_id,
                    session=session,
                    expect=Expectation(
                        expect_delivered=False,
                        expect_rag=True,
                        expect_gate_fail=["version-coherence"],
                    ),
                    product=row.product,
                    surface="canonical",
                )
            )
            continue

        template = ATTRIBUTE_QUESTION.get(row.attribute, "What is the {benefit} {attribute} on {product}?")
        canonical = template.format(
            benefit=benefit,
            attribute=attribute_label(row.attribute),
            product=title,
            subject=BENEFIT_SUBJECT.get(row.benefit_code, "my trip"),
        )
        expect = Expectation(
            must_cite=citing[:1],
            expect_row_ids=[row.row_id],
            must_contain=[row.rendered()],
            expect_delivered=True,
            relevant_pages=citing,
        )
        fact = FigureFact(
            title=title,
            benefit_code=row.benefit_code,
            benefit=benefit,
            attribute=row.attribute,
            attribute_text=attribute_label(row.attribute),
            value=row.rendered(),
            tier=row.tier,
            canonical=canonical,
            aliases=tuple(product_page.frontmatter.aliases),
            brands=_brands(product_page),
            seed=seed,
        )
        cases.extend(
            _expand(
                base_id=f"fig-{_slug(row.row_id)}",
                surfaces=figure_surfaces(fact),
                expect=expect,
                session=session,
                generated_from=row.row_id,
                product=row.product,
            )
        )
    return cases


def _any_policy(product: str, version: str):  # type: ignore[no-untyped-def]
    for summary in FIXTURE_POLICIES.values():
        if summary.product_id.rsplit("/", 1)[-1] == product and summary.version == version:
            return summary
    return None


def alias_coverage_cases(bundle: Bundle) -> list[GeneratedCase]:
    """Every authored alias, asked three ways, so no alias goes untested and
    none is tested only in the one sentence shape it happens to suit.

    Scored through the retrieval metrics rather than a brittle citation
    assertion, because several aliases legitimately resolve to a sub-page.
    """
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.pages.values(), key=lambda p: p.id):
        if page.frontmatter.type not in {PageType.product, PageType.concept}:
            continue
        product = bundle.product_key(page) if page.frontmatter.type is PageType.product else None
        for alias in page.frontmatter.aliases:
            cases.extend(
                _expand(
                    base_id=f"ali-{_slug(page.id)}-{_slug(alias)}",
                    surfaces=alias_surfaces(alias),
                    expect=Expectation(expect_delivered=True, relevant_pages=[page.id]),
                    session=SessionSpec(auth_level="L0"),
                    generated_from=f"{page.id} alias {alias!r}",
                    product=product,
                )
            )
    return cases


def exclusion_cases(bundle: Bundle) -> list[GeneratedCase]:
    """One family per excluded thing the corpus actually states.

    The unit is a paragraph rather than a heading: an exclusions page states
    one excluded thing per paragraph, and asking once per heading tests only
    that the page is reachable, not that each exclusion in it is.
    """
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.pages.values(), key=lambda p: p.id):
        if not page.id.endswith("/exclusions"):
            continue
        product_id = page.id.rsplit("/", 1)[0]
        product = bundle.get(product_id)
        title = product.frontmatter.title if product else page.frontmatter.title
        key = bundle.product_key(product) if product else None
        brands = _brands(product) if product else ()
        expect = Expectation(must_cite=[page.id], expect_delivered=True, relevant_pages=[page.id])

        # A page the compiler could not fill is not a page to ask questions
        # about. On the real corpus 108 exclusions sections failed to extract,
        # leaving a placeholder — and the fallback below then generated "I am
        # claiming for exclusions", which is not a question anyone would ask
        # and which the bot was right to refuse.
        if UNCOMPILED_MARK in page.body:
            continue

        subjects: list[str] = []
        for paragraph in _paragraphs(page.body):
            subject = _excluded_subject(paragraph)
            if subject and subject not in subjects:
                subjects.append(subject)
        # A page that states its exclusions some other way still has to be
        # reachable, so fall back to its headings rather than emitting nothing.
        if not subjects:
            subjects = [heading.lower() for heading, _ in _sections(page)]

        for seed, subject in enumerate(subjects):
            cases.extend(
                _expand(
                    base_id=f"exc-{_slug(page.id)}-{_slug(subject)}",
                    surfaces=exclusion_surfaces(title, subject, seed, brands),
                    expect=expect,
                    session=SessionSpec(auth_level="L0"),
                    generated_from=f"{page.id} :: {subject}",
                    product=key,
                )
            )
    return cases


def concept_cases(bundle: Bundle) -> list[GeneratedCase]:
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.by_type(PageType.concept), key=lambda p: p.id):
        title = page.frontmatter.title
        cases.append(
            GeneratedCase(
                id=f"con-{_slug(page.id)}",
                question=f"What does {title.lower()} mean?",
                category=Category.concept,
                generated_from=page.id,
                expect=Expectation(must_cite=[page.id], expect_delivered=True, relevant_pages=[page.id]),
            )
        )
        for alias in page.frontmatter.aliases[:1]:
            cases.append(
                GeneratedCase(
                    id=f"con-alias-{_slug(page.id)}-{_slug(alias)}",
                    question=f"Can you explain {alias}?",
                    category=Category.alias,
                    generated_from=f"{page.id} via alias {alias!r}",
                    expect=Expectation(must_cite=[page.id], expect_delivered=True, relevant_pages=[page.id]),
                    paraphrase_of=f"con-{_slug(page.id)}",
                )
            )
    return cases


def journey_cases(bundle: Bundle) -> list[GeneratedCase]:
    """One case per journey, plus one per section it actually publishes.

    The phrasing comes from the page's own headings rather than assuming every
    journey has a "Steps" section — asking for steps of a page that documents
    "Before you buy" and "Route" tests the fixture's vocabulary, not the
    assistant's routing, and the answer lands on whichever *other* journey
    happens to use the word.
    """
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.by_type(PageType.journey), key=lambda p: p.id):
        title = page.frontmatter.title
        expect = Expectation(must_cite=[page.id], expect_delivered=True, relevant_pages=[page.id])
        cases.append(
            GeneratedCase(
                id=f"jrn-{_slug(page.id)}",
                question=f"How do I go about this: {title}?",
                category=Category.journey,
                generated_from=page.id,
                session=SessionSpec(auth_level="L0"),
                expect=expect,
            )
        )
        for heading, _ in _sections(page)[:2]:
            cases.append(
                GeneratedCase(
                    id=f"jrn-{_slug(page.id)}-{_slug(heading)}",
                    question=f"{title} — {heading.lower()}?",
                    category=Category.journey,
                    generated_from=f"{page.id}#{heading}",
                    session=SessionSpec(auth_level="L0"),
                    expect=Expectation(must_cite=[page.id], expect_delivered=True, relevant_pages=[page.id]),
                )
            )
    return cases


def coverage_cases(bundle: Bundle) -> list[GeneratedCase]:
    """The question most conversations open with, including the two brand
    forms the merge has to survive."""
    cases: list[GeneratedCase] = []
    for page in _product_pages(bundle):
        title = page.frontmatter.title
        exclusions = page.frontmatter.links.exclusions
        cases.extend(
            _expand(
                base_id=f"cov-{_slug(page.id)}",
                surfaces=coverage_surfaces(title, _brands(page)),
                expect=Expectation(
                    expect_delivered=True,
                    relevant_pages=[page.id] + ([exclusions] if exclusions else []),
                ),
                session=SessionSpec(auth_level="L0"),
                generated_from=page.id,
                product=bundle.product_key(page),
            )
        )
    return cases


def _promo_subject(page: Page) -> str:
    """What to call the offer. Taken from the page's own alias list so the
    question tracks the corpus instead of a phrasing baked in for one bundle."""
    for alias in page.frontmatter.aliases:
        if len(alias.split()) >= 2:
            return alias.lower()
    return page.frontmatter.title.split("—")[0].strip().lower()


def promotion_cases(bundle: Bundle, today: dt.date) -> list[GeneratedCase]:
    """Live promotions must be quotable; expired ones must not be. Derived from
    the effective windows themselves, so the suite tracks the calendar."""
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.by_type(PageType.promotion), key=lambda p: p.id):
        numbers = re.findall(r"\d+(?:\.\d+)?\s?%|S?\$\s?\d[\d,]*", page.body)
        live = page.frontmatter.is_effective_on(today)
        expect = Expectation(expect_delivered=True)
        if live:
            expect.must_contain = numbers[:1]
        else:
            expect.must_not_contain = numbers[:1]
        cases.append(
            GeneratedCase(
                id=f"promo-{_slug(page.id)}",
                question=(
                    f"Is the {_promo_subject(page)} still running right now?"
                    if live
                    else f"What was the discount on the {_promo_subject(page)}?"
                ),
                category=Category.promotion if live else Category.staleness,
                generated_from=page.id,
                session=SessionSpec(auth_level="L0", today=today),
                expect=expect,
            )
        )
    return cases


def entitlement_cases(bundle: Bundle) -> list[GeneratedCase]:
    """Customer-specific data must never surface without authentication."""
    cases: list[GeneratedCase] = []
    for policy in sorted(FIXTURE_POLICIES.values(), key=lambda p: p.policy_id)[:3]:
        product = bundle.get(policy.product_id)
        title = product.frontmatter.title if product else policy.product_id
        cases.append(
            GeneratedCase(
                id=f"ent-{_slug(policy.policy_id)}",
                question=f"What plan tier and policy number is on my {title}?",
                category=Category.entitlement,
                generated_from=policy.policy_id,
                session=SessionSpec(auth_level="L0", policy_id=None),
                expect=Expectation(
                    must_not_contain=[policy.policy_id]
                    + ([policy.tier] if policy.tier not in {"ALL", ""} else [])
                ),
            )
        )
    return cases


def advice_cases(bundle: Bundle) -> list[GeneratedCase]:
    """Requests for a recommendation. Every one must trip the advice boundary;
    none may be answered with a product choice, however the customer asks."""
    cases: list[GeneratedCase] = []
    for page in _product_pages(bundle):
        cases.extend(
            _expand(
                base_id=f"adv-{_slug(page.id)}",
                surfaces=advice_surfaces(page.frontmatter.title),
                expect=Expectation(expect_advice_flag=True),
                session=SessionSpec(auth_level="L0"),
                generated_from=page.id,
                product=bundle.product_key(page),
            )
        )
    return cases


def conflict_cases(bundle_root: Path, bundle: Bundle) -> list[GeneratedCase]:
    """Every detected source disagreement becomes numeric-hallucination bait:
    the customer quotes the wrong website figure and the bot must not agree."""
    from compiler.conflicts import scan

    cases: list[GeneratedCase] = []
    for conflict in scan(bundle_root):
        loser = conflict.loser
        wrong = f"{loser.value} {loser.unit}".strip()
        cases.append(
            GeneratedCase(
                id=f"cft-{_slug(conflict.slug)}",
                question=(
                    f"The website says the {benefit_label(conflict.benefit_code)} "
                    f"{attribute_label(conflict.attribute)} is {wrong} — is that right?"
                ),
                category=Category.conflict,
                generated_from=f"{loser.source_path} vs {conflict.winner.source_path}",
                session=SessionSpec(auth_level="L0"),
                expect=Expectation(must_not_contain=[wrong]),
            )
        )
    return cases


def merge_cases(bundle: Bundle, index: TransclusionIndex) -> list[MergeCase]:
    """One merge pair per multi-channel product x transcluded benefit."""
    cases: list[MergeCase] = []
    for page in _product_pages(bundle):
        channels = [c.ref for c in page.frontmatter.channels]
        if len(channels) < 2:
            continue
        product = bundle.product_key(page)
        version = page.frontmatter.version_in_force or ""
        seen: set[str] = set()
        for (prod, benefit, attribute), _pages in sorted(index.by_key.items()):
            if prod != product or benefit in seen:
                continue
            seen.add(benefit)
            policy = _any_policy(product, version)
            cases.append(
                MergeCase(
                    id=f"mrg-{_slug(product)}-{_slug(benefit)}",
                    question=f"What is the {benefit_label(benefit)} {attribute_label(attribute)}?",
                    generated_from=f"{page.id} + {benefit}",
                    channels=channels,
                    policy_id=policy.policy_id if policy else None,
                )
            )
    return cases


def channel_cases(bundle: Bundle) -> list[GeneratedCase]:
    """Contact details are a verbatim-only zone (§C.4): a hotline is either
    reproduced exactly from the channel binding or not given at all. These
    cases also reach the channel pages, which no other family cites."""
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.by_type(PageType.channel), key=lambda p: p.id):
        fm = page.frontmatter
        spec = spec_for(page.id)
        route = spec.name if spec else fm.title.split("(")[0].strip()
        hotline = getattr(fm, "hotline", None)
        other = [
            str(getattr(p.frontmatter, "hotline", "") or "")
            for p in bundle.by_type(PageType.channel)
            if p.id != page.id
        ]
        expect = Expectation(
            must_cite=[page.id],
            must_contain=[hotline] if hotline else [],
            # Another *route's* number leaking into this channel's answer is
            # the failure the channel-coherence gate exists to prevent. Routes
            # that publish the same corporate number are not in conflict.
            must_not_contain=[h for h in other if h and h != hotline],
            expect_delivered=True,
            relevant_pages=[page.id],
        )
        cases.append(
            GeneratedCase(
                id=f"chan-{_slug(page.id)}",
                question=f"How do I contact you about a policy bought through the {route} channel?",
                category=Category.channel,
                generated_from=page.id,
                session=SessionSpec(channel=page.id),
                expect=expect,
            )
        )
    return cases


def entity_cases(bundle: Bundle) -> list[GeneratedCase]:
    """One underwriter behind every route (§B.1). If the assistant cannot say
    who carries the risk, the merge story is decorative."""
    entities = sorted(bundle.by_type(PageType.entity), key=lambda p: p.id)
    if not entities:
        return []
    entity = entities[0]
    cases = [
        GeneratedCase(
            id=f"ent-{_slug(entity.id)}",
            question="Who underwrites these policies?",
            category=Category.entity,
            generated_from=entity.id,
            expect=Expectation(must_cite=[entity.id], expect_delivered=True, relevant_pages=[entity.id]),
            surface="canonical",
        )
    ]
    for page in _product_pages(bundle):
        underwriter = page.frontmatter.underwriter
        if not underwriter:
            continue
        cases.extend(
            _expand(
                base_id=f"ent-{_slug(page.id)}",
                surfaces=entity_surfaces(page.frontmatter.title),
                expect=Expectation(
                    must_contain=[underwriter],
                    expect_delivered=True,
                    relevant_pages=[page.id, entity.id],
                ),
                session=SessionSpec(auth_level="L0"),
                generated_from=page.id,
                product=bundle.product_key(page),
            )
        )
    return cases


#: Section headings that duplicate a dedicated child page.
_SECTION_CHILD = (
    (re.compile(r"not covered|exclusion", re.I), "/exclusions"),
    (re.compile(r"how to claim|making a claim", re.I), "/claims"),
    (re.compile(r"definition", re.I), "/definitions"),
)


def _has_child_for(bundle: Bundle, page: Page, heading: str) -> bool:
    return any(
        pattern.search(heading) and bundle.get(f"{page.id}{suffix}") is not None
        for pattern, suffix in _SECTION_CHILD
    )


def _is_pointer_only(body: str) -> bool:
    """Is this section nothing but a link to another page?"""
    stripped = SOURCE_REF_RE.sub("", body).strip()
    if not stripped or "](" not in stripped:
        return False
    residual = re.sub(r"\[([^\]]*)\]\([^)]*\)", " ", stripped)
    return len(residual.split()) <= 12


def section_cases(bundle: Bundle) -> list[GeneratedCase]:
    """Every section a product page publishes under its own heading.

    The corpus chose the heading, so these ask its own words back — the
    cheapest possible retrieval, and therefore the one whose failure says the
    most.
    """
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.by_type(PageType.product), key=lambda p: p.id):
        # A published-FAQ page's headings are already questions, so this family
        # would re-ask them as "what does Travel Insurance say about can I apply
        # if I am more than 70 years old" — a worse phrasing of a question the
        # FAQ evaluation already asks properly, with the insurer's own answer as
        # ground truth. Three thousand of those would drown the suite.
        if page.id.endswith("/faq"):
            continue
        title = page.frontmatter.title
        key = bundle.product_key(page)
        for heading, body in _sections(page):
            if not body.strip():
                continue
            # A section whose whole body is a cross-reference — "The complete
            # list is on the exclusions page" — is a signpost, and asking what
            # it says tests the signpost rather than the answer. The composer
            # deprioritises these for the same reason; generating a case that
            # asserts one *must* be cited pins the old behaviour in place.
            if _is_pointer_only(body):
                continue
            # A product page summarises "What is not covered" and links to the
            # exclusions page that holds it. Asking what the summary says, and
            # demanding the *parent* be cited, pins routing we deliberately
            # changed: an exclusion question is now answered from the
            # exclusions page. The child page's own cases still cover it.
            if _has_child_for(bundle, page, heading):
                continue
            cases.extend(
                _expand(
                    base_id=f"sec-{_slug(page.id)}-{_slug(heading)}",
                    surfaces=section_surfaces(title, heading),
                    expect=Expectation(
                        must_cite=[page.id],
                        expect_delivered=True,
                        relevant_pages=[page.id],
                    ),
                    session=SessionSpec(auth_level="L0"),
                    generated_from=f"{page.id}#{heading}",
                    product=key,
                )
            )
    return cases


def concept_product_cases(bundle: Bundle) -> list[GeneratedCase]:
    """A concept asked *through* a product.

    The definition lives on the concept page and the number lives in the
    product's tables, so these are the turns whose answer has to be assembled
    from two pages at once — the shape a single-page retriever gets wrong while
    scoring well everywhere else.
    """
    cases: list[GeneratedCase] = []
    for page in _product_pages(bundle):
        title = page.frontmatter.title
        key = bundle.product_key(page)
        for concept_id in page.frontmatter.links.concepts:
            concept = bundle.get(concept_id)
            if concept is None:
                continue
            cases.extend(
                _expand(
                    base_id=f"cpt-{_slug(page.id)}-{_slug(concept_id)}",
                    surfaces=concept_surfaces(title, concept.frontmatter.title),
                    expect=Expectation(
                        expect_delivered=True,
                        relevant_pages=[concept_id, page.id],
                    ),
                    session=SessionSpec(auth_level="L0"),
                    generated_from=f"{page.id} + {concept_id}",
                    product=key,
                )
            )
    return cases


def channel_product_cases(bundle: Bundle) -> list[GeneratedCase]:
    """One product down one route.

    `channel_cases` proves a route's contact details are reproduced verbatim.
    These prove the route is reachable *from a product question*, which is how
    a customer actually arrives at it — nobody opens with "tell me about your
    bancassurance channel".
    """
    cases: list[GeneratedCase] = []
    for page in _product_pages(bundle):
        title = page.frontmatter.title
        key = bundle.product_key(page)
        for binding in page.frontmatter.channels:
            spec = spec_for(binding.ref)
            route = spec.name if spec else binding.name
            cases.extend(
                _expand(
                    base_id=f"cch-{_slug(page.id)}-{_slug(binding.ref)}",
                    surfaces=channel_surfaces(title, route, binding.hotline),
                    expect=Expectation(
                        expect_delivered=True,
                        relevant_pages=[page.id, binding.ref],
                    ),
                    session=SessionSpec(channel=binding.ref, auth_level="L0"),
                    generated_from=f"{page.id} via {binding.ref}",
                    product=key,
                )
            )
    return cases


def compound_cases(bundle: Bundle, index: TransclusionIndex) -> list[GeneratedCase]:
    """Two figures in one turn.

    Every figure family asks for one number. A customer routinely asks for two,
    and an answer that binds the first correctly then improvises the second
    passes every single-figure case in the suite.
    """
    cases: list[GeneratedCase] = []
    products = {bundle.product_key(p): p for p in _product_pages(bundle)}
    by_group: dict[tuple[str, str, str], list[TableRow]] = {}
    for row in sorted(bundle.tables.rows, key=lambda r: r.row_id):
        by_group.setdefault((row.product, row.version, row.tier), []).append(row)

    for (product, version, tier), rows in sorted(by_group.items()):
        page = products.get(product)
        if page is None or page.frontmatter.version_in_force != version:
            continue
        policy = policy_for(product, version, tier) or policy_for(product, version, "ALL")
        if policy is None and tier != "ALL":
            continue
        session = SessionSpec(
            channel="channel/direct",
            auth_level="L2" if policy else "L0",
            policy_id=policy.policy_id if policy else None,
        )
        title = page.frontmatter.title
        for first, second in itertools.pairwise(rows):
            citing = index.pages_for(product, first.benefit_code, first.attribute)
            cases.append(
                GeneratedCase(
                    id=f"cmp-{_slug(first.row_id)}-{_slug(second.benefit_code + second.attribute)}",
                    question=(
                        f"For {title}, what is the {benefit_label(first.benefit_code)} "
                        f"{attribute_label(first.attribute)} and the "
                        f"{benefit_label(second.benefit_code)} {attribute_label(second.attribute)}?"
                    ),
                    category=Category.figure,
                    generated_from=f"{first.row_id} + {second.row_id}",
                    session=session,
                    expect=Expectation(
                        expect_row_ids=[first.row_id, second.row_id],
                        must_contain=[first.rendered(), second.rendered()],
                        expect_delivered=True,
                        relevant_pages=citing,
                    ),
                    product=product,
                    surface="compound",
                )
            )
    return cases


def near_miss_cases(bundle: Bundle) -> list[GeneratedCase]:
    """A benefit one product carries, asked of a product that does not.

    Out-of-scope cases about crop insurance are easy: nothing in the corpus is
    even close. These are the hard ones — the figure is in the corpus, is the
    right shape for the question, and belongs to a different product. Asserting
    the other product's number does *not* appear is how a plausible answer gets
    caught.
    """
    rows_by_product: dict[str, list[TableRow]] = {}
    for row in bundle.tables.rows:
        rows_by_product.setdefault(row.product, []).append(row)

    cases: list[GeneratedCase] = []
    for page in _product_pages(bundle):
        key = bundle.product_key(page)
        title = page.frontmatter.title
        own_codes = {r.benefit_code for r in rows_by_product.get(key, [])}
        own_values = {r.rendered() for r in rows_by_product.get(key, [])}
        seen: set[str] = set()
        # Capped per product, and the cap scales with the corpus because the
        # problem only exists on a large one. This family is cross-product by
        # construction: on 108 products it produced 10,115 of 19,159 cases —
        # over half the suite spent on a single question shape, and an accuracy
        # figure that mostly measured it. On a three-product bundle there is
        # nothing to explode, and clamping it there just starves the floor.
        budget = max(4, 24 // max(1, len(rows_by_product) - 1))
        for other, rows in sorted(rows_by_product.items()):
            if other == key:
                continue
            for row in sorted(rows, key=lambda r: r.row_id):
                if row.benefit_code in own_codes or row.benefit_code in seen:
                    continue
                # A value this product also publishes is not evidence of a
                # borrowed answer, so it cannot be asserted against.
                if row.rendered() in own_values:
                    continue
                seen.add(row.benefit_code)
                budget -= 1
                cases.append(
                    GeneratedCase(
                        id=f"near-{_slug(page.id)}-{_slug(row.benefit_code)}",
                        question=f"What is the {benefit_label(row.benefit_code)} limit on {title}?",
                        category=Category.out_of_scope,
                        generated_from=f"{page.id} asked about {row.row_id}",
                        session=SessionSpec(auth_level="L0"),
                        expect=Expectation(must_not_contain=[row.rendered()]),
                        product=key,
                        surface="near-miss",
                    )
                )
                if budget <= 0:
                    break
            if budget <= 0:
                break
    return cases


def faq_cases(bundle: Bundle) -> list[GeneratedCase]:
    """The websites already publish their own questions. Compiling them onto
    the product page means the suite can ask them back verbatim — the closest
    thing to real customer phrasing this corpus contains."""
    cases: list[GeneratedCase] = []
    for page in _product_pages(bundle):
        for match in re.finditer(r"^###\s+(.+?)\s*$", page.body, re.M):
            question = match.group(1).strip()
            if len(question.split()) < 3:
                continue
            title = page.frontmatter.title
            cases.append(
                GeneratedCase(
                    id=f"faq-{_slug(page.id)}-{_slug(question)}",
                    question=f"{question.rstrip('?')} — for {title}?",
                    category=Category.faq,
                    generated_from=f"{page.id}#{question}",
                    expect=Expectation(must_cite=[page.id], expect_delivered=True, relevant_pages=[page.id]),
                )
            )
    return cases


def out_of_scope_cases(bundle: Bundle) -> list[GeneratedCase]:
    """Products the corpus does not carry. The only acceptable answers are a
    handoff or an explicit gap — never a plausible-sounding invention."""
    known = {bundle.product_key(p) for p in _product_pages(bundle)}
    absent = [
        ("crop insurance", "What does your crop insurance cover?"),
        ("marine hull insurance", "What is the marine hull insurance excess?"),
        ("kidnap and ransom cover", "Do you sell kidnap and ransom cover?"),
    ]
    cases: list[GeneratedCase] = []
    for name, question in absent:
        if any(word in key for key in known for word in name.split()[:1]):
            continue
        cases.append(
            GeneratedCase(
                id=f"oos-{_slug(name)}",
                question=question,
                category=Category.out_of_scope,
                generated_from="(nothing in the corpus)",
                # A handoff is delivered — it is a refusal the customer sees.
                # What must not happen is a confident answer about a product
                # the corpus does not carry.
                expect=Expectation(expect_handoff=True),
            )
        )
    return cases


def _answerable(bundle: Bundle, page: Page, needs: str | None) -> str | None:
    """The page that would answer a gap probe for this product, if the bundle
    has one. Structural, so enriching the corpus flips a probe from "must hand
    off" to "must cite" without anyone editing the suite."""
    if needs is None:
        return None
    links = page.frontmatter.links
    if needs == "claims":
        return links.claims
    if needs == "free-look":
        return next((c for c in links.concepts if c.endswith("free-look")), None)
    if needs == "buy":
        key = bundle.product_key(page)
        return next(
            (
                p.id
                for p in bundle.by_type(PageType.journey)
                if p.id.startswith("journey/buy/") and key in p.id
            ),
            None,
        )
    return None


def gap_probe_cases(bundle: Bundle) -> list[GeneratedCase]:
    """The questions customers ask that the corpus may or may not answer.

    Both outcomes are asserted. Where the bundle carries the page the answer
    must cite it; where it does not, the answer must hand off rather than
    improvise — which is the harder half, because an invented renewal date or
    premium reads exactly like a good answer.
    """
    cases: list[GeneratedCase] = []
    for page in _product_pages(bundle):
        title = page.frontmatter.title
        key = bundle.product_key(page)
        for probe in GAP_PROBES:
            target = _answerable(bundle, page, probe.needs)
            expect = (
                Expectation(expect_delivered=True, relevant_pages=[target, page.id])
                if target
                else Expectation(expect_handoff=True)
            )
            cases.extend(
                _expand(
                    # Keyed on the page, not the probe: the probe key is
                    # already the surface name, and repeating it would read
                    # back as `gap-...-claim-claim`.
                    base_id=f"gap-{_slug(page.id)}",
                    surfaces=gap_surfaces(probe, title),
                    expect=expect,
                    session=SessionSpec(auth_level="L0"),
                    generated_from=f"{page.id} :: {probe.key} -> {target or '(not in corpus)'}",
                    product=key,
                )
            )
    return cases


def product_entitlement_cases(bundle: Bundle) -> list[GeneratedCase]:
    """Customer-specific questions asked without authentication, one per
    product. `entitlement_cases` covers the fixture policies; these cover
    products no fixture policy happens to be issued against."""
    cases: list[GeneratedCase] = []
    for page in _product_pages(bundle):
        title = page.frontmatter.title
        key = bundle.product_key(page)
        # Only this customer's identifiers are secret. The limits printed on
        # the public product page are not, and asserting against those would
        # score a correct answer as a breach.
        secrets = sorted(
            {
                value
                for policy in FIXTURE_POLICIES.values()
                if policy.product_id == page.id
                for value in (policy.policy_id, policy.tier)
                # Long enough to identify someone. A tier of "5" is not a
                # secret — it matches any answer containing the digit, and on
                # the real corpus that reported three entitlement leaks where
                # the answer merely mentioned a clause number.
                if value and value not in {"ALL", ""} and len(value) >= 4 and not value.isdigit()
            }
        )
        # A policy id derived from the product's own name is not a secret. On
        # the real corpus the super-suite policy id contains "super-suite", so
        # every correct answer that named the product was scored as a breach.
        haystack = f"{page.id} {title}".lower()
        secrets = [s for s in secrets if s.lower() not in haystack]
        if not secrets:
            continue
        for n, question in enumerate(
            (
                f"What is on my {title} policy right now?",
                f"Read me the plan tier and policy number on my {title}.",
            )
        ):
            cases.append(
                GeneratedCase(
                    id=f"pent-{_slug(page.id)}-{n}",
                    question=question,
                    category=Category.entitlement,
                    generated_from=f"{page.id} unauthenticated",
                    session=SessionSpec(auth_level="L0", policy_id=None),
                    expect=Expectation(must_not_contain=secrets),
                    product=key,
                    surface="unauthenticated",
                )
            )
    return cases


def per_product_counts(suite: Suite) -> dict[str, int]:
    """Cases attributable to each product.

    Cross-product cases — concept definitions, channel contact details,
    out-of-scope probes — carry no product and are deliberately not counted
    toward any product's total. Counting them would let a bundle hit a
    per-product floor without a single question about the product.
    """
    counts: dict[str, int] = {}
    for case in suite.cases:
        if case.product:
            counts[case.product] = counts.get(case.product, 0) + 1
    for merge in suite.merge_cases:
        product = merge.id.split("-")[1] if merge.id.count("-") >= 2 else None
        if product and product in counts:
            counts[product] += 1
    return counts


def per_product_facts(suite: Suite) -> dict[str, int]:
    """Distinct facts behind each product's cases.

    Reported next to the case count so the ratio is visible: a hundred
    questions drawn from forty facts is a paraphrase-robustness suite, and a
    hundred drawn from four is a paraphrase suite. Both are worth running; only
    one of them is worth calling coverage.
    """
    facts: dict[str, set[str]] = {}
    for case in suite.cases:
        if case.product:
            facts.setdefault(case.product, set()).add(case.generated_from)
    return {product: len(seen) for product, seen in facts.items()}


def generate(bundle: Bundle, bundle_root: Path, today: dt.date | None = None) -> Suite:
    today = today or dt.date.today()
    index = TransclusionIndex.build(bundle)

    groups = {
        "figure": figure_cases(bundle, index),
        "compound": compound_cases(bundle, index),
        "alias_coverage": alias_coverage_cases(bundle),
        "exclusion": exclusion_cases(bundle),
        "section": section_cases(bundle),
        "concept": concept_cases(bundle),
        "concept_product": concept_product_cases(bundle),
        "journey": journey_cases(bundle),
        "coverage": coverage_cases(bundle),
        "promotion": promotion_cases(bundle, today),
        "entitlement": entitlement_cases(bundle),
        "advice": advice_cases(bundle),
        "conflict": conflict_cases(bundle_root, bundle),
        "channel": channel_cases(bundle),
        "channel_product": channel_product_cases(bundle),
        "entity": entity_cases(bundle),
        "faq": faq_cases(bundle),
        "near_miss": near_miss_cases(bundle),
        "gap_probe": gap_probe_cases(bundle),
        "product_entitlement": product_entitlement_cases(bundle),
        "out_of_scope": out_of_scope_cases(bundle),
    }
    cases = [case for group in groups.values() for case in group]
    merges = merge_cases(bundle, index)

    # Ids collide when two facts slug to the same string; the runner keys
    # results by id, so a collision would silently drop a case from the report.
    seen: set[str] = set()
    unique: list[GeneratedCase] = []
    for case in cases:
        if case.id in seen:
            continue
        seen.add(case.id)
        unique.append(case)
    cases = unique

    stats = {name: len(group) for name, group in groups.items()}
    stats["merge"] = len(merges)
    by_category: dict[str, int] = {}
    for case in cases:
        by_category[case.category.value] = by_category.get(case.category.value, 0) + 1
    stats.update({f"category:{k}": v for k, v in sorted(by_category.items())})

    suite = Suite(
        name="auto-faq",
        bundle=str(bundle_root),
        generated_at=today.isoformat(),
        cases=cases,
        merge_cases=merges,
        stats=stats,
    )
    counts = per_product_counts(suite)
    facts = per_product_facts(suite)
    suite.stats.update({f"product:{k}": v for k, v in sorted(counts.items())})
    suite.stats.update({f"facts:{k}": v for k, v in sorted(facts.items())})
    return suite
