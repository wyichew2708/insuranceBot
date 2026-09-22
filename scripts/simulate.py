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

#: `>chip` as a turn means "tap the first suggestion the bot offered". A turn
#: is a string, or `(text, expectations)` where the turn is one the product
#: owner named a rule for — see `SCENARIOS`.
CONVERSATIONS: list[tuple[str, list[object]]] = [
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

#: Five scenario families the product owner named, each written as a
#: conversation with *expectations* on the turn that matters. A weirdness
#: detector says "this reads wrong"; an expectation says "this is wrong" —
#: the difference matters, because a reply can be perfectly well-formed and
#: still answer about the wrong product, or refuse to compare at all.
#:
#: A turn is either a string or `(text, expectations)`. Keys:
#:   product    the product key the turn must resolve to
#:   layer2     how it was resolved: named | carried | ambiguous | guessed | none
#:   layer3     which handler owned it: compare | coverage | claims | ...
#:   clarifying False forbids "which product did you mean?"
#:   handoff    False forbids passing the turn to a person
#:   max_words  the reply must be no longer than this
#:   options    True: the reply must offer a way to go deeper (chips or sections)
#:   says       a regex the reply must contain — the caveat the case turns on
SCENARIOS: list[tuple[str, list[object]]] = [
    # 1 — comparison ---------------------------------------------------------
    (
        "compare-two-travel",
        [
            (
                "what is the difference between tiq travel insurance and travel infinite",
                {"layer3": "compare", "clarifying": False, "handoff": False},
            ),
        ],
    ),
    (
        "compare-life",
        [
            ("compare term life insurance and whole life insurance", {"layer3": "compare", "handoff": False}),
        ],
    ),
    (
        "compare-after-intro",
        [
            "tiq home insurance",
            ("how does it compare to hdb fire insurance", {"layer3": "compare", "handoff": False}),
        ],
    ),
    (
        "compare-which-better",
        [
            ("which is better, tiq travel or travel infinite?", {"layer3": "compare"}),
        ],
    ),
    (
        "compare-three",
        [
            ("compare tiq travel, travel infinite and travel takaful", {"layer3": "compare"}),
        ],
    ),
    (
        "compare-on-attribute",
        [
            ("tiq travel vs travel infinite on medical expenses", {"layer3": "compare"}),
        ],
    ),
    (
        "compare-vs-shorthand",
        [
            ("tiq home vs property insurance", {"layer3": "compare"}),
        ],
    ),
    # 2 — introducing a product ---------------------------------------------
    (
        "intro-tell-me-about",
        [
            (
                "tell me about tiq home insurance",
                {
                    "product": "home-insurance",
                    "clarifying": False,
                    "handoff": False,
                    "max_words": 180,
                    "options": True,
                },
            ),
        ],
    ),
    (
        "intro-what-is",
        [
            (
                "what is tiq personal accident",
                {"product": "personal-accident", "clarifying": False, "max_words": 180, "options": True},
            ),
        ],
    ),
    (
        "intro-never-bought",
        [
            (
                "i have never bought insurance before, what is pet insurance",
                {"product": "pet-insurance", "clarifying": False, "max_words": 180},
            ),
        ],
    ),
    (
        "intro-bare-name",
        [
            (
                "tiq maid insurance",
                {"product": "maid-insurance", "clarifying": False, "handoff": False, "options": True},
            ),
        ],
    ),
    (
        "intro-category",
        [
            ("tell me about your travel insurance", {"clarifying": False, "handoff": False}),
        ],
    ),
    (
        "intro-then-deeper",
        [
            ("tiq travel insurance", {"clarifying": False, "options": True}),
            (">chip", {"handoff": False}),
        ],
    ),
    # 3 — context retention --------------------------------------------------
    (
        "memory-cancellation",
        [
            "tiq travel",
            (
                "what is the cancellation policy",
                {"product": "travel-insurance", "layer2": "carried", "clarifying": False},
            ),
        ],
    ),
    (
        "memory-claim-pronoun",
        [
            "tiq home insurance",
            ("how do i claim it", {"product": "home-insurance", "layer2": "carried", "clarifying": False}),
            ("how long does it take", {"product": "home-insurance", "clarifying": False}),
        ],
    ),
    (
        "memory-four-turns",
        [
            "tiq travel insurance",
            ("what does it cover", {"product": "travel-insurance", "clarifying": False}),
            ("and the exclusions", {"product": "travel-insurance", "clarifying": False}),
            ("how much does it cost", {"product": "travel-insurance", "clarifying": False}),
        ],
    ),
    (
        "memory-price-ellipsis",
        [
            "pet insurance",
            ("how much", {"product": "pet-insurance", "clarifying": False}),
        ],
    ),
    (
        "memory-switch-not-revived",
        [
            "tiq travel insurance",
            "what about private car insurance",
            ("what is the excess", {"product": "private-car-insurance", "clarifying": False}),
        ],
    ),
    (
        "memory-eligibility-carried",
        [
            "tiq maid insurance",
            ("who can buy it", {"product": "maid-insurance", "layer2": "carried", "clarifying": False}),
        ],
    ),
    (
        "memory-renewal-carried",
        [
            "private car insurance",
            ("how do i renew", {"product": "private-car-insurance", "clarifying": False}),
        ],
    ),
    # 4 — answer length ------------------------------------------------------
    (
        "length-coverage",
        [
            ("what does tiq travel insurance cover", {"max_words": 180, "options": True}),
        ],
    ),
    (
        "length-exclusions",
        [
            ("what does tiq home insurance not cover", {"max_words": 220, "options": True}),
        ],
    ),
    (
        "length-everything",
        [
            ("tell me everything about tiq maid insurance", {"options": True}),
        ],
    ),
    (
        "length-claims",
        [
            ("how do i make a claim on private car insurance", {"max_words": 180, "options": True}),
        ],
    ),
    (
        "length-benefits",
        [
            ("what are the benefits of tiq personal accident", {"max_words": 180, "options": True}),
        ],
    ),
    # 5 — context awareness --------------------------------------------------
    (
        "aware-bought-after-cancel",
        [
            (
                "i bought insurance after flight was cancelled, am i eligible to claim?",
                {"says": r"aware|not be aware|knew|know", "handoff": False},
            ),
        ],
    ),
    (
        "aware-bought-after-cancel-variant",
        [
            (
                "my flight got cancelled yesterday and i bought travel insurance today, can i claim",
                {"says": r"aware|not be aware|knew|know"},
            ),
        ],
    ),
    (
        "aware-already-overseas",
        [
            (
                "I am at oversea, can i buy travel insurance?",
                {"says": r"before departing|before you depart|before leaving"},
            ),
        ],
    ),
    (
        "aware-already-overseas-variant",
        [
            (
                "im already in bangkok, can i still get tiq travel",
                {"says": r"before departing|before you depart|before leaving"},
            ),
        ],
    ),
    (
        "aware-loss-already-happened",
        [
            (
                "my laptop was stolen last week, can i buy personal cyber insurance and claim for it",
                {"says": r"aware|already|before|existing|happened"},
            ),
        ],
    ),
    (
        "aware-pre-existing-condition",
        [
            ("i have diabetes, will tiq travel cover my medical bills", {"handoff": False}),
        ],
    ),
    (
        "aware-pregnant",
        [
            ("i am 30 weeks pregnant, can i buy travel insurance", {}),
        ],
    ),
    (
        "aware-trip-started",
        [
            ("my trip already started, can i extend my tiq travel policy", {}),
        ],
    ),
    # 6 — plan-specific questions ------------------------------------------
    (
        "plan-travel-savvy",
        [
            (
                "what is the child limit on the tiq travel savvy plan",
                {"product": "travel-insurance", "clarifying": False, "says": r"Savvy plan"},
            ),
        ],
    ),
    (
        "plan-travel-entry",
        [
            ("tiq travel entry plan limits", {"product": "travel-insurance", "says": r"Entry plan"}),
        ],
    ),
    (
        "plan-maid-b",
        [
            (
                "tiq maid insurance plan b limits",
                {"product": "maid-insurance", "says": r"Plan B plan|Plan B"},
            ),
        ],
    ),
    (
        "plan-pet-pawfect",
        [
            ("pet insurance pawfect plan limits", {"product": "pet-insurance", "says": r"Pawfect"}),
        ],
    ),
    (
        "plan-pa-platinum",
        [
            (
                "tiq personal accident enhanced platinum limits",
                {"product": "personal-accident", "says": r"Enhanced Platinum"},
            ),
        ],
    ),
    (
        "plan-carried",
        [
            "tiq travel insurance",
            ("what about the luxury plan", {"product": "travel-insurance", "clarifying": False}),
        ],
    ),
    (
        "plan-unnamed-still-asks",
        [
            ("tiq travel child limit", {"product": "travel-insurance", "says": r"depends on your plan tier"}),
        ],
    ),
]

CONVERSATIONS += SCENARIOS

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
    turn: str,
    env: AnswerEnvelope,
    trace: Trace,
    names: dict[str, str],
    prior_product: str | None,
    router: dict[str, object] | None = None,
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
    comparing = bool(router) and (router or {}).get("layer3") == "compare"
    if product and not comparing:
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
    if (
        not env.delivered
        and a.handoff
        and ask.get("named_by") in ("title", "alias")
        and ask.get("intent") == "unknown"
    ):
        findings.append("bare product name handed off")
    return findings


#: A reply "offers options" when it ends with next-question chips or carries
#: section headings the customer can name back. Either is a way to go deeper;
#: a long answer with neither is a wall of text.
_SECTION_CHIP_RE = re.compile(r"^(?:- |\*\*|#{1,3} )", re.M)


def _offers_options(env: AnswerEnvelope) -> bool:
    return bool(env.answer.suggestions) or bool(_SECTION_CHIP_RE.search(env.answer.answer or ""))


def check(
    expect: dict[str, object], env: AnswerEnvelope, ask: dict[str, object], router: dict[str, object]
) -> list[str]:
    """Findings for the expectations this turn carried. Empty means it met them."""
    out: list[str] = []
    a = env.answer
    text = a.answer or ""
    want_product = expect.get("product")
    if want_product:
        got = ask.get("product") or router.get("product") or "none"
        if got != want_product:
            out.append(f"wrong product: wanted {want_product}, got {got}")
    want_l2 = expect.get("layer2")
    if want_l2 and router.get("layer2") != want_l2:
        out.append(f"wrong resolution: wanted layer2={want_l2}, got {router.get('layer2')}")
    want_l3 = expect.get("layer3")
    if want_l3 and router.get("layer3") != want_l3:
        out.append(f"wrong handler: wanted layer3={want_l3}, got {router.get('layer3')}")
    if expect.get("clarifying") is False and a.clarifying:
        out.append("clarified when the turn was already clear")
    if expect.get("handoff") is False and a.handoff:
        out.append("handed off a turn the corpus can answer")
    cap = expect.get("max_words")
    if isinstance(cap, int):
        words = len(text.split())
        if words > cap:
            out.append(f"over length: {words} words, budget {cap}")
    if expect.get("options") and not _offers_options(env):
        out.append("no way to go deeper: neither chips nor sections")
    says = expect.get("says")
    if isinstance(says, str) and not re.search(says, text, re.I):
        out.append(f"missing the caveat this turn turns on (/{says}/)")
    return out


def run(
    bundle: Bundle, settings: Settings, live: bool, only: str = ""
) -> tuple[list[dict[str, object]], int]:
    names = _product_names(bundle)
    results: list[dict[str, object]] = []
    turns_total = 0
    for name, script in CONVERSATIONS:
        if only and not name.startswith(only):
            continue
        session = Session(
            session_id=f"sim-{name}-{int(time.time())}",
            channel=Channel("channel/direct"),
            auth_level=AuthLevel("L0"),
        )
        history: list[str] = []
        last_chips: list[str] = []
        prior_product: str | None = None
        for step in script:
            raw: str
            expect: dict[str, object]
            raw, expect = step if isinstance(step, tuple) else (step, {})
            turn = last_chips[0] if raw == ">chip" and last_chips else raw
            if raw == ">chip" and not last_chips:
                results.append(
                    {"conversation": name, "turn": raw, "reply": "", "findings": ["no chip to tap"]}
                )
                continue
            t0 = time.perf_counter()
            env, trace = answer_question(bundle, turn, session, settings, history=history)
            elapsed = time.perf_counter() - t0
            turns_total += 1
            ask = next((s.detail for s in trace.stages if s.name == "ask"), {})
            router = next((s.detail for s in trace.stages if s.name == "router"), {})
            findings = detect(turn, env, trace, names, prior_product, router)
            findings += check(expect, env, ask, router)
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
                        "router": f"{router.get('layer1')}/{router.get('layer2')}/{router.get('layer3')}",
                    }
                )
    return results, turns_total


def _findings(result: dict[str, object]) -> list[object]:
    """The findings list, typed. `results` rows are `dict[str, object]`
    because they hold strings, floats and lists together, so every read
    of a list-valued key needs this said once rather than at each use."""
    found = result.get("findings")
    return list(found) if isinstance(found, list) else []


def write_report(
    path: Path,
    results: list[dict[str, object]],
    turns: int,
    live: bool,
    head: str,
    conversations: int = 0,
) -> None:
    lines = [
        "# Conversation simulation",
        "",
        f"- build `{head}` · {'live model' if live else 'deterministic'} · "
        f"{conversations or len(CONVERSATIONS)} conversations, {turns} turns",
        f"- **{len(results)} turns with a finding**",
        "",
    ]
    by_kind: dict[str, int] = {}
    for r in results:
        for f in _findings(r):
            kind = str(f).split(":")[0]
            by_kind[kind] = by_kind.get(kind, 0) + 1
    lines.append("| finding | turns |")
    lines.append("|---|---|")
    for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {kind} | {n} |")
    lines.append("")
    lines.append("## By scenario family")
    lines.append("")
    families = {
        "compare": "1 · comparing products",
        "intro": "2 · introducing a product",
        "memory": "3 · context retention",
        "length": "4 · answer length",
        "aware": "5 · context awareness",
    }
    lines.append("| family | turns with a finding |")
    lines.append("|---|---:|")
    for prefix, label in families.items():
        n = sum(1 for r in results if str(r["conversation"]).startswith(prefix))
        lines.append(f"| {label} | {n} |")
    lines.append("")
    for r in results:
        lines.append(f"## {r['conversation']} — {r['turn']!r}")
        lines.append("")
        for f in _findings(r):
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
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bundle", type=Path, default=Path("okf-real"))
    parser.add_argument("--out", type=Path, default=Path(".eval-reports/v22/simulation.md"))
    parser.add_argument("--live", action="store_true", help="use the model configured in .env")
    parser.add_argument("--only", default="", help="run only conversations whose name starts with this")
    args = parser.parse_args()
    if not args.live:
        os.environ["LLM_PROVIDER"] = "deterministic"
        os.environ["GUARDRAILS"] = "rules"
    settings = Settings(bundle_path=args.bundle)
    bundle = Bundle.load(args.bundle)
    results, turns = run(bundle, settings, args.live, args.only)
    head = os.popen("git rev-parse --short HEAD").read().strip()
    ran = sum(1 for n, _ in CONVERSATIONS if not args.only or n.startswith(args.only))
    write_report(args.out, results, turns, args.live, head, ran)
    print(f"{len(results)} turns with findings out of {turns} → {args.out}")
    for r in results:
        print(f"  {r['conversation']:28} {str(r['turn'])[:40]:40} {r['findings']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
