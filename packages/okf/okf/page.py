"""OKF page model — YAML frontmatter + markdown body (design §C.2).

Frontmatter is the *pre-read filter*: the harness queries these fields to
eliminate most pages before loading a single body token (§C.2 closing note),
so every field the filter needs is typed here rather than left free-form.
"""

from __future__ import annotations

import datetime as dt
import re
from enum import Enum
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

OKF_VERSION = "0.1"


class PageType(str, Enum):
    product = "product"
    concept = "concept"
    journey = "journey"
    channel = "channel"
    entity = "entity"
    promotion = "promotion"
    # `index` would shadow str.index on a (str, Enum) member.
    index_page = "index"


class Status(str, Enum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    deprecated = "deprecated"


class Lifecycle(str, Enum):
    on_sale = "on_sale"
    closed_to_new_business = "closed_to_new_business"
    withdrawn = "withdrawn"
    not_applicable = "not_applicable"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class ChannelBinding(BaseModel):
    """A distribution-route binding hanging off a canonical product (§B.1).

    Brand is deliberately absent: every route sells the same Etiqa product, so
    a binding says *how the customer buys*, not *whose product it is*. A route
    with more than one front door lists the extras in `surfaces` — they are
    interchangeable, and citing any of them is citing this channel.
    """

    ref: str
    name: str
    purchase: str
    landing: str
    hotline: str | None = None
    #: Additional equally-valid landing URLs for the same route.
    surfaces: list[str] = Field(default_factory=list)

    @property
    def landings(self) -> list[str]:
        return [self.landing, *self.surfaces]


class Links(BaseModel):
    """Typed edges. Relationship semantics live in the prose (§C.3 rule 3);
    these are the traversal handles the harness follows deterministically."""

    model_config = ConfigDict(extra="allow")

    benefits: str | None = None
    exclusions: str | None = None
    claims: str | None = None
    concepts: list[str] = Field(default_factory=list)

    def all_refs(self) -> list[str]:
        refs: list[str] = []
        for value in (self.benefits, self.exclusions, self.claims):
            if value:
                refs.append(value)
        refs.extend(self.concepts)
        for key, value in (self.model_extra or {}).items():
            if key == "concepts":
                continue
            if isinstance(value, str):
                refs.append(value)
            elif isinstance(value, list):
                refs.extend(v for v in value if isinstance(v, str))
        return refs


class Frontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    okf_version: str = OKF_VERSION
    id: str
    title: str
    type: PageType
    status: Status = Status.draft
    lifecycle: Lifecycle = Lifecycle.not_applicable

    underwriter: str | None = None
    uen: str | None = None
    jurisdiction: str = "SG"
    line_of_business: str | None = None
    # Drives the advice-boundary gate (§F.2): advised products may not receive
    # a factual-only answer without an adviser handoff path.
    regulated_advice: bool = False

    aliases: list[str] = Field(default_factory=list)
    channels: list[ChannelBinding] = Field(default_factory=list)
    plan_tiers: list[str] = Field(default_factory=list)

    # Ordered highest-authority-first; the compiler resolves conflicts by this
    # list and files the loser to conflicts/ (§D.2).
    authority: list[str] = Field(default_factory=list)

    version_in_force: str | None = None
    effective_from: dt.date | None = None
    effective_to: dt.date | None = None

    links: Links = Field(default_factory=Links)

    compiled_from_commit: str | None = None
    compiled_at: dt.datetime | None = None
    reviewed_by: list[str] = Field(default_factory=list)
    review_due: dt.date | None = None
    confidence: Confidence = Confidence.medium

    @field_validator("id")
    @classmethod
    def _slug_path(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(?:[-/][a-z0-9]+)*", v):
            raise ValueError(f"id must be a lowercase slug path, got {v!r}")
        return v

    def is_effective_on(self, when: dt.date) -> bool:
        started = self.effective_from is None or when >= self.effective_from
        not_ended = self.effective_to is None or when <= self.effective_to
        return started and not_ended

    def is_review_overdue(self, when: dt.date) -> bool:
        """Overdue pages are auto-demoted out of wiki-first retrieval (§I:
        staleness) — a stale trusted page is worse than no page."""
        return self.review_due is not None and when > self.review_due


class Page(BaseModel):
    frontmatter: Frontmatter
    body: str
    source_path: str | None = None

    @property
    def id(self) -> str:
        return self.frontmatter.id

    def section(self, heading: str) -> str | None:
        """Body text under a `##` heading, for quoting a specific part."""
        pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", re.M | re.S)
        m = pattern.search(self.body)
        return m.group(1).strip() if m else None


FENCE = "---"


def parse_page(text: str, source_path: str | None = None) -> Page:
    if not text.startswith(FENCE):
        raise ValueError("page is missing its YAML frontmatter fence")
    parts = text.split(FENCE, 2)
    if len(parts) < 3:
        raise ValueError("unterminated frontmatter fence")
    raw = yaml.safe_load(parts[1])
    if not isinstance(raw, dict):
        raise ValueError("frontmatter is not a mapping")
    return Page(
        frontmatter=Frontmatter.model_validate(raw),
        body=parts[2].lstrip("\n"),
        source_path=source_path,
    )


def render_page(page: Page) -> str:
    data: dict[str, Any] = page.frontmatter.model_dump(mode="json", exclude_none=True)
    fm = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return f"{FENCE}\n{fm}{FENCE}\n\n{page.body.rstrip()}\n"
