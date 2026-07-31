"""Every tool: schema registration + malformed-args rejection (§13)."""

from typing import Any

import pytest
from orchestrator.tools import REGISTRY, ToolError, validate_tool_call

VALID_CALLS: dict[str, dict[str, Any]] = {
    "search_kb": {"query": "baggage cover"},
    "search_web_index": {"query": "current promotions"},
    "read_page": {"block_id": "tiq-trv/faq/what-is-covered"},
    "get_catalogue": {"product_code": "TIQ-TRV"},
    "compare_plans": {"product_codes": ["TIQ-TRV", "ETQ-TRV"]},
    "get_procedure": {"intent": "cancel-policy"},
    "get_action": {"action_id": "customer-hotline"},
    "escalate_human": {"reason": "complex complaint", "transcript_ref": "sess-1"},
}

MALFORMED_CALLS: dict[str, dict[str, Any]] = {
    "search_kb": {"query": ""},
    "search_web_index": {"top_k": 5},
    "read_page": {},
    "get_catalogue": {"product_code": None},
    "compare_plans": {"product_codes": ["ONLY-ONE"]},
    "get_procedure": {"intent": 123},
    "get_action": {"action_id": ""},
    "escalate_human": {"reason": "x"},
}


def test_registry_covers_planned_tools() -> None:
    assert set(VALID_CALLS) == set(REGISTRY)
    for spec in REGISTRY.values():
        assert spec.permission == "all"
        schema = spec.json_schema
        assert schema.get("type") == "object"


@pytest.mark.parametrize("name", sorted(VALID_CALLS))
def test_valid_args_accepted(name: str) -> None:
    validate_tool_call(name, VALID_CALLS[name])


@pytest.mark.parametrize("name", sorted(MALFORMED_CALLS))
def test_malformed_args_rejected(name: str) -> None:
    with pytest.raises(ToolError):
        validate_tool_call(name, MALFORMED_CALLS[name])


def test_unknown_tool_rejected() -> None:
    with pytest.raises(ToolError):
        validate_tool_call("delete_policy", {})
