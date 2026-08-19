"""Contracts for generated evaluation cases.

A generated case carries not just an expected answer but the *evidence* it
should rest on — the page that must be cited and the benefit-table row the
figure must bind to. That is what lets the scorer separate "right answer" from
"right answer for the right reason", and what makes retrieval measurable
independently of composition.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, Field


class Category(str, Enum):
    figure = "figure"  # a number that must come from a table row
    coverage = "coverage"  # what a product covers
    exclusion = "exclusion"  # what it does not
    concept = "concept"  # free-look, excess, pre-existing…
    journey = "journey"  # buy / claim / service
    alias = "alias"  # entity resolution via authored aliases
    merge = "merge"  # same question, both brand framings
    promotion = "promotion"  # effective-dated offers
    staleness = "staleness"  # answers that changed at a known date
    entitlement = "entitlement"  # customer data without authentication
    advice = "advice"  # regulated-advice boundary
    conflict = "conflict"  # a source disagreement used as bait
    historic = "historic"  # customer on a superseded version
    channel = "channel"  # contact details and purchase route per brand
    entity = "entity"  # who underwrites the product
    faq = "faq"  # a question the website itself publishes
    out_of_scope = "out_of_scope"  # nothing in the corpus answers this


class SessionSpec(BaseModel):
    channel: str = "channel/tiq-sg"
    auth_level: str = "L0"
    policy_id: str | None = None
    today: dt.date | None = None


class Expectation(BaseModel):
    must_cite: list[str] = Field(default_factory=list)
    expect_row_ids: list[str] = Field(default_factory=list)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    expect_delivered: bool | None = None
    expect_handoff: bool | None = None
    expect_advice_flag: bool | None = None
    expect_rag: bool | None = None
    expect_gate_fail: list[str] = Field(default_factory=list)
    # Pages the retriever should surface; scored separately from the answer so
    # a retrieval gap is distinguishable from a composition gap (§G Loop 4).
    relevant_pages: list[str] = Field(default_factory=list)


class GeneratedCase(BaseModel):
    id: str
    question: str
    category: Category
    generated_from: str
    session: SessionSpec = Field(default_factory=SessionSpec)
    expect: Expectation = Field(default_factory=Expectation)
    paraphrase_of: str | None = None


class MergeCase(BaseModel):
    """One question asked in each brand framing. Facts must match exactly and
    only the deep link may differ (§B.1)."""

    id: str
    question: str
    category: Category = Category.merge
    generated_from: str
    channels: list[str]
    policy_id: str | None = None


class Suite(BaseModel):
    name: str
    bundle: str
    generated_at: str
    cases: list[GeneratedCase] = Field(default_factory=list)
    merge_cases: list[MergeCase] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.cases) + len(self.merge_cases)
