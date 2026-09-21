"""Interaction rates with explicit denominators; unlabelled turns aren't accuracy data."""

from typing import Any


def interaction_metrics(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    turns = [t for r in results for t in r.get("turn_results", [])]
    labelled = [t for t in turns if t.get("expected_product")]
    observed = [t.get("observed", {}) for t in turns]
    handoffs = [o for o in observed if o.get("handoff")]
    overviews = [
        o
        for o in observed
        if o.get("handler") in {"knowledge.coverage", "knowledge.general"} and o.get("selected_product")
    ]

    def rate(count: int, total: int) -> dict[str, Any]:
        return {"count": count, "total": total, "rate": count / total if total else None}

    return {
        "selection accuracy": rate(
            sum(
                t.get("observed", {}).get("selected_product")
                in (
                    [t["expected_product"]]
                    if isinstance(t["expected_product"], str)
                    else t["expected_product"]
                )
                for t in labelled
            ),
            len(labelled),
        ),
        "clarification rate": rate(sum(bool(o.get("clarifying")) for o in observed), len(observed)),
        "handoff dead-end rate": rate(
            sum(not o.get("suggestions") and not o.get("destinations") for o in handoffs), len(handoffs)
        ),
        "overview chip coverage": rate(sum(o.get("suggestions", 0) >= 3 for o in overviews), len(overviews)),
    }
