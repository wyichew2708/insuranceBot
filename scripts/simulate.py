"""Conversation simulations: talk to the bot the way customers do, and flag
what reads wrong.

Not a correctness suite — the field test and the FAQ suite are that. This
runs scripted multi-turn conversations across the catalogue and applies a
set of *weirdness detectors* to each reply: too long, a duplicated sentence,
page furniture ("Buy Now"), a shouted line, a clarification on a product the
customer named, a handoff on a coverage question about a named product, an
answer that names another product, no chips, an introduction with no closing
question, a chip that leads to a handoff, personal data echoed back. Each hit
is a finding with the conversation, the turn and the reply, written to a
Markdown report a person can read in five minutes.

Deterministic by default (no model), so it runs in a minute and on every
build; `--live` uses whatever `.env` configures.

    uv run python scripts/simulate.py --bundle okf-real --out .eval-reports/v22/simulation.md
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / p) for p in ("apps/api", "packages/harness", "packages/okf")]

from api.pipeline import answer_question  # noqa: E402
from api.settings import Settings  # noqa: E402
from harness import AnswerEnvelope, AuthLevel, Channel, Session, Trace  # noqa: E402

from okf import Bundle, PageType  # noqa: E402

#: `>chip` as a turn means "tap the first suggestion the bot offered".
CONVERSATIONS: list[tuple[str, list[str]]] = [
    ("home-intro-then-chips", ["hi", "tiq home", ">chip", ">chip"]),
    ("travel-exclusions-then-drill", ["tiq travel", "What does Tiq Travel Insurance not cover?", ">chip"]),
    ("travel-claim", ["How do I make a claim on Tiq Travel Insurance?", "what documents do i need"]),
    ("maid-basics", ["maid insurance", "how much is it", "is there a promotion"]),
    ("car-typo", ["car insurnace coverage", "what is the excess", "NCD?"]),
    ("pet-elliptical", ["pet insurance", "and the exclusions?", "how to claim"]),
    ("cancer-single", ["i want to buy cancer insurance", "what does it cover", "who can buy"]),
    ("home-burglary", ["does tiq home cover burglary", "what about renovation", "how do i buy"]),
    ("pa-replaced", ["personal mobility insurance", "what does it cover"]),
    ("legacy", ["eeasy savepro", "can i still buy it"]),
    ("term-life", ["term life insurance", "how much cover can i get", "what is not covered"]),
    ("invest", ["tiq invest", "what are the charges", "can i withdraw"]),
    ("travel-covid", ["tiq travel covid coverage", "is covid covered"]),
    ("travel-full", ["show everything: what does tiq travel insurance not cover"]),
    ("offtopic", ["what is the capital of france", "ok what about travel insurance then"]),
    ("pii", ["my nric is S1234567A, does tiq home cover flood"]),
    ("advice", ["which plan should i buy for my family", "tiq home"]),
    ("category", ["do you have business insurance", "casualty", "what does it cover"]),
    ("fire", ["hdb fire insurance", "how do i claim"]),
    ("motorcycle", ["motorbike insurance", "what does it not cover"]),
    ("cyber", ["personal cyber insurance", "what is covered", "how much is it"]),
    ("endowment", ["3 year endowment plan", "what is the return", "can i cancel"]),
    ("whole-life", ["whole life insurance", "what is the difference from term life"]),
    ("travel-infinite", ["travel infinite", "what plans are there"]),
    ("greeting-only", ["hello", "thanks", "bye"]),
]

LONG_WORDS = 160
FURNITURE_RE = re.compile(r"buy now|read more|learn more|click here|follow us|\|", re.I)
SHOUT_RE = re.compile(r"\b[A-Z]{4,}(?:\s+[A-Z]{3,}){3,}\b")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 5]


def _product_names(bundle: Bundle) -> dict[str, str]:
    out = {}
    for p in bundle.pages.values():
        if p.frontmatter.type is PageType.product and p.id.count("/") == 2:
            out[bundle.product_key(p)] = p.frontmatter.title.split(" — ")[0]
    return out


def detect(
    turn: str, env: AnswerEnvelope, trace: Trace, names: dict[str, str], prior_product: str | None
) -> list[str]:
    a = env.answer
    text = a.answer or ""
    findings: list[str] = []
    ask = next((s.detail for s in trace.stages if s.name == "ask"), {})
    product = ask.get("product")
    words = len(text.split())
    if words > LONG_WORDS and not ask.get("full"):
        findings.append(f"long answer: {words} words")
    seen = set()
    for s in _sentences(text):
        key = s.lower()
        if key in seen:
            findings.append(f"duplicated sentence: {s[:60]!r}")
            break
        seen.add(key)
    if FURNITURE_RE.search(text):
        findings.append("page furniture in the answer")
    if SHOUT_RE.search(text):
        findings.append("shouted line in the answer")
    if a.clarifying and ask.get("named_by") in ("title", "alias", "fuzzy"):
        findings.append("asked which product on a named product")
    if a.handoff and product and ask.get("intent") in ("coverage", "exclusion", "unknown"):
        findings.append(f"handoff on a named product ({product}, {ask.get('intent')})")
    if product:
        others = [n for k, n in names.items() if k != product and n.lower() in text.lower()]
        if others and not a.clarifying:
            findings.append(f"names another product: {others[:2]}")
    if env.delivered and not a.handoff and not a.smalltalk and not a.suggestions:
        findings.append("no next-question chips")
    if ask.get("scope") == "overview" and env.delivered and not a.handoff and "?" not in text[-200:]:
        findings.append("introduction without a closing question")
    if re.search(r"\b[STFGM]\d{7}[A-Z]\b", text):
        findings.append("personal data echoed")
    if "[unavailable]" in text or "{{" in text:
        findings.append("unresolved placeholder shown")
    if not env.delivered and a.handoff and ask.get("named_by") in ("title", "alias") and ask.get("intent") == "unknown":
        findings.append("bare product name handed off")
    return findings


def run(bundle: Bundle, settings: Settings, live: bool) -> tuple[list[dict[str, object]], int]:
    names = _product_names(bundle)
    results: list[dict[str, object]] = []
    turns_total = 0
    for name, script in CONVERSATIONS:
        session = Session(session_id=f"sim-{name}-{int(time.time())}", channel=Channel("channel/direct"), auth_level=AuthLevel("L0"))
        history: list[str] = []
        last_chips: list[str] = []
        prior_product: str | None = None
        for raw in script:
            turn = last_chips[0] if raw == ">chip" and last_chips else raw
            if raw == ">chip" and not last_chips:
                results.append({"conversation": name, "turn": raw, "reply": "", "findings": ["no chip to tap"]})
                continue
            t0 = time.perf_counter()
            env, trace = answer_question(bundle, turn, session, settings, history=history)
            elapsed = time.perf_counter() - t0
            turns_total += 1
            findings = detect(turn, env, trace, names, prior_product)
            ask = next((s.detail for s in trace.stages if s.name == "ask"), {})
            prior_product = ask.get("product") or prior_product
            last_chips = list(env.answer.suggestions)
            history.append(turn)
            if findings:
                results.append(
                    {
                        "conversation": name,
                        "turn": turn,
                        "reply": env.answer.answer[:600],
                        "findings": findings,
                        "seconds": round(elapsed, 1),
                        "chips": last_chips,
                    }
                )
    return results, turns_total


def write_report(path: Path, results: list[dict[str, object]], turns: int, live: bool, head: str) -> None:
    lines = [
        "# Conversation simulation",
        "",
        f"- build `{head}` · {'live model' if live else 'deterministic'} · {len(CONVERSATIONS)} conversations, {turns} turns",
        f"- **{len(results)} turns with a finding**",
        "",
    ]
    by_kind: dict[str, int] = {}
    for r in results:
        for f in r["findings"]:  # type: ignore[union-attr]
            kind = str(f).split(":")[0]
            by_kind[kind] = by_kind.get(kind, 0) + 1
    lines.append("| finding | turns |")
    lines.append("|---|---|")
    for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {kind} | {n} |")
    lines.append("")
    for r in results:
        lines.append(f"## {r['conversation']} — {r['turn']!r}")
        lines.append("")
        for f in r["findings"]:  # type: ignore[union-attr]
            lines.append(f"- {f}")
        lines.append("")
        reply = str(r.get("reply", "")).replace("\n", "\n> ")
        if reply:
            lines.append(f"> {reply}")
            lines.append("")
        if r.get("chips"):
            lines.append(f"chips: {r['chips']}")
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bundle", type=Path, default=Path("okf-real"))
    parser.add_argument("--out", type=Path, default=Path(".eval-reports/v22/simulation.md"))
    parser.add_argument("--live", action="store_true", help="use the model configured in .env")
    args = parser.parse_args()
    if not args.live:
        os.environ["LLM_PROVIDER"] = "deterministic"
        os.environ["GUARDRAILS"] = "rules"
    settings = Settings(bundle_path=args.bundle)
    bundle = Bundle.load(args.bundle)
    results, turns = run(bundle, settings, args.live)
    head = os.popen("git rev-parse --short HEAD").read().strip()
    write_report(args.out, results, turns, args.live, head)
    print(f"{len(results)} turns with findings out of {turns} → {args.out}")
    for r in results:
        print(f"  {r['conversation']:28} {str(r['turn'])[:40]:40} {r['findings']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
