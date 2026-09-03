"""The stream says what the plain endpoint says, and says it in order."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from api import main as api_main
from api.settings import Settings
from fastapi.testclient import TestClient

BUNDLE_ROOT = Path(__file__).resolve().parents[3] / "okf"


@pytest.fixture()
def client() -> Iterator[TestClient]:
    api_main._state["settings"] = Settings(bundle_path=BUNDLE_ROOT)
    api_main._state["bundle"] = None
    with TestClient(api_main.app) as test_client:
        yield test_client
    api_main._state["settings"] = None
    api_main._state["bundle"] = None


def _events(body: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for frame in body.split("\n\n"):
        event, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            out.append((event, json.loads(data)))
    return out


def test_stream_delivers_the_verified_answer_after_the_stages(client: TestClient) -> None:
    body = {
        "question": "what does travel insurance cover",
        "session": {"session_id": "stream-1", "channel": "channel/direct", "auth_level": "L0"},
        "history": [],
    }
    plain = client.post("/v1/answer", json=body).json()
    res = client.post("/v1/answer/stream", json=body)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    events = _events(res.text)
    kinds = [k for k, _ in events]

    # Progress first, then the text, then the envelope — never text before
    # the gates have run.
    assert kinds[0] == "stage"
    assert "done" in kinds and kinds[-1] == "done"
    first_delta = kinds.index("delta")
    assert all(k == "stage" for k in kinds[:first_delta])

    # The streamed text is the delivered answer, whole.
    streamed = "".join(d["text"] for k, d in events if k == "delta")
    done = events[-1][1]
    assert streamed == done["answer"]["answer"]
    assert done["answer"]["answer"] == plain["answer"]["answer"]
    assert done["delivered"] == plain["delivered"]

    # Every stage the customer sees has a label, and closes.
    stages = [d for k, d in events if k == "stage"]
    assert all(d["label"] for d in stages)
    assert any(d["name"] == "gates" and d["phase"] == "end" for d in stages)
