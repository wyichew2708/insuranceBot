"""OKF knowledge-bundle block contract (§4.1).

The CMS authors these blocks; we only validate and consume them.
Parsing is tolerant of unknown frontmatter keys (they are preserved and
the loader logs them) but strict about required fields.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

OKF_SUPPORTED_VERSIONS = {"0.2"}


class BlockType(str, Enum):
    faq = "faq"
    benefit = "benefit"
    eligibility = "eligibility"
    exclusion = "exclusion"
    procedure = "procedure"
    disclaimer = "disclaimer"
    escalation = "escalation"


class Audience(str, Enum):
    public = "public"
    policyholder = "policyholder"
    internal = "internal"


class Brand(str, Enum):
    etiqa = "etiqa"
    tiq = "tiq"


class Language(str, Enum):
    en = "en"
    ms = "ms"
    zh = "zh"


class Jurisdiction(str, Enum):
    SG = "SG"
    MY = "MY"


class Status(str, Enum):
    draft = "draft"
    in_review = "in_review"
    published = "published"
    retired = "retired"


class DistributionChannel(str, Enum):
    banca = "banca"
    ifa_ad = "ifa_ad"
    direct = "direct"
    all = "all"


class OkfFrontmatter(BaseModel):
    """YAML frontmatter of one OKF block. Unknown keys are preserved."""

    model_config = ConfigDict(extra="allow")

    okf: str
    id: str
    type: BlockType
    title: str
    product_code: str
    line: str
    audience: Audience
    brand: list[Brand]
    language: Language
    jurisdiction: Jurisdiction
    version: int
    status: Status
    effective_from: dt.date
    effective_to: dt.date | None = None
    distribution_channel: DistributionChannel | None = None
    takaful: bool = False
    source_ref: str | None = None
    action_ref: str | None = None
    channels: list[str] | None = None
    sla: str | None = None
    related: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("okf")
    @classmethod
    def _supported_okf(cls, v: str) -> str:
        if v not in OKF_SUPPORTED_VERSIONS:
            raise ValueError(f"unsupported okf version {v!r}; supported: {sorted(OKF_SUPPORTED_VERSIONS)}")
        return v

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not v or v != v.strip() or " " in v:
            raise ValueError("block id must be a non-empty slug path without spaces")
        return v

    def unknown_keys(self) -> list[str]:
        return sorted(set(self.model_extra or {}))

    def to_frontmatter_dict(self) -> dict[str, Any]:
        """Round-trippable dict: enums as values, dates as ISO, defaults kept."""
        data = self.model_dump(mode="json", exclude_none=True)
        return data


class OkfBlock(BaseModel):
    frontmatter: OkfFrontmatter
    body: str
    source_path: str | None = None


def parse_okf_markdown(text: str, source_path: str | None = None) -> OkfBlock:
    """Parse a `---` fenced YAML frontmatter markdown document into an OkfBlock."""
    if not text.startswith("---"):
        raise ValueError("missing frontmatter fence")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("unterminated frontmatter fence")
    raw = yaml.safe_load(parts[1])
    if not isinstance(raw, dict):
        raise ValueError("frontmatter is not a mapping")
    fm = OkfFrontmatter.model_validate(raw)
    return OkfBlock(frontmatter=fm, body=parts[2].lstrip("\n"), source_path=source_path)


def render_okf_markdown(block: OkfBlock) -> str:
    fm_yaml = yaml.safe_dump(block.frontmatter.to_frontmatter_dict(), sort_keys=False, allow_unicode=True)
    return f"---\n{fm_yaml}---\n\n{block.body.rstrip()}\n"
