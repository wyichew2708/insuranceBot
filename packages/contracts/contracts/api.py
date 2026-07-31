"""Internal API contracts (§4.3) shared by gateway, orchestrator, retrieval, evals."""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from contracts.okf import Audience, Brand, Jurisdiction, Language


class ChatRequest(BaseModel):
    session_id: str
    brand: Brand
    audience: Audience
    message: str


class ChatEventType(str, Enum):
    token = "token"
    citation = "citation"
    action = "action"
    handover = "handover"
    done = "done"


class ChatEvent(BaseModel):
    """One SSE event on the /v1/chat stream."""

    type: ChatEventType
    # token
    text: str | None = None
    # citation
    chunk_id: str | None = None
    title: str | None = None
    url: str | None = None
    # action (renderer substitutes exact values from the actions table)
    action_id: str | None = None
    # handover
    payload: dict[str, Any] | None = None
    # done
    route: str | None = None
    trace_id: str | None = None


class SearchIndex(str, Enum):
    kb = "kb"
    web = "web"


class SearchFilters(BaseModel):
    brand: Brand
    audience: Audience
    language: Language = Language.en
    jurisdiction: Jurisdiction = Jurisdiction.SG
    line: str | None = None
    product_code: str | None = None
    active_on: dt.date | None = None


class SearchRequest(BaseModel):
    query: str
    index: SearchIndex = SearchIndex.kb
    filters: SearchFilters
    top_k: int = Field(default=8, ge=1, le=50)


class SearchResult(BaseModel):
    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionKind(str, Enum):
    link = "link"
    phone = "phone"
    email = "email"


class Action(BaseModel):
    action_id: str
    brand: Brand
    kind: ActionKind
    value: str
    label: str
    verbatim: bool = False


class CompareRequest(BaseModel):
    product_codes: list[str] = Field(min_length=2)
    benefit_codes: list[str] | None = None


class HandoverPayload(BaseModel):
    session_id: str
    transcript: list[dict[str, str]]
    summary: str
    reason: str


class PublishEvent(BaseModel):
    """Event published by the CMS on the Redis stream KB_PUBLISH_STREAM."""

    bundle_id: str
    git_ref: str
    delta: bool = False
    blocks: list[str] = Field(default_factory=list)
