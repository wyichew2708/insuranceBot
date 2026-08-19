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
import re
from dataclasses import dataclass

from api.sor import FIXTURE_POLICIES, policy_for
from okf.linter import CHANNEL_VARIANT_RE
from okf.tables import TOKEN_RE

from evalgen.schema import Category, Expectation, GeneratedCase, MergeCase, SessionSpec, Suite
from okf import Bundle, Page, PageType

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
    """One case per (benefit-table row x phrasing). These are the cases that
    prove numbers are fetched rather than generated."""
    cases: list[GeneratedCase] = []
    products = {bundle.product_key(p): p for p in _product_pages(bundle)}

    for row in sorted(bundle.tables.rows, key=lambda r: r.row_id):
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
            channel="channel/tiq-sg",
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
                )
            )
            continue

        template = ATTRIBUTE_QUESTION.get(row.attribute, "What is the {benefit} {attribute} on {product}?")
        question = template.format(
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
        cases.append(
            GeneratedCase(
                id=f"fig-{_slug(row.row_id)}",
                question=question,
                category=Category.figure,
                generated_from=row.row_id,
                session=session,
                expect=expect,
            )
        )

        # A paraphrase through an authored alias, testing entity resolution
        # rather than bag-of-words luck. Rotating through the list keeps both
        # brands' vocabulary in play instead of always using the first alias.
        aliases = product_page.frontmatter.aliases
        if aliases:
            alias = aliases[len(cases) % len(aliases)]
            cases.append(
                GeneratedCase(
                    id=f"fig-alias-{_slug(row.row_id)}",
                    question=(f"For my {alias}, how much is the {benefit} {attribute_label(row.attribute)}?"),
                    category=Category.alias,
                    generated_from=f"{row.row_id} via alias {alias!r}",
                    session=session,
                    expect=expect.model_copy(),
                    paraphrase_of=f"fig-{_slug(row.row_id)}",
                )
            )
    return cases


def _any_policy(product: str, version: str):  # type: ignore[no-untyped-def]
    for summary in FIXTURE_POLICIES.values():
        if summary.product_id.rsplit("/", 1)[-1] == product and summary.version == version:
            return summary
    return None


def alias_coverage_cases(bundle: Bundle) -> list[GeneratedCase]:
    """One question per authored alias, so no alias goes untested. Scored
    through the retrieval metrics rather than a brittle citation assertion,
    because several aliases legitimately resolve to a sub-page."""
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.pages.values(), key=lambda p: p.id):
        if page.frontmatter.type not in {PageType.product, PageType.concept}:
            continue
        for alias in page.frontmatter.aliases:
            cases.append(
                GeneratedCase(
                    id=f"ali-{_slug(page.id)}-{_slug(alias)}",
                    question=f"Tell me about {alias}.",
                    category=Category.alias,
                    generated_from=f"{page.id} alias {alias!r}",
                    session=SessionSpec(auth_level="L0"),
                    expect=Expectation(expect_delivered=True, relevant_pages=[page.id]),
                )
            )
    return cases


def exclusion_cases(bundle: Bundle) -> list[GeneratedCase]:
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.pages.values(), key=lambda p: p.id):
        if not page.id.endswith("/exclusions"):
            continue
        product_id = page.id.rsplit("/", 1)[0]
        product = bundle.get(product_id)
        title = product.frontmatter.title if product else page.frontmatter.title
        for heading, _body in _sections(page):
            cases.append(
                GeneratedCase(
                    id=f"exc-{_slug(page.id)}-{_slug(heading)}",
                    question=f"Are {heading.lower()} covered under {title}?",
                    category=Category.exclusion,
                    generated_from=f"{page.id}#{heading}",
                    session=SessionSpec(auth_level="L0"),
                    expect=Expectation(
                        must_cite=[page.id],
                        expect_delivered=True,
                        relevant_pages=[page.id],
                    ),
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
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.by_type(PageType.journey), key=lambda p: p.id):
        title = page.frontmatter.title
        cases.append(
            GeneratedCase(
                id=f"jrn-{_slug(page.id)}",
                question=f"{title}: what are the steps?",
                category=Category.journey,
                generated_from=page.id,
                session=SessionSpec(auth_level="L0"),
                expect=Expectation(must_cite=[page.id], expect_delivered=True, relevant_pages=[page.id]),
            )
        )
    return cases


def coverage_cases(bundle: Bundle) -> list[GeneratedCase]:
    cases: list[GeneratedCase] = []
    for page in _product_pages(bundle):
        title = page.frontmatter.title
        exclusions = page.frontmatter.links.exclusions
        cases.append(
            GeneratedCase(
                id=f"cov-{_slug(page.id)}",
                question=f"What does {title} cover?",
                category=Category.coverage,
                generated_from=page.id,
                session=SessionSpec(auth_level="L0"),
                expect=Expectation(
                    expect_delivered=True,
                    relevant_pages=[page.id] + ([exclusions] if exclusions else []),
                ),
            )
        )
    return cases


def promotion_cases(bundle: Bundle, today: dt.date) -> list[GeneratedCase]:
    """Live promotions must be quotable; expired ones must not be. Derived from
    the effective windows themselves, so the suite tracks the calendar."""
    cases: list[GeneratedCase] = []
    for page in sorted(bundle.by_type(PageType.promotion), key=lambda p: p.id):
        numbers = re.findall(r"\d+(?:\.\d+)?\s?%|S?\$\s?\d[\d,]*", page.body)
        live = page.frontmatter.is_effective_on(today)
        title = page.frontmatter.title
        expect = Expectation(expect_delivered=True)
        if live:
            expect.must_contain = numbers[:1]
        else:
            expect.must_not_contain = numbers[:1]
        cases.append(
            GeneratedCase(
                id=f"promo-{_slug(page.id)}",
                question=(
                    "Is there a travel promotion running right now?"
                    if live
                    else f"What was the discount on the {title.split('—')[-1].strip()} offer?"
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
    cases: list[GeneratedCase] = []
    for page in _product_pages(bundle):
        cases.append(
            GeneratedCase(
                id=f"adv-{_slug(page.id)}",
                question=f"Which {page.frontmatter.title} plan should I buy for my family?",
                category=Category.advice,
                generated_from=page.id,
                session=SessionSpec(auth_level="L0"),
                expect=Expectation(expect_advice_flag=True),
            )
        )
    return cases


def conflict_cases(bundle_root, bundle: Bundle) -> list[GeneratedCase]:  # type: ignore[no-untyped-def]
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


def generate(bundle: Bundle, bundle_root, today: dt.date | None = None) -> Suite:  # type: ignore[no-untyped-def]
    today = today or dt.date.today()
    index = TransclusionIndex.build(bundle)

    groups = {
        "figure": figure_cases(bundle, index),
        "alias_coverage": alias_coverage_cases(bundle),
        "exclusion": exclusion_cases(bundle),
        "concept": concept_cases(bundle),
        "journey": journey_cases(bundle),
        "coverage": coverage_cases(bundle),
        "promotion": promotion_cases(bundle, today),
        "entitlement": entitlement_cases(bundle),
        "advice": advice_cases(bundle),
        "conflict": conflict_cases(bundle_root, bundle),
    }
    cases = [case for group in groups.values() for case in group]
    merges = merge_cases(bundle, index)

    stats = {name: len(group) for name, group in groups.items()}
    stats["merge"] = len(merges)
    by_category: dict[str, int] = {}
    for case in cases:
        by_category[case.category.value] = by_category.get(case.category.value, 0) + 1
    stats.update({f"category:{k}": v for k, v in sorted(by_category.items())})

    return Suite(
        name="auto-faq",
        bundle=str(bundle_root),
        generated_at=today.isoformat(),
        cases=cases,
        merge_cases=merges,
        stats=stats,
    )
