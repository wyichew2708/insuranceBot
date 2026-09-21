"""Retention applies to persisted, cached and asynchronously refined sessions."""

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from api.memory import RECALL_TURNS, SessionMemory
from api.pipeline import memory_for
from api.settings import Settings
from harness.contracts import AnswerEnvelope, GroundedAnswer
from pydantic import ValidationError


def remember(memory: SessionMemory, question: str = "home insurance", session: str = "s1") -> None:
    memory.remember(session, question, AnswerEnvelope(answer=GroundedAnswer(answer="Original answer.")), None)


def test_retention_survives_restart_and_preserves_recall(tmp_path: Path) -> None:
    memory = SessionMemory(tmp_path, max_turns=8)
    for i in range(30):
        remember(memory, f"question {i}")
    persisted = json.loads((tmp_path / "sessions/s1.json").read_text())
    assert [t["question"] for t in persisted["turns"]] == [f"question {i}" for i in range(22, 30)]
    recalled = SessionMemory(tmp_path, max_turns=8).recall("s1")
    assert recalled.questions == [f"question {i}" for i in range(30 - RECALL_TURNS, 30)]
    assert "question 24" not in recalled.summary
    assert "question 25" in recalled.summary


def test_existing_long_session_is_trimmed_on_load(tmp_path: Path) -> None:
    old = SessionMemory(tmp_path, max_turns=100)
    for i in range(40):
        remember(old, f"question {i}")
    assert len(SessionMemory(tmp_path).record("s1")["turns"]) == 20
    assert len(json.loads((tmp_path / "sessions/s1.json").read_text())["turns"]) == 20


def test_expiry_removes_cached_and_unvisited_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("api.memory.time.time", lambda: now[0])
    memory = SessionMemory(tmp_path, idle_ttl_seconds=120)
    remember(memory)
    remember(memory, session="unvisited")
    now[0] += 119
    assert memory.recall("s1").questions
    # Reading a session does not extend retention; only a new turn does.
    now[0] += 1
    assert memory.recall("s1").questions == []
    assert not (tmp_path / "sessions/s1.json").exists()
    now[0] += 60
    SessionMemory(tmp_path, idle_ttl_seconds=120).recall("new-session")
    assert not (tmp_path / "sessions/unvisited.json").exists()


def test_new_turn_refreshes_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("api.memory.time.time", lambda: now[0])
    memory = SessionMemory(tmp_path, idle_ttl_seconds=120)
    remember(memory)
    now[0] += 100
    remember(memory, "how do I claim")
    now[0] += 100
    assert memory.recall("s1").questions == ["home insurance", "how do I claim"]


@pytest.mark.parametrize("contents", ["{", "[]", '{"turns": [null]}'])
def test_malformed_file_is_empty_memory(tmp_path: Path, contents: str) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "s1.json").write_text(contents)
    assert SessionMemory(tmp_path).recall("s1").questions == []


def test_disabled_memory_does_not_read_or_sweep(tmp_path: Path) -> None:
    remember(SessionMemory(tmp_path))
    memory = SessionMemory(tmp_path, enabled=False)
    assert memory.recall("s1").questions == []
    assert memory.record("s1")["turns"] == []
    assert (tmp_path / "sessions/s1.json").exists()


@pytest.mark.parametrize("expire", [False, True])
def test_delayed_summary_cannot_overwrite_or_resurrect_a_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expire: bool,
) -> None:
    now = [1000.0]
    monkeypatch.setattr("api.memory.time.time", lambda: now[0])
    pending: list[Any] = []

    class DeferredThread:
        def __init__(self, *, target: Any, daemon: bool) -> None:
            pending.append(target)

        def start(self) -> None:
            pass

    class Provider:
        name = "test"

        def classify(self, *args: Any, **kwargs: Any) -> dict[str, str]:
            return {"summary": "Stale model summary"}

    monkeypatch.setattr(threading, "Thread", DeferredThread)
    memory = SessionMemory(tmp_path, idle_ttl_seconds=120)
    remember(memory)
    memory.refine_later("s1", "home insurance", "Original answer.", Provider())
    if expire:
        now[0] += 120
    else:
        # Identical question text does not make this the same turn.
        remember(memory)
    pending[0]()
    assert "Stale model summary" not in memory.recall("s1").summary
    if expire:
        assert not (tmp_path / "sessions/s1.json").exists()


def test_settings_validate_limits_and_select_separate_memories(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(memory_max_turns=5)
    with pytest.raises(ValidationError):
        Settings(memory_idle_ttl_seconds=0)
    first = memory_for(Settings(state_dir=tmp_path, memory="on", memory_max_turns=20))
    second = memory_for(Settings(state_dir=tmp_path, memory="on", memory_max_turns=30))
    assert first is not second
    assert second.max_turns == 30
