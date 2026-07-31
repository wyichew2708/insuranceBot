"""Guided-procedure flow (§8.3): render a procedure block into the fixed
template. This path bypasses free generation entirely — only the source
block's own text is used, so the verbatim rule holds by construction."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

CANNOT_EXECUTE_LINE = (
    "I can't make this change for you, but the steps above will get it done through the official channels."
)


class ProcedureView(BaseModel):
    text: str
    citation: str
    action_ids: list[str]


def render_procedure(block: dict[str, Any]) -> ProcedureView:
    """block: /page/{block_id} payload — {block_id, text, metadata} where
    metadata carries channels / sla / action_ref from frontmatter."""
    metadata = block.get("metadata") or {}
    parts: list[str] = [str(block.get("text", "")).strip()]

    channels = metadata.get("channels") or []
    if channels:
        parts.append("Available channels: " + ", ".join(str(c) for c in channels) + ".")
    sla = metadata.get("sla")
    if sla:
        parts.append(f"Processing time: {sla}.")
    parts.append(CANNOT_EXECUTE_LINE)

    action_ids: list[str] = []
    action_ref = metadata.get("action_ref")
    if action_ref:
        action_ids.append(str(action_ref))

    return ProcedureView(
        text="\n\n".join(p for p in parts if p),
        citation=str(block["block_id"]),
        action_ids=action_ids,
    )
