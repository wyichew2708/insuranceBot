"""Typed tool registry (§8.2).

Every tool: pydantic args model, permission tag, JSON Schema for guided
decoding, audit-log + Langfuse span at call time. The model never sees raw
URLs/phone numbers — it references action_ids; the renderer substitutes
exact values from the actions table (verbatim rule enforced mechanically).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from contracts.api import SearchFilters, SearchIndex, SearchRequest
from contracts.settings import Settings
from pydantic import BaseModel, Field, ValidationError


class ToolError(Exception):
    pass


class SearchKbArgs(BaseModel):
    query: str = Field(min_length=1)
    line: str | None = None
    product_code: str | None = None
    top_k: int = Field(default=8, ge=1, le=20)


class SearchWebArgs(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=20)


class ReadPageArgs(BaseModel):
    block_id: str = Field(min_length=1)


class CatalogueArgs(BaseModel):
    product_code: str = Field(min_length=1)


class CompareArgs(BaseModel):
    product_codes: list[str] = Field(min_length=2)
    benefit_codes: list[str] | None = None


class ProcedureArgs(BaseModel):
    intent: str | None = None
    block_id: str | None = None


class ActionArgs(BaseModel):
    action_id: str = Field(min_length=1)


class EscalateArgs(BaseModel):
    reason: str = Field(min_length=1)
    transcript_ref: str = Field(min_length=1)


@dataclass
class ToolSpec:
    name: str
    args_model: type[BaseModel]
    permission: str  # "all" for v1; per-audience tags later
    handler: Callable[[BaseModel, ToolContext], Awaitable[Any]]

    @property
    def json_schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()


@dataclass
class ToolContext:
    settings: Settings
    session_id: str
    brand: str
    audience: str
    language: str = "en"
    jurisdiction: str = "SG"
    trace_id: str = ""


async def _retrieval_post(ctx: ToolContext, path: str, payload: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(base_url=ctx.settings.retrieval_url, timeout=15) as http:
        resp = await http.post(path, json=payload)
        resp.raise_for_status()
        return resp.json()


async def _retrieval_get(ctx: ToolContext, path: str) -> Any:
    async with httpx.AsyncClient(base_url=ctx.settings.retrieval_url, timeout=15) as http:
        resp = await http.get(path)
        resp.raise_for_status()
        return resp.json()


def _session_filters(ctx: ToolContext, args: SearchKbArgs | None = None) -> SearchFilters:
    # Session-derived filters are authoritative: the model cannot widen
    # audience/brand — only narrow by line/product.
    return SearchFilters.model_validate(
        {
            "brand": ctx.brand,
            "audience": ctx.audience,
            "language": ctx.language,
            "jurisdiction": ctx.jurisdiction,
            "line": args.line if args else None,
            "product_code": args.product_code if args else None,
            "active_on": dt.date.today(),
        }
    )


async def _search_kb(args: BaseModel, ctx: ToolContext) -> Any:
    assert isinstance(args, SearchKbArgs)
    req = SearchRequest(
        query=args.query, index=SearchIndex.kb, filters=_session_filters(ctx, args), top_k=args.top_k
    )
    return await _retrieval_post(ctx, "/search", req.model_dump(mode="json"))


async def _search_web(args: BaseModel, ctx: ToolContext) -> Any:
    assert isinstance(args, SearchWebArgs)
    req = SearchRequest(
        query=args.query, index=SearchIndex.web, filters=_session_filters(ctx), top_k=args.top_k
    )
    return await _retrieval_post(ctx, "/search", req.model_dump(mode="json"))


async def _read_page(args: BaseModel, ctx: ToolContext) -> Any:
    assert isinstance(args, ReadPageArgs)
    return await _retrieval_get(ctx, f"/page/{args.block_id}")


async def _get_catalogue(args: BaseModel, ctx: ToolContext) -> Any:
    assert isinstance(args, CatalogueArgs)
    return await _retrieval_get(ctx, f"/catalogue/{args.product_code}")


async def _compare_plans(args: BaseModel, ctx: ToolContext) -> Any:
    assert isinstance(args, CompareArgs)
    return await _retrieval_post(ctx, "/catalogue/compare", args.model_dump(mode="json"))


async def _get_procedure(args: BaseModel, ctx: ToolContext) -> Any:
    assert isinstance(args, ProcedureArgs)
    if not args.block_id and not args.intent:
        raise ToolError("get_procedure requires intent or block_id")
    if args.block_id:
        return await _retrieval_get(ctx, f"/page/{args.block_id}")
    req = SearchRequest(
        query=f"procedure: {args.intent}", index=SearchIndex.kb, filters=_session_filters(ctx), top_k=3
    )
    return await _retrieval_post(ctx, "/search", req.model_dump(mode="json"))


async def _get_action(args: BaseModel, ctx: ToolContext) -> Any:
    assert isinstance(args, ActionArgs)
    return await _retrieval_get(ctx, f"/actions/{ctx.brand}/{args.action_id}")


async def _escalate_human(args: BaseModel, ctx: ToolContext) -> Any:
    assert isinstance(args, EscalateArgs)
    # The loop treats this tool as terminal; the payload is emitted as a
    # handover event and pushed to the handover.requests stream by the caller.
    return {"handover": True, "reason": args.reason, "transcript_ref": args.transcript_ref}


REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec("search_kb", SearchKbArgs, "all", _search_kb),
        ToolSpec("search_web_index", SearchWebArgs, "all", _search_web),
        ToolSpec("read_page", ReadPageArgs, "all", _read_page),
        ToolSpec("get_catalogue", CatalogueArgs, "all", _get_catalogue),
        ToolSpec("compare_plans", CompareArgs, "all", _compare_plans),
        ToolSpec("get_procedure", ProcedureArgs, "all", _get_procedure),
        ToolSpec("get_action", ActionArgs, "all", _get_action),
        ToolSpec("escalate_human", EscalateArgs, "all", _escalate_human),
    ]
}


def validate_tool_call(name: str, raw_args: dict[str, Any]) -> BaseModel:
    """Schema-validate a model-proposed tool call. Raises ToolError on any mismatch."""
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(f"unknown tool {name!r}")
    try:
        return spec.args_model.model_validate(raw_args)
    except ValidationError as exc:
        raise ToolError(f"invalid args for {name}: {exc}") from exc
