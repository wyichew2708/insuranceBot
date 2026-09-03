"""Conversation memory: what this session has asked and been told, summarised.

The API was stateless by design — the client sent the last few questions
and a turn was reproducible from its own request. That stays true: a client
that sends `history` is believed. What this adds is a server-side record per
session, so a client that sends nothing still gets a subject carried forward,
and every turn leaves a one-line summary behind it: the product, what was
asked, and the first sentence of what was answered. The rolling summary is
the last few of those, and it is what a later turn's reading of the question
falls back on.

Stored as one JSON file per session under `state_dir/sessions/`, and kept in
memory while the process runs. Nothing here is the customer's policy data —
the system of record holds that — and a session file holds only what the
customer typed and what the bot said back.

Summaries are deterministic and synchronous, so the turn never waits on a
model to remember itself. Where a model is configured, a tidier one-sentence
summary is written by a background thread afterwards and replaces the
deterministic line when it arrives.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.ask import Ask
from harness.contracts import AnswerEnvelope

#: How many earlier questions a turn is given back.
RECALL_TURNS = 6
#: How many turn summaries make the rolling summary.
ROLLING_TURNS = 5
SUMMARY_CHARS = 160

_FIRST_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_MARKUP_RE = re.compile(r"\*\*|^- ", re.M)

SUMMARY_SYSTEM = """\
You summarise one turn of an insurance customer-service conversation in one \
sentence of at most 25 words: what the customer asked and what they were told. \
Name the product if one is named. Do not add facts, figures or advice."""
SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary"],
    "properties": {"summary": {"type": "string", "maxLength": 240}},
}


@dataclass
class Turn:
    at: float
    question: str
    answer_summary: str
    product: str | None = None
    intent: str = ""
    delivered: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "question": self.question,
            "answer_summary": self.answer_summary,
            "product": self.product,
            "intent": self.intent,
            "delivered": self.delivered,
        }


@dataclass
class Recall:
    questions: list[str] = field(default_factory=list)
    summary: str = ""
    last_product: str | None = None


def summarise_turn(question: str, envelope: AnswerEnvelope, ask: Ask | None) -> str:
    """The deterministic one-liner: product, intent, and the answer's first sentence."""
    answer = envelope.answer
    text = _MARKUP_RE.sub("", answer.answer or "").strip()
    first = _FIRST_SENTENCE_RE.split(text, maxsplit=1)[0].strip() if text else ""
    if len(first) > SUMMARY_CHARS:
        first = first[: SUMMARY_CHARS - 1].rstrip() + "…"
    if answer.clarifying:
        what = "asked which product was meant"
    elif answer.handoff:
        what = "handed to a colleague"
    elif answer.smalltalk:
        what = "greeted"
    else:
        what = first or "answered"
    product = ask.product if ask and ask.product else "general"
    intent = ask.intent.value if ask else "unknown"
    return f"[{product} · {intent}] asked: {question.strip()[:80]} → {what}"


class SessionMemory:
    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = root
        self.enabled = enabled
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # --- reading -----------------------------------------------------------

    def _path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:120] or "anonymous"
        return self.root / "sessions" / f"{safe}.json"

    def _load(self, session_id: str) -> dict[str, Any]:
        if session_id in self._cache:
            return self._cache[session_id]
        path = self._path(session_id)
        record: dict[str, Any] = {"session_id": session_id, "turns": [], "summary": ""}
        if path.exists():
            # An unreadable file is an empty memory, not a failed turn.
            with contextlib.suppress(OSError, json.JSONDecodeError):
                record = json.loads(path.read_text())
        self._cache[session_id] = record
        return record

    def recall(self, session_id: str) -> Recall:
        if not self.enabled or not session_id:
            return Recall()
        with self._lock:
            record = self._load(session_id)
            turns = record.get("turns", [])[-RECALL_TURNS:]
            last_product = next((t.get("product") for t in reversed(turns) if t.get("product")), None)
            return Recall(
                questions=[t["question"] for t in turns if t.get("question")],
                summary=record.get("summary", ""),
                last_product=last_product,
            )

    def record(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            copy: dict[str, Any] = json.loads(json.dumps(self._load(session_id)))
            return copy

    # --- writing -----------------------------------------------------------

    def remember(self, session_id: str, question: str, envelope: AnswerEnvelope, ask: Ask | None) -> str:
        """Append this turn and refresh the rolling summary. Returns the turn's summary."""
        summary = summarise_turn(question, envelope, ask)
        if not self.enabled or not session_id:
            return summary
        turn = Turn(
            at=time.time(),
            question=question,
            answer_summary=summary,
            product=ask.product if ask else None,
            intent=ask.intent.value if ask else "",
            delivered=envelope.delivered,
        )
        with self._lock:
            record = self._load(session_id)
            record.setdefault("turns", []).append(turn.as_dict())
            record["summary"] = " ".join(t["answer_summary"] for t in record["turns"][-ROLLING_TURNS:])
            self._write(session_id, record)
        return summary

    def refine_later(self, session_id: str, question: str, answer_text: str, provider: Any) -> None:
        """Replace the last turn's deterministic summary with a model's, off the
        request path. Silent on any fault: the deterministic line stands."""
        classify = getattr(provider, "classify", None)
        if not self.enabled or classify is None or getattr(provider, "name", "") == "deterministic":
            return

        def work() -> None:
            try:
                payload = classify(
                    SUMMARY_SYSTEM,
                    f"CUSTOMER: {question}\nASSISTANT: {answer_text[:1500]}",
                    SUMMARY_SCHEMA,
                    max_tokens=96,
                )
            except Exception:
                return
            text = payload.get("summary") if isinstance(payload, dict) else None
            if not isinstance(text, str) or not text.strip():
                return
            with self._lock:
                record = self._load(session_id)
                turns = record.get("turns", [])
                if turns and turns[-1].get("question") == question:
                    prefix = turns[-1]["answer_summary"].split("]", 1)[0] + "]"
                    turns[-1]["answer_summary"] = f"{prefix} {text.strip()}"
                    record["summary"] = " ".join(t["answer_summary"] for t in turns[-ROLLING_TURNS:])
                    self._write(session_id, record)

        threading.Thread(target=work, daemon=True).start()

    def _write(self, session_id: str, record: dict[str, Any]) -> None:
        path = self._path(session_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
        except OSError:
            pass  # memory is a convenience; a full disk must not fail the turn
