"""Build grader Evidence from the agent's actual tool observations, and screen
retrieved web text for prompt-injection before the model ever sees it (§9.2).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from contracts.screening import screen_injection

from orchestrator.verification import Evidence

logger = logging.getLogger("orchestrator.evidence")


def _parse_dt(raw: Any) -> dt.datetime | None:
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _parse_date(raw: Any) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def screen_tool_result(tool: str, result: Any) -> Any:
    """Neutralise imperative-to-assistant patterns inside retrieved web text.
    KB text is CMS-authored and trusted; web text is not."""
    if tool != "search_web_index" or not isinstance(result, list):
        return result
    screened_result = []
    for item in result:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            screened, flagged = screen_injection(item["text"])
            if flagged:
                logger.warning("injection pattern stripped from web chunk %s", item.get("chunk_id"))
                item = {**item, "text": screened, "injection_flagged": True}
        screened_result.append(item)
    return screened_result


def build_evidence(
    tool_results: list[dict[str, Any]],
    session_audience: str,
    today: dt.date | None = None,
) -> Evidence:
    ev = Evidence(session_audience=session_audience)
    if today is not None:
        ev.today = today

    for obs in tool_results:
        tool = obs.get("tool")
        result = obs.get("result")
        if tool in {"search_kb", "search_web_index"} and isinstance(result, list):
            for item in result:
                if not isinstance(item, dict) or "chunk_id" not in item:
                    continue
                chunk_id = str(item["chunk_id"])
                metadata = item.get("metadata") or {}
                ev.cited_texts[chunk_id] = str(item.get("text", ""))
                ev.cited_audiences[chunk_id] = str(metadata.get("audience", "public"))
                ev.permitted_chunk_ids.add(chunk_id)
                if tool == "search_web_index":
                    ev.promo_windows[chunk_id] = (
                        _parse_dt(metadata.get("expires_at")),
                        _parse_date(metadata.get("accurate_as_of")),
                    )
        elif tool in {"read_page", "get_procedure"} and isinstance(result, dict) and "block_id" in result:
            block_id = str(result["block_id"])
            metadata = result.get("metadata") or {}
            ev.cited_texts[block_id] = str(result.get("text", ""))
            ev.cited_audiences[block_id] = str(metadata.get("audience", "public"))
            ev.permitted_chunk_ids.add(block_id)
        elif tool == "get_action" and isinstance(result, dict) and "value" in result:
            ev.action_values[str(result.get("action_id", ""))] = str(result["value"])

    return ev


def benefit_answer(tool_results: list[dict[str, Any]], citations: list[str]) -> bool:
    """True when any cited chunk is a benefit block (drives disclaimer-attach)."""
    types: dict[str, str] = {}
    for obs in tool_results:
        result = obs.get("result")
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict) and "chunk_id" in item:
                    types[str(item["chunk_id"])] = str((item.get("metadata") or {}).get("type", ""))
        elif isinstance(result, dict) and "block_id" in result:
            types[str(result["block_id"])] = str((result.get("metadata") or {}).get("type", ""))
    return any(types.get(c) == "benefit" for c in citations)
