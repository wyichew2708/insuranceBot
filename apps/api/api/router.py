"""The router, in three layers, decided once and written down.

The pipeline already routed. Smalltalk, the entity question, the directory,
a medical emergency, an off-topic turn, the five out-of-corpus intents, an
advice request, an ambiguous product — each had a branch in `_answer_turn`,
in an order that mattered, and nothing recorded which branch a turn took.
The evaluation could say a turn failed and could not say which decision
failed it.

This module makes the decision explicit and layered:

    Layer 1 — what kind of turn        smalltalk · emergency · off_topic ·
                                       account_state · advice · browse ·
                                       entity · product
    Layer 2 — which product, how surely named · carried · ambiguous · guessed · none
    Layer 3 — which handler            coverage · exclusions · limits · claims ·
                                       eligibility · conditions · documents ·
                                       price · offer · definition · application ·
                                       general

and it makes one thing true that was not: **a guess is asked about, not
answered.** `Ask.named_by == "flagship"` means the customer named a category
— "travel insurance", four products — and the code picked the one whose
title matched. That is a reading, not the customer's word, and the product
owner's rule is that an unsure reading asks. So layer 2 `guessed` asks which,
listing the category's members, the same way `ambiguous` always did.

Deterministic and lexical throughout, for the reason `classify` is: this
decides whether a customer is answered, asked, or handed on, and that
decision must be reproducible, free, and identical on every machine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from harness.ask import Ask
from harness.gates import ADVICE_SEEKING_RE
from harness.intent import OUT_OF_CORPUS, Intent, smalltalk_kind

from api.guardrails import medical_emergency
from okf import Bundle, Scope


class Layer1(str, Enum):
    smalltalk = "smalltalk"
    emergency = "emergency"
    off_topic = "off_topic"
    account_state = "account_state"
    advice = "advice"
    browse = "browse"
    entity = "entity"
    product = "product"


class Layer2(str, Enum):
    named = "named"  # the customer said the product's name or alias
    carried = "carried"  # read from an earlier turn of this conversation
    ambiguous = "ambiguous"  # several candidates, nothing separates them
    guessed = "guessed"  # a category's flagship, or the model's pick
    inferred = "inferred"  # unnamed, but only one product in the corpus can answer
    none = "none"  # no product at all
    n_a = "n/a"  # layer 1 settled the turn without one


class Layer3(str, Enum):
    coverage = "coverage"
    exclusions = "exclusions"
    limits = "limits"
    claims = "claims"
    eligibility = "eligibility"
    conditions = "conditions"
    documents = "documents"
    price = "price"
    offer = "offer"
    definition = "definition"
    application = "application"
    general = "general"
    n_a = "n/a"


#: Intent → the handler that owns it. Anything not here is `general`.
HANDLER_FOR: dict[Intent, Layer3] = {
    Intent.coverage: Layer3.coverage,
    Intent.exclusion: Layer3.exclusions,
    Intent.limit: Layer3.limits,
    Intent.claim: Layer3.claims,
    Intent.eligibility: Layer3.eligibility,
    Intent.renewal: Layer3.conditions,
    Intent.document: Layer3.documents,
    Intent.price: Layer3.price,
    Intent.offer: Layer3.offer,
    Intent.definition: Layer3.definition,
    Intent.application: Layer3.application,
}

#: Handlers whose answer depends on which product — so a turn that reaches
#: one with no product must ask. `definition` is not here: "what is an
#: excess" is answered from the concept pages whatever the product. `price`,
#: `offer` and `documents` are not here either: with no product those owe a
#: handoff with a destination, and asking first would trade a correct handoff
#: for a question.
PRODUCT_SPECIFIC: frozenset[Layer3] = frozenset(
    {
        Layer3.coverage,
        Layer3.exclusions,
        Layer3.limits,
        Layer3.claims,
        Layer3.eligibility,
        Layer3.conditions,
        Layer3.application,
    }
)

#: Shopping phrased as a need rather than a question. "I need life insurance"
#: names a line, not a product; it was read as a two-product family and asked
#: which, when the right reply is the directory of that line.
NEED_RE = re.compile(
    r"\bi(?:'m| am)? (?:need|want|would like|looking for|am looking for|after)\b"
    r"[\w\s]{0,20}\b(?:insurance|cover(?:age)?|plan|policy|protection)\b",
    re.I,
)


@dataclass(frozen=True)
class Decision:
    layer1: Layer1
    layer2: Layer2
    layer3: Layer3
    #: The product key the turn is about, where layer 2 settled on one.
    product: str | None = None
    #: Candidate product page ids, where layer 2 could not settle.
    options: tuple[str, ...] = ()
    #: Why — one short phrase, for the trace.
    reason: str = ""
    scope: Scope = field(default_factory=Scope.open)

    @property
    def clarify(self) -> bool:
        """Ask the customer which product, before anything is retrieved.

        Only where the reading itself is unsure: two candidates, or a guess.
        A turn that named no product is not yet unsure — the corpus may be
        able to settle it, and `needs_product` says the pipeline must find
        out before it answers.
        """
        return self.layer1 is Layer1.product and self.layer2 in (Layer2.ambiguous, Layer2.guessed)

    @property
    def needs_product(self) -> bool:
        """The handler's answer depends on the product and none was named.

        "What is the overseas medical expenses limit?" names no product and
        is not unsure: exactly one product carries that benefit. "How do I
        make a claim?" names no product and ties eighty-seven. The lexical
        layer tells them apart, and the pipeline asks only in the second
        case — after it has looked, not before.
        """
        return (
            self.layer1 is Layer1.product and self.layer2 is Layer2.none and self.layer3 in PRODUCT_SPECIFIC
        )

    def inferred(self, product: str) -> Decision:
        """The corpus settled an unnamed product on exactly one candidate."""
        return Decision(
            self.layer1,
            Layer2.inferred,
            self.layer3,
            product=product,
            reason=f"only {product} can answer",
            scope=Scope.for_product(product),
        )

    def as_trace(self) -> dict[str, str]:
        return {
            "layer1": self.layer1.value,
            "layer2": self.layer2.value,
            "layer3": self.layer3.value,
            "product": self.product or "",
            "scope": self.scope.describe(),
            "reason": self.reason,
        }


def _layer1(ask: Ask, question: str) -> tuple[Layer1, str]:
    if smalltalk_kind(question):
        return Layer1.smalltalk, "pleasantry"
    if medical_emergency(question):
        return Layer1.emergency, "medical emergency"
    if ask.kind == "off_topic":
        return Layer1.off_topic, "not about insurance"
    if ask.intent in OUT_OF_CORPUS:
        return Layer1.account_state, f"{ask.intent.value}: no document has this"
    if ADVICE_SEEKING_RE.search(question):
        return Layer1.advice, "asks for a recommendation"
    if ask.intent is Intent.entity:
        return Layer1.entity, "who underwrites"
    if ask.intent is Intent.browse:
        return Layer1.browse, "shopping"
    if not ask.named and NEED_RE.search(question):
        return Layer1.browse, "a need, not a product"
    return Layer1.product, ""


def _layer2(ask: Ask) -> tuple[Layer2, str | None, tuple[str, ...], str]:
    if ask.named:
        return Layer2.named, ask.product, (), f"named by {ask.named_by}"
    if ask.named_by == "history" and ask.resolved:
        return Layer2.carried, ask.product, (), "carried from an earlier turn"
    if ask.ambiguous and ask.family:
        return Layer2.ambiguous, None, tuple(ask.family), ask.family_phrase or "several candidates"
    if ask.resolved and ask.named_by in ("flagship", "model"):
        options = tuple(ask.family) if ask.family else ((ask.product_page,) if ask.product_page else ())
        return Layer2.guessed, ask.product, options, f"{ask.named_by} of {ask.family_phrase or 'a category'}"
    if ask.family:
        return Layer2.ambiguous, None, tuple(ask.family), ask.family_phrase or "a category"
    return Layer2.none, None, (), "no product named"


def route(bundle: Bundle, ask: Ask, question: str) -> Decision:
    """The three-layer decision for this turn."""
    layer1, why1 = _layer1(ask, question)
    if layer1 is not Layer1.product:
        # Layer 1 settled it. A product may still be known — an account-state
        # question about a named plan routes with that plan's page — but no
        # retrieval scope is needed because nothing is retrieved.
        product = ask.product if ask.resolved else None
        return Decision(layer1, Layer2.n_a, Layer3.n_a, product=product, reason=why1)
    layer2, product, options, why2 = _layer2(ask)
    layer3 = HANDLER_FOR.get(ask.intent, Layer3.general)
    # Retrieval is scoped to a product the customer named or that the
    # conversation carries. A guess is asked about instead of scoped to, and
    # an open turn reads the whole corpus, as before.
    scope = Scope.for_product(product) if layer2 in (Layer2.named, Layer2.carried) else Scope.open()
    return Decision(layer1, layer2, layer3, product=product, options=options, reason=why2, scope=scope)
