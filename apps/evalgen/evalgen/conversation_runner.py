"""Run conversations turn by turn and score what only multi-turn can show."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any

from api.pipeline import answer_question
from api.settings import Settings
from harness import AnswerEnvelope, Trace

from evalgen.conversations import Conversation, ConversationSuite, Turn
from evalgen.runner import build_session
from okf import Bundle

FIGURE_RE = re.compile(r"S?\$\s?\d[\d,]*(?:\.\d+)?|\b\d+(?:\.\d+)?\s?%|\b\d+\s+hours?\b", re.I)


def _turn_failures(turn: Turn, envelope: AnswerEnvelope, trace: Trace) -> list[str]:
    expect, answer = turn.expect, envelope.answer
    text = answer.answer.lower()
    out: list[str] = []
    for needle in expect.must_contain:
        if needle.lower() not in text:
            out.append(f"missing {needle!r}")
    for needle in expect.must_not_contain:
        if needle and needle.lower() in text:
            out.append(f"leaked {needle!r}")
    for row_id in expect.expect_row_ids:
        if row_id not in {f.table_row_id for f in answer.figures if f.table_row_id}:
            out.append(f"not bound to {row_id}")
    if expect.expect_delivered is not None and envelope.delivered != expect.expect_delivered:
        out.append(f"delivered={envelope.delivered}, expected {expect.expect_delivered}")
    if expect.expect_advice_flag is not None and answer.advice_flag != expect.expect_advice_flag:
        out.append(f"advice_flag={answer.advice_flag}, expected {expect.expect_advice_flag}")
    if expect.expect_handoff is not None and answer.handoff != expect.expect_handoff:
        out.append(f"handoff={answer.handoff}, expected {expect.expect_handoff}")
    return out


def run_conversation(bundle: Bundle, settings: Settings, convo: Conversation) -> dict[str, Any]:
    """One session, every turn in order.

    The session object is built once and reused, because that is the only state
    the design actually carries between turns — channel, auth, policy. Anything
    the customer said earlier is gone, which is precisely what the
    `needs_context` turns are here to measure.
    """
    session = build_session(convo.session, convo.id)
    turns: list[dict[str, Any]] = []
    figures_by_tag: dict[str, set[str]] = defaultdict(set)

    for index, turn in enumerate(convo.turns):
        started = time.perf_counter()
        try:
            envelope, trace = answer_question(bundle, turn.question, session, settings)
        except Exception as exc:  # a crashed turn is a failed turn, not a failed run
            turns.append(
                {
                    "index": index,
                    "kind": turn.kind,
                    "question": turn.question,
                    "passed": False,
                    "failures": [f"error: {exc}"],
                    "needs_context": turn.needs_context,
                    "delivered": False,
                    "ms": 0.0,
                }
            )
            continue
        failures = _turn_failures(turn, envelope, trace)
        if turn.consistency_tag:
            # Only the figure bound to *this* row. The composer answers with a
            # whole section, so an answer legitimately carries several figures;
            # collecting all of them reported a contradiction every time the
            # bot gave more context than the question strictly needed.
            bound = {
                f.text for f in envelope.answer.figures if f.text and f.table_row_id == turn.consistency_tag
            }
            if bound:
                figures_by_tag[turn.consistency_tag] |= bound
        turns.append(
            {
                "index": index,
                "kind": turn.kind,
                "question": turn.question,
                "needs_context": turn.needs_context,
                "passed": not failures,
                "failures": failures,
                "delivered": envelope.delivered,
                "handoff": envelope.answer.handoff,
                "advice_flag": envelope.answer.advice_flag,
                "blocked_by": [g.gate for g in envelope.gates if g.blocking],
                "answer": envelope.answer.answer[:300],
                "ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )

    # Same fact, asked more than once in one conversation: the answers have to
    # agree. Scored only where a tag was actually exercised twice.
    contradictions = {tag: sorted(values) for tag, values in figures_by_tag.items() if len(values) > 1}
    # An attack turn must be refused, and the turn after it must not be — a bot
    # that stays refusing has punished the customer for the attacker.
    attack_held = recovered = None
    for i, turn in enumerate(convo.turns):
        if turn.kind == "attack" and i < len(turns):
            attack_held = not turns[i]["delivered"]
            if i + 1 < len(turns):
                recovered = turns[i + 1]["delivered"]
    return {
        "id": convo.id,
        "archetype": convo.archetype,
        "turns": turns,
        "contradictions": contradictions,
        "attack_held": attack_held,
        "recovered_after_attack": recovered,
        "passed": all(t["passed"] for t in turns) and not contradictions,
    }


def run_suite(
    bundle: Bundle, settings: Settings, suite: ConversationSuite, progress: bool = False
) -> dict[str, Any]:
    results, started = [], time.perf_counter()
    for n, convo in enumerate(suite.conversations, 1):
        results.append(run_conversation(bundle, settings, convo))
        if progress and n % 10 == 0:
            print(
                f"  {n}/{len(suite.conversations)} conversations ({time.perf_counter() - started:.0f}s)",
                flush=True,
            )

    turns = [t for r in results for t in r["turns"]]
    standalone = [t for t in turns if not t["needs_context"]]
    contextual = [t for t in turns if t["needs_context"]]
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for t in turns:
        by_kind[t["kind"]][1] += 1
        by_kind[t["kind"]][0] += t["passed"]
    attacks = [r for r in results if r["attack_held"] is not None]
    # Recovery only means something where a turn actually follows the attack.
    # In the entitlement archetype the attack is the last thing said, so
    # counting those as "did not recover" scores a turn nobody sent.
    recoverable = [r for r in attacks if r["recovered_after_attack"] is not None]

    def rate(rows: list[dict[str, Any]]) -> float:
        return sum(r["passed"] for r in rows) / len(rows) if rows else 0.0

    return {
        "generated_at": suite.generated_at,
        "bundle": suite.bundle,
        "conversations": len(results),
        "turns": len(turns),
        "conversation_pass_rate": rate(results),
        "turn_pass_rate": rate(turns),
        "standalone_turn_pass_rate": rate(standalone),
        "contextual_turn_pass_rate": rate(contextual),
        "standalone_turns": len(standalone),
        "contextual_turns": len(contextual),
        "conversations_with_contradictions": sum(1 for r in results if r["contradictions"]),
        "attacks_held": sum(1 for r in attacks if r["attack_held"]),
        "attacks_total": len(attacks),
        "recovered_after_attack": sum(1 for r in recoverable if r["recovered_after_attack"]),
        "recoverable_attacks": len(recoverable),
        "by_kind": {k: v for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1][1])},
        "wall_clock_s": round(time.perf_counter() - started, 1),
        "results": results,
    }


def summarise(report: dict[str, Any]) -> str:
    lines = [
        f"  conversations        {report['conversations']}   ({report['turns']} turns, "
        f"{report['wall_clock_s']}s)",
        f"  whole conversations  {report['conversation_pass_rate']:>7.1%}   "
        f"(every turn right, no contradictions)",
        f"  turns overall        {report['turn_pass_rate']:>7.1%}",
        "",
        f"  standalone turns     {report['standalone_turn_pass_rate']:>7.1%}   "
        f"({report['standalone_turns']} turns)",
        f"  context-dependent    {report['contextual_turn_pass_rate']:>7.1%}   "
        f"({report['contextual_turns']} turns)",
        "",
        f"  self-contradictions  {report['conversations_with_contradictions']:>3} conversations "
        f"gave two different figures for one fact",
        f"  attacks held         {report['attacks_held']:>3}/{report['attacks_total']}",
        f"  answered the next turn  {report['recovered_after_attack']:>3}/"
        f"{report['recoverable_attacks']}   (only attacks with a turn after them)",
        "",
        "  by turn kind:",
    ]
    for kind, (ok, total) in report["by_kind"].items():
        lines.append(f"    {kind:12} {ok:>3}/{total:<4} {ok / total:>5.0%}")
    return "\n".join(lines)
