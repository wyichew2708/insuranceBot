"""Conversation memory: what this session has asked and been told, summarised.

The API was stateless by design — the client sent the last few questions
and a turn was reproducible from its own request. That stays true: a client
that sends `history` is believed. What this adds is a server-side record per
session, so a client that sends nothing still gets a subject carried forward,
and every turn leaves a one-line summary behind it: the product, what was
asked, and the first sentence of what was answered. The rolling summary is
returned on the envelope; follow-up resolution still uses recent questions.

Stored as one JSON file per session under `state_dir/sessions/`, and kept in
memory while the process runs. Nothing here is the customer's policy data —
the system of record holds that — and a session file holds only what the
customer typed and what the bot said back. Keep only the latest configured
number of turns (twenty by default), and expire idle sessions after a day.
Cleanup runs on memory access, including files belonging to unvisited sessions,
at most once per minute; no cleanup runs while the process is idle or stopped.

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

from api.entities import EntitySlots, restore_slots

#: How many earlier questions a turn is given back.
RECALL_TURNS = 6
#: How many turn summaries make the rolling summary.
ROLLING_TURNS = 5
SUMMARY_CHARS = 160
MAX_TURNS = 20
IDLE_TTL_SECONDS = 24 * 60 * 60
SWEEP_SECONDS = 60

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
    product_page: str | None = None
    answered: bool = False
    slots: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "question": self.question,
            "answer_summary": self.answer_summary,
            "product": self.product,
            "intent": self.intent,
            "delivered": self.delivered,
            "product_page": self.product_page,
            "answered": self.answered,
            "slots": self.slots,
        }


@dataclass
class ConversationState:
    product: str | None = None
    product_page: str | None = None
    intent: str = ""
    answered_topics: dict[str, frozenset[str]] = field(default_factory=dict)
    slots: EntitySlots = field(default_factory=EntitySlots)


@dataclass
class Recall:
    questions: list[str] = field(default_factory=list)
    summary: str = ""
    last_product: str | None = None
    state: ConversationState = field(default_factory=ConversationState)


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
    def __init__(
        self,
        root: Path,
        enabled: bool = True,
        *,
        max_turns: int = MAX_TURNS,
        idle_ttl_seconds: float = IDLE_TTL_SECONDS,
    ) -> None:
        if max_turns < RECALL_TURNS or idle_ttl_seconds <= 0:
            raise ValueError("Memory requires at least six retained turns and a positive idle TTL")
        self.root = root
        self.enabled = enabled
        self.max_turns = max_turns
        self.idle_ttl_seconds = idle_ttl_seconds
        self._next_sweep = 0.0
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # --- reading -----------------------------------------------------------

    def _path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:120] or "anonymous"
        return self.root / "sessions" / f"{safe}.json"

    def _load(self, session_id: str) -> dict[str, Any]:
        now = time.time()
        self._sweep(now)
        if session_id in self._cache:
            cached = self._cache[session_id]
            if not self._expired(cached, now):
                return cached
            self._cache.pop(session_id, None)
            with contextlib.suppress(OSError):
                self._path(session_id).unlink(missing_ok=True)
        path = self._path(session_id)
        record: dict[str, Any] = {"session_id": session_id, "turns": [], "summary": ""}
        if path.exists():
            # An unreadable file is an empty memory, not a failed turn.
            loaded = self._read(path)
            if loaded is not None and not self._expired(loaded, now):
                record = loaded
                if len(record["turns"]) > self.max_turns:
                    record["turns"] = record["turns"][-self.max_turns :]
                    self._summarise(record)
                    self._write(session_id, record)
            else:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
        self._cache[session_id] = record
        return record

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        with contextlib.suppress(OSError, ValueError, TypeError):
            record = json.loads(path.read_text())
            if (
                isinstance(record, dict)
                and isinstance(record.get("turns"), list)
                and all(
                    isinstance(t, dict)
                    and isinstance(t.get("at"), (int, float))
                    and isinstance(t.get("question"), str)
                    and isinstance(t.get("answer_summary"), str)
                    for t in record["turns"]
                )
            ):
                return record
        return None

    def _expired(self, record: dict[str, Any], now: float) -> bool:
        turns = record.get("turns", [])
        return not turns or now - turns[-1]["at"] >= self.idle_ttl_seconds

    def _sweep(self, now: float) -> None:
        """Reap idle sessions on access, at most once a minute, including disk-only ones."""
        if now < self._next_sweep:
            return
        self._next_sweep = now + SWEEP_SECONDS
        for key, record in list(self._cache.items()):
            if self._expired(record, now):
                self._cache.pop(key, None)
        with contextlib.suppress(OSError):
            for path in (self.root / "sessions").glob("*.json"):
                stored = self._read(path)
                if stored is None or self._expired(stored, now):
                    with contextlib.suppress(OSError):
                        path.unlink(missing_ok=True)

    @staticmethod
    def _summarise(record: dict[str, Any]) -> None:
        record["summary"] = " ".join(t["answer_summary"] for t in record["turns"][-ROLLING_TURNS:])

    def recall(self, session_id: str) -> Recall:
        if not self.enabled or not session_id:
            return Recall()
        with self._lock:
            record = self._load(session_id)
            turns = record.get("turns", [])[-RECALL_TURNS:]
            last_product = next((t.get("product") for t in reversed(turns) if t.get("product")), None)
            retained = record.get("turns", [])
            latest: dict[str, Any] = next((t for t in reversed(retained) if t.get("product")), {})
            answered: dict[str, set[str]] = {}
            for turn in retained:
                if turn.get("answered") and turn.get("product") and turn.get("intent"):
                    answered.setdefault(turn["product"], set()).add(
                        "documents" if turn["intent"] == "document" else turn["intent"]
                    )
            return Recall(
                questions=[t["question"] for t in turns if t.get("question")],
                summary=record.get("summary", ""),
                last_product=last_product,
                state=ConversationState(
                    slots=restore_slots(retained[-1].get("slots", {})) if retained else EntitySlots(),
                    product=latest.get("product"),
                    product_page=latest.get("product_page"),
                    intent=latest.get("intent", ""),
                    answered_topics={key: frozenset(value) for key, value in answered.items()},
                ),
            )

    def record(self, session_id: str) -> dict[str, Any]:
        if not self.enabled or not session_id:
            return {"session_id": session_id, "turns": [], "summary": ""}
        with self._lock:
            copy: dict[str, Any] = json.loads(json.dumps(self._load(session_id)))
            return copy

    # --- writing -----------------------------------------------------------

    def remember(
        self,
        session_id: str,
        question: str,
        envelope: AnswerEnvelope,
        ask: Ask | None,
        *,
        slots: EntitySlots | None = None,
    ) -> str:
        """Append this turn and refresh the rolling summary. Returns the turn's summary."""
        summary = summarise_turn(question, envelope, ask)
        if not self.enabled or not session_id:
            return summary
        turn = Turn(
            slots=(slots or EntitySlots()).model_dump(mode="json"),
            at=time.time(),
            question=question,
            answer_summary=summary,
            product=ask.product if ask else None,
            intent=ask.intent.value if ask else "",
            delivered=envelope.delivered,
            product_page=ask.product_page if ask else None,
            answered=(
                envelope.delivered
                and not envelope.answer.handoff
                and not envelope.answer.clarifying
                and not envelope.answer.smalltalk
                and not envelope.answer.guidance
            ),
        )
        with self._lock:
            record = self._load(session_id)
            record.setdefault("turns", []).append(turn.as_dict())
            record["turns"] = record["turns"][-self.max_turns :]
            self._summarise(record)
            self._write(session_id, record)
        return summary

    def refine_later(self, session_id: str, question: str, answer_text: str, provider: Any) -> None:
        """Replace the last turn's deterministic summary with a model's, off the
        request path. Silent on any fault: the deterministic line stands."""
        classify = getattr(provider, "classify", None)
        if (
            not self.enabled
            or not session_id
            or classify is None
            or getattr(provider, "name", "") == "deterministic"
        ):
            return
        with self._lock:
            turns = self._load(session_id).get("turns", [])
            if not turns or turns[-1].get("question") != question:
                return
            target = turns[-1]

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
                if turns and turns[-1] is target:
                    prefix = turns[-1]["answer_summary"].split("]", 1)[0] + "]"
                    turns[-1]["answer_summary"] = f"{prefix} {text.strip()[:240]}"
                    self._summarise(record)
                    self._write(session_id, record)

        threading.Thread(target=work, daemon=True).start()

    def _write(self, session_id: str, record: dict[str, Any]) -> None:
        path = self._path(session_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
        except OSError:
            pass  # memory is a convenience; a full disk must not fail the turn
