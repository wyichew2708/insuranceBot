"""The Ask: one typed reading of the customer's question, made once per turn.

Every gate verifies that an answer came from the corpus. Until now nothing of
comparable rigour decided what the customer *meant*: one regex classified the
intent, another decided whether a product was named (matching titles only), a
third stripped brand words to find a family, a fourth treated "no question
word" as "just a name". Each was added for one bug; three of them broke a
neighbouring case in a single day. "tiq travel coverage" asked which of three
travel products was meant, when the intent could not have been clearer.

The Ask replaces those readers with one object built before retrieval and
carried on the trace. Retrieval, section selection, the answerability gate and
the clarify decision read its fields instead of re-deriving their own guess
from the raw string. Three sources fill it, in order, and `evidence` records
which one set each field:

1. the product-name index — titles, aliases, shopfront names — for `product`,
   `family`, and `scope` when the name accounts for the whole question;
2. the deterministic intent classifier, kept where it is confident;
3. the model, only for what the first two left unknown (`Ask.with_model`),
   and never to overrule a product the customer named.

Nothing here establishes a fact about insurance. The Ask is a reading of the
question, checkable against the question, and the gates still verify every
claim in the answer against the corpus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from okf.names import Family, ProductNameIndex, index_for, names_of, normalise

from harness.intent import Intent, classify
from okf import Bundle, Page

#: Words that add nothing to a product name. "tiq travel insurance please" is
#: still just the name.
NAME_FILLER = frozenset(
    [
        "insurance",
        "plan",
        "policy",
        "product",
        "tiq",
        "etiqa",
        "the",
        "a",
        "an",
        "my",
        "about",
        "info",
        "information",
        "details",
        "please",
        "tell",
        "me",
    ]
)

#: The open form of a coverage question: what does it cover, what are the
#: coverages, what is included. No subject of its own beyond the product.
BROAD_COVERAGE_RE = re.compile(
    # Not anchored to the start of the string. Reference resolution prepends
    # the carried topic, so by the time composition sees it the turn reads
    # "term life whats the coverages" and the interrogative is no longer
    # first — anchoring here silently disabled the steering for every
    # follow-up question, which is the case it exists for.
    r"\b(?:what(?:'?s)?|which)\b[\w\s']{0,24}?"
    r"\b(?:cover(?:s|ed|age|ages)?|include[sd]?|benefits?)\b\W*$",
    re.I,
)
#: A coverage request with no question word in it — "Tiq travel insurance
#: coverage", "maid insurance benefits". A noun phrase ending on the category.
SUMMARY_PHRASE_RE = re.compile(r"^[\w\s'&-]{0,50}\b(?:cover|coverage|benefits?|summary)\s*[.?]?\s*$", re.I)

_SECTION_RE = re.compile(r"\bsection\s+(\d+[a-z]?)\b", re.I)


def asked_benefits(bundle: Bundle, question: str) -> set[str]:
    """Benefit codes the question names — through the bundle's vocabulary
    ("suitcase" → `baggage_loss`) or a section number ("section 6" →
    `section_6`). Empty where the question names none."""
    from okf import expand_vocabulary, load_vocabulary

    asked = set(expand_vocabulary(question, load_vocabulary(bundle.root)))
    asked.update(f"section_{m.group(1).lower()}" for m in _SECTION_RE.finditer(question or ""))
    return asked


@dataclass(frozen=True)
class Ask:
    question: str
    intent: Intent = Intent.unknown
    #: Product key and page id of the product this turn is about, once known.
    product: str | None = None
    product_page: str | None = None
    #: `title` | `alias` | `history` | `model` | `flagship` | "" — how the
    #: product was identified. A product named by the customer (`title`,
    #: `alias`) is authoritative; the rest are readings that may be wrong.
    named_by: str = ""
    #: Page ids the customer's phrase could mean, where it means several.
    family: tuple[str, ...] = ()
    family_phrase: str = ""
    #: Benefit codes the question names.
    subject: frozenset[str] = frozenset()
    #: `overview` — the shape of the product; `specific` — a figure, a rule,
    #: a procedure.
    scope: str = "specific"
    #: `product` | `general_insurance` | `off_topic` | "" — from the model.
    kind: str = ""
    #: The model could not separate the products it returned.
    ambiguous: bool = False
    #: Per field: what set it. Read on the trace, never by code.
    evidence: dict[str, str] = field(default_factory=dict)
    degraded: str = ""

    @property
    def named(self) -> bool:
        """The customer named the product themselves."""
        return self.product is not None and self.named_by in ("title", "alias")

    @property
    def resolved(self) -> bool:
        return self.product is not None

    def with_model(self, product_ids: list[str], ambiguous: bool, kind: str, bundle: Bundle) -> Ask:
        """Fill what the deterministic reading left open. Never overrules a
        named product — the model selects among candidates; the customer's own
        words are not a candidate."""
        if self.named:
            return replace(self, kind=kind or self.kind, evidence={**self.evidence, "kind": "model"})
        evidence = {**self.evidence, "kind": "model"}
        if not product_ids:
            return replace(self, kind=kind, evidence=evidence)
        if ambiguous or len(product_ids) > 1:
            evidence["family"] = "model"
            return replace(
                self, family=tuple(product_ids), ambiguous=True, kind=kind or "product", evidence=evidence
            )
        page = bundle.get(product_ids[0])
        if page is None:
            return replace(self, kind=kind, evidence=evidence, degraded="model id did not resolve")
        evidence["product"] = "model"
        return replace(
            self,
            product=bundle.product_key(page),
            product_page=page.id,
            named_by="model",
            kind=kind or "product",
            evidence=evidence,
        )

    def carried_from(self, other: Ask) -> Ask:
        """A product read from the conversation's earlier turns, where this
        turn named none."""
        if self.resolved or not other.resolved:
            return self
        return replace(
            self,
            product=other.product,
            product_page=other.product_page,
            named_by="history",
            evidence={**self.evidence, "product": "history"},
        )

    def as_trace(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "intent": self.intent.value,
            "scope": self.scope,
        }
        if self.product:
            out["product"] = self.product
            out["named_by"] = self.named_by
        if self.family:
            out["family"] = list(self.family)
        if self.subject:
            out["subject"] = sorted(self.subject)
        if self.kind:
            out["kind"] = self.kind
        if self.ambiguous:
            out["ambiguous"] = True
        if self.degraded:
            out["degraded"] = self.degraded
        return out


def _scope(question: str, intent: Intent, bare: bool) -> str:
    if bare:
        return "overview"
    if intent is Intent.coverage and (
        BROAD_COVERAGE_RE.search(question) or SUMMARY_PHRASE_RE.search(question)
    ):
        return "overview"
    return "specific"


def read_ask(bundle: Bundle, question: str) -> Ask:
    """The deterministic reading: names, category, intent, subject, scope."""
    index: ProductNameIndex = index_for(bundle)
    intent = classify(question)
    evidence: dict[str, str] = {"intent": "classifier"}
    subject = frozenset(asked_benefits(bundle, question))
    if subject:
        evidence["subject"] = "vocabulary"

    named = index.named(question)
    if len(named) == 1:
        hit = named[0]
        bare = index.bare(hit.page_id, question, NAME_FILLER)
        evidence["product"] = hit.kind
        if bare:
            evidence["scope"] = "bare name"
        return Ask(
            question=question,
            intent=intent,
            product=hit.key,
            product_page=hit.page_id,
            named_by=hit.kind,
            subject=subject,
            scope=_scope(question, intent, bare),
            kind="product",
            evidence=evidence,
        )
    if len(named) >= 2:
        # Two full names in one question. The customer really did name two
        # products; ask which, rather than pick.
        evidence["family"] = "two names"
        return Ask(
            question=question,
            intent=intent,
            family=tuple(n.page_id for n in named),
            family_phrase=" / ".join(n.phrase for n in named),
            subject=subject,
            scope=_scope(question, intent, False),
            kind="product",
            ambiguous=True,
            evidence=evidence,
        )

    family: Family | None = index.family(question, bundle=bundle)
    if family is not None:
        evidence["family"] = "category phrase"
        if family.flagship is not None:
            page = bundle.get(family.flagship)
            evidence["product"] = "flagship"
            return Ask(
                question=question,
                intent=intent,
                product=bundle.product_key(page) if page else None,
                product_page=family.flagship,
                named_by="flagship",
                family=family.members,
                family_phrase=family.phrase,
                subject=subject,
                scope=_scope(question, intent, False),
                kind="product",
                evidence=evidence,
            )
        return Ask(
            question=question,
            intent=intent,
            family=family.members,
            family_phrase=family.phrase,
            subject=subject,
            scope=_scope(question, intent, False),
            kind="product",
            ambiguous=True,
            evidence=evidence,
        )

    return Ask(
        question=question,
        intent=intent,
        subject=subject,
        scope=_scope(question, intent, False),
        evidence=evidence,
    )


def ask_about(question: str, product: Page | None, benefits: set[str] | None = None) -> Ask:
    """The light reading, for a caller that has a product page and no bundle:
    intent, scope, and whether the question is the product's bare name."""
    intent = classify(question)
    bare = False
    if product is not None:
        joined = normalise(question)
        for phrase in names_of(product):
            if f" {phrase} " in f" {joined} ":
                residue = f" {joined} ".replace(f" {phrase} ", " ", 1).split()
                bare = all(w in NAME_FILLER for w in residue)
                break
    return Ask(
        question=question,
        intent=intent,
        product_page=product.id if product else None,
        subject=frozenset(benefits or ()),
        scope=_scope(question, intent, bare),
    )
