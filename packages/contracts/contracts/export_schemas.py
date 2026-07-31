"""Export JSON Schemas for shared contracts to contracts/schema/*.json.

Run: python -m contracts.export_schemas
test_schemas_current.py fails if the exported files are stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from contracts.api import (
    Action,
    ChatEvent,
    ChatRequest,
    CompareRequest,
    HandoverPayload,
    PublishEvent,
    SearchRequest,
    SearchResult,
)
from contracts.okf import OkfFrontmatter

SCHEMA_DIR = Path(__file__).parent / "schema"

EXPORTED: dict[str, type[BaseModel]] = {
    "okf_frontmatter": OkfFrontmatter,
    "chat_request": ChatRequest,
    "chat_event": ChatEvent,
    "search_request": SearchRequest,
    "search_result": SearchResult,
    "action": Action,
    "compare_request": CompareRequest,
    "handover_payload": HandoverPayload,
    "publish_event": PublishEvent,
}


def render_schemas() -> dict[str, str]:
    return {
        name: json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        for name, model in EXPORTED.items()
    }


def main() -> None:
    SCHEMA_DIR.mkdir(exist_ok=True)
    for name, content in render_schemas().items():
        (SCHEMA_DIR / f"{name}.json").write_text(content)
    print(f"exported {len(EXPORTED)} schemas to {SCHEMA_DIR}")


if __name__ == "__main__":
    main()
