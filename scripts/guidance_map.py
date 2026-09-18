"""Generate the guidance map from a bundle: `GUIDANCE-MAP.md` and an HTML page.

The map is what the assistant offers a customer before they type — the six
greeting branches, the line → plan → topic-ring browse tree, the next-chip
table per intent — and a coverage matrix of which ring topics each plan's
pages can answer. It is walked out of the bundle rather than written, so a
plan added to the catalogue joins the map without a code change and a topic
whose page is missing is shown as a gap rather than promised.

    make guidance-map
    uv run python scripts/guidance_map.py --bundle okf-real \\
        --md GUIDANCE-MAP.md --html .eval-reports/guidance-map.html

Three rules carry over from the suggestion chips the serve loop already
offers (`api.suggest`): every node is a literal question the corpus answers
or a registry destination, never model-written; a question node is offered
only where the page that answers it exists; and no node carries a digit,
because a number in an answer must bind to a benefit-table row.

The page's shell — styles, layout, the filter script — lives beside this file
in `guidance_map_template.html`; this module fills its `{{PLACEHOLDER}}`
slots from the bundle.
"""

from __future__ import annotations

import argparse
import html
from dataclasses import dataclass, field
from pathlib import Path

from okf import DESTINATIONS, Bundle, Desk, Page, PageType

TEMPLATE = Path(__file__).with_name("guidance_map_template.html")

LINE_LABEL: dict[str, str] = {
    "protection": "Life & protection",
    "health-medical": "Health & medical",
    "savings-retirement": "Savings & retirement",
    "investments": "Investments",
    "general": "General",
    "motor": "Motor",
    "business": "Business",
    "premier": "Premier",
    "scheme": "Schemes",
}
LINE_ORDER = [
    "general",
    "motor",
    "protection",
    "health-medical",
    "savings-retirement",
    "investments",
    "business",
    "premier",
    "scheme",
]
#: The general line files personal and commercial plans together; seventeen
#: chips is too many, so the map splits them for navigation only.
COMMERCIAL_GENERAL = frozenset({"casualty", "engineering", "marine", "miscellaneous", "property"})

#: The topic ring: (code, the question in the plan's name, what page it needs).
TOPICS: tuple[tuple[str, str, str], ...] = (
    ("coverage", "What does {p} cover?", "always"),
    ("exclusion", "What does {p} not cover?", "exclusions page"),
    ("limit", "What are the cover limits for {p}?", "benefits or cover page"),
    ("eligibility", "Who can buy {p}?", "eligibility or FAQ page"),
    ("application", "How do I buy {p}?", "a channel binding"),
    ("offer", "Is there a promotion for {p}?", "a live promotion page"),
    ("claim", "How do I make a claim on {p}?", "claims page or claim journey"),
    ("documents", "What documents do I need to claim on {p}?", "claims page or claim journey"),
    ("definition", "What do the terms in {p} mean?", "definitions page"),
    ("conditions", "How do I cancel or renew {p}?", "conditions page"),
)

#: After each intent, what to offer next and why — journey order, not the last
#: intent alone. Mirrors the table in DESIGN-v2.9.md §3 (A2).
NEXT_CHIPS: tuple[tuple[str, str, str], ...] = (
    (
        "a bare plan name (overview)",
        "not covered · limits · who can buy · how to buy",
        "evaluate, then apply",
    ),
    (
        "what it covers",
        "not covered · limits · how to claim · promotion",
        "the exclusions belong beside the cover",
    ),
    (
        "what it does not cover",
        "what it covers · how to claim · how to buy",
        "back to the positive, then forward",
    ),
    (
        "the limits",
        "not covered · who can buy · how to buy · compare",
        "a figure question is a buying question",
    ),
    ("who can buy", "how to buy · promotion · what it covers", "eligibility, then apply"),
    ("how to buy", "promotion · get a quote ↗ · what it covers", "apply, then price"),
    ("a promotion", "how to buy · what it covers · not covered", "offer, then apply"),
    ("how to claim", "documents needed · track my claim ↗ · not covered", "claim, evidence, status"),
    (
        "a claim-status or servicing handoff",
        "documents needed · what it covers · talk to a person ↗",
        "stay in the conversation after a handoff",
    ),
    (
        "a price handoff",
        "how to buy · what it covers · limits",
        "the quote is one tap away; the plan is still here",
    ),
    ("a definition", "what it covers · not covered", "back to the product"),
    (
        "a clarification (which plan?)",
        "the candidate plans · “I'm not sure which plan I have” ↗",
        "the options are the chips",
    ),
    ("off-topic, or a greeting", "the six branches", "the map"),
)

INCIDENTS = (
    "My flight was delayed",
    "The airline lost my bag",
    "Someone broke into my flat",
    "My helper is unwell",
    "I had a car accident",
    "My dog needs the vet",
)

GREETING = (
    "Hi. I answer from Etiqa's policy wordings and product pages, and I'll point you to the "
    "right place for anything about your own policy. Pick a branch or just ask."
)


@dataclass
class Plan:
    id: str
    key: str
    title: str
    aliases: list[str]
    topics: dict[str, bool]
    bound_rows: int


@dataclass
class Inventory:
    by_line: dict[str, list[Plan]] = field(default_factory=dict)
    concepts: list[str] = field(default_factory=list)
    journeys: list[str] = field(default_factory=list)
    promotions: int = 0
    desks: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def plans(self) -> list[Plan]:
        return [p for line in LINE_ORDER for p in self.by_line.get(line, [])]


def _plan(bundle: Bundle, page: Page) -> Plan:
    key = bundle.product_key(page)
    pid = page.id

    def has(suffix: str) -> bool:
        return bundle.get(f"{pid}{suffix}") is not None

    claims = has("/claims") or bundle.get(f"journey/claim/{key}") is not None
    promo = any(
        q.frontmatter.type is PageType.promotion and bundle.product_key(q) == key
        for q in bundle.pages.values()
    )
    rows = sum(1 for r in bundle.tables.rows if r.product == key)
    return Plan(
        id=pid,
        key=key,
        title=page.frontmatter.title.split(" — ")[0],
        aliases=list(page.frontmatter.aliases[:3]),
        topics={
            "coverage": True,
            "exclusion": has("/exclusions"),
            "limit": has("/benefits") or has("/cover"),
            "eligibility": has("/eligibility") or has("/faq"),
            "application": bool(page.frontmatter.channels),
            "offer": promo,
            "claim": claims,
            "documents": claims,
            "definition": has("/definitions"),
            "conditions": has("/conditions"),
        },
        bound_rows=rows,
    )


def inventory(bundle: Bundle) -> Inventory:
    inv = Inventory()
    roots = [
        p for p in bundle.pages.values() if p.frontmatter.type == PageType.product and p.id.count("/") == 2
    ]
    for page in sorted(roots, key=lambda p: p.frontmatter.title.lower()):
        inv.by_line.setdefault(page.id.split("/")[1], []).append(_plan(bundle, page))
    inv.concepts = sorted(p for p in bundle.pages if p.startswith("concept/"))
    inv.journeys = sorted(p for p in bundle.pages if p.startswith("journey/"))
    inv.promotions = sum(1 for p in bundle.pages.values() if p.frontmatter.type is PageType.promotion)
    inv.desks = {d.value: (DESTINATIONS[d].label, DESTINATIONS[d].url) for d in Desk}
    return inv


def concept_question(concept_id: str) -> str:
    return f"What does {concept_id.split('/')[-1].replace('-', ' ')} mean?"


def gaps(inv: Inventory) -> dict[str, list[str]]:
    plans = inv.plans
    return {
        "no_buy": [p.title for p in plans if not p.topics["application"]],
        "no_claim": [p.title for p in plans if not p.topics["claim"]],
        "no_eligibility": [p.title for p in plans if not p.topics["eligibility"]],
        "with_rows": [p.title for p in plans if p.bound_rows],
    }


# ------------------------------------------------------------------ Markdown


def render_markdown(inv: Inventory, bundle_name: str) -> str:
    out: list[str] = []
    w = out.append
    n = len(inv.plans)
    g = gaps(inv)
    cols = [t[0] for t in TOPICS]
    desks = inv.desks

    w("# Guidance map — what the assistant can be asked, and where it leads")
    w("")
    w(f"Generated from `{bundle_name}` by `scripts/guidance_map.py` (`make guidance-map`);")
    w("regenerate rather than edit. The reference for the greeting map, the browse tree and")
    w("the follow-on chips in `DESIGN-v2.9.md` §3 (A2). Every node is one of three things:")
    w("")
    w("| Mark | Node | What tapping it does |")
    w("|---|---|---|")
    w(
        "| ✎ | a question the corpus answers "
        "| sends that exact question; the reply is delivered, by construction |"
    )
    w("| ↗ | a destination | opens a registry address (portal, claims, renewal, promotions, contact) |")
    w(
        "| ◌ | a topic the pages do not hold for this plan "
        "| not shown as a chip; the reply falls to the steps |"
    )
    w("")
    w("No node is model-written, none carries a digit, and a ✎ node is offered only while the")
    w("page behind it is approved and in its effective window.")
    w("")
    w("## The greeting")
    w("")
    w(f"> {GREETING}")
    w("")
    w("Then six branches. The tree below is the full expansion.")
    w("")
    w("## Level 1 — the six branches")
    w("")
    w("```")
    w("Etiqa assistant")
    w("├─ Product information    what a plan covers, excludes, needs to claim on, who can buy it")
    w("├─ Claims                 how to claim, what to send, where to track it")
    w("├─ My policy              log in, renew, update, cancel — the customer's own record")
    w("├─ Buy and quote          the lines sold, how to buy, where a price comes from")
    w("├─ Promotions             live offers only")
    w("└─ Help and contact       a person, a scam report, app and login help")
    w("```")
    w("")
    w("## Branch 1 — Product information")
    w("")
    w(f"{n} approved plans across {len(inv.by_line)} lines. Line → plan → the topic ring. The ring has")
    w("the same shape for every plan; a topic appears only where the pages hold it (see the matrix).")
    w("")
    w("```")
    w("Product information")
    for line in LINE_ORDER:
        plans = inv.by_line.get(line, [])
        if not plans:
            continue
        w(f"├─ {LINE_LABEL.get(line, line)}")
        if line == "general":
            w("│   ├─ Personal")
            for p in plans:
                if p.key not in COMMERCIAL_GENERAL:
                    w(f"│   │   ├─ {p.title}")
            w("│   └─ Commercial")
            for p in plans:
                if p.key in COMMERCIAL_GENERAL:
                    w(f"│       ├─ {p.title}")
        else:
            for p in plans:
                w(f"│   ├─ {p.title}")
    w("├─ Tell me what happened          an incident names its line; the plans in that line are offered")
    w("│     " + " · ".join(f"✎ {i.lower()}" for i in INCIDENTS[:3]))
    w("│     " + " · ".join(f"✎ {i.lower()}" for i in INCIDENTS[3:]))
    w("└─ Insurance terms")
    for c in inv.concepts:
        w(f"      ✎ {concept_question(c)}")
    w("```")
    w("")
    w("### The topic ring (every plan)")
    w("")
    w("```")
    w("<Plan>")
    for _, q, needs in TOPICS:
        w(f"  ✎ {q.format(p='<Plan>'):<52} needs: {needs}")
    w("  ✎ Compare <Plan> with <another plan>                   compare intent (A4); a bound table")
    w("```")
    w("")
    w('After any ring answer the section chips of that page follow ("<heading> — <Plan>"), then')
    w('a "back to <line>" chip.')
    w("")
    w("## Branch 2 — Claims")
    w("")
    w("```")
    w("Claims")
    w("├─ ✎ How do I make a claim?                 → asks which plan, then that plan's steps")
    w("├─ ✎ What documents does a claim need?       → same, then the documents section")
    w("├─ ✎ How long does a claim take?             → only where a page states it; else the claims desk")
    w(f"├─ ↗ Track my claim                          {desks['portal'][0]}")
    w(f"├─ ↗ Submit a claim                          {desks['claims'][0]}")
    w("└─ Claim journeys the corpus holds:")
    for j in inv.journeys:
        w(f"      {j}")
    w("```")
    w("")
    w("## Branch 3 — My policy")
    w("")
    w("Destination-led by design: nothing here is in a policy document. The steps come from the")
    w("guidance table; the addresses from the registry.")
    w("")
    w("```")
    w("My policy")
    w(f"├─ ↗ Log in and view my policy               {desks['portal'][0]}")
    w(f"├─ ↗ Renew online                            {desks['renewal'][0]}  (general insurance only)")
    w("├─ Update my details                         steps → portal, then a person")
    w("│     nominee · address · contact · bank · add or remove a driver or dependant")
    w("├─ Cancel, refund, payments                  steps → portal, then a person")
    w("│     cancel · free-look · refund · pay by GIRO or card · premium due")
    w("├─ ✎ Where is the policy wording for <Plan>?     corpus: links the published document")
    w("└─ ✎ What is a policy schedule?              concept page")
    w("```")
    w("")
    w("## Branch 4 — Buy and quote")
    w("")
    w("```")
    w("Buy and quote")
    w("├─ ✎ Which kinds of insurance do you sell?   the lines, one example each")
    w("├─ ✎ I need <line> insurance                 the plans in that line")
    w("├─ ✎ How do I buy <Plan>?                    the channel route: online, agent, broker")
    w(
        "├─ ↗ Get a quote                             "
        'the plan\'s own page; "the price depends on your details"'
    )
    w("└─ ↗ Speak to an adviser                     a recommendation is a licensed adviser's call")
    w("```")
    w("")
    w("## Branch 5 — Promotions")
    w("")
    w(f"{inv.promotions} promotion pages compiled; only those inside their validity window are offered.")
    w("")
    w("```")
    w("Promotions")
    w("├─ ✎ Is there a promotion for <Plan>?        for the plans in the matrix with an offer")
    w(f"└─ ↗ All current promotions                  {desks['promotions'][0]}")
    w("```")
    w("")
    w("## Branch 6 — Help and contact")
    w("")
    w("```")
    w("Help and contact")
    w(f"├─ ↗ Talk to a person                        {desks['contact'][0]}")
    w("├─ ↗ Report a scam or suspicious message     a person only, never the portal")
    w("├─ ↗ App and portal help                     login · OTP · password → portal, then a person")
    w("├─ ✎ Who underwrites these policies?         entity page")
    w("└─ ✎ What can you help with?                 the capability reply")
    w("```")
    w("")
    w("## Along the way — what to offer after each answer")
    w("")
    w("Chips follow the customer's journey, not only the last intent, and a topic already answered")
    w("in this session is not offered again.")
    w("")
    w("| After the customer asked | Offer next, in order | Why this order |")
    w("|---|---|---|")
    for asked, offer, why in NEXT_CHIPS:
        w(f"| {asked} | {offer} | {why} |")
    w("")
    w("## Rules")
    w("")
    w("1. Nodes are generated from the bundle at load time, never written into code.")
    w("2. A ✎ node exists only if the page that answers it is approved and effective today.")
    w("3. No digits anywhere in a node; a number in an answer must bind to a row.")
    w("4. ↗ addresses come from the destination registry, never from retrieved text.")
    w("5. The map rides on the greeting envelope as a typed `map` field: each node carries `kind`")
    w("   (`question` | `destination`), `label`, and `question` or `url`; children nest. A plain")
    w("   client flattens it to chips.")
    w('6. The map is shown once. Per-turn chips take over; a "back to the start" chip returns.')
    w("7. Proved by a generated suite that asks every chip the map and the per-turn table offer and")
    w("   asserts each reply is delivered; and by two report rows, dead-end rate and chip coverage.")
    w("")
    w("## Coverage matrix — which ring topics each plan can answer")
    w("")
    w("✓ the pages hold it · they do not (the chip is omitted). *Bound rows* counts benefit-table")
    w("rows, which is what lets a limit answer carry a figure; a plan with none still answers a limit")
    w("question with the wording and no number.")
    w("")
    w("| Line | Plan | " + " | ".join(cols) + " | bound rows |")
    w("|---|---|" + "|".join("---" for _ in cols) + "|---|")
    for line in LINE_ORDER:
        for p in inv.by_line.get(line, []):
            marks = " | ".join("✓" if p.topics[c] else "·" for c in cols)
            w(f"| {LINE_LABEL.get(line, line)} | {p.title} | {marks} | {p.bound_rows or '·'} |")
    w("")
    w("## Content gaps the map makes visible")
    w("")
    w(f"- **No route to buy** on {len(g['no_buy'])} plans ({', '.join(g['no_buy'])}): the how-to-buy")
    w("  chip is missing and the application intent falls to the apply steps. A channel binding on")
    w("  each page closes it.")
    w(f"- **No claim steps** on {len(g['no_claim'])} plans ({', '.join(g['no_claim'])}): the claims chip")
    w("  is missing and the reply is the claims desk. A claims section or a claim journey page closes it.")
    w(f"- **No eligibility page or FAQ** on {len(g['no_eligibility'])} plans: who-can-buy falls to the")
    w("  eligibility steps. The eligibility table in `DESIGN-v2.9.md` §3 (C2) closes it with bound ages.")
    w(f"- **Benefit-table rows on {len(g['with_rows'])} of {n} plans** ({', '.join(g['with_rows'])}):")
    w("  everywhere else a limit answer is the wording with its figures trimmed. The single largest")
    w("  content gap, and a document-extraction problem rather than a retrieval one.")
    w("- **Policy servicing is uncompiled.** The crawled policy-services page is not a journey, so the")
    w("  My policy branch is desk-led. Compiling it into servicing journeys makes those steps corpus-backed.")
    w("- **The app has no corpus content.** App and portal help is two buttons until its pages are compiled.")
    w("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------- HTML


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _q(text: str) -> str:
    return f'<li class="n q"><span class="mk">✎</span><span>{_esc(text)}</span></li>'


def _d(inv: Inventory, text: str, desk: str) -> str:
    label, url = inv.desks[desk]
    link = f'<a class="desk" href="{_esc(url)}" target="_blank" rel="noopener">{_esc(label)}</a>'
    return f'<li class="n d"><span class="mk">↗</span><span>{_esc(text)} {link}</span></li>'


def _x(text: str) -> str:
    return f'<li class="n x"><span class="mk">◌</span><span>{_esc(text)}</span></li>'


def _note(text: str) -> str:
    return f'<li class="note">{_esc(text)}</li>'


def _details(title: str, sub: str, inner: str, open_: bool = False) -> str:
    sub_html = f'<span class="sub">{_esc(sub)}</span>' if sub else ""
    return (
        f'<details{" open" if open_ else ""}><summary><span class="t">{_esc(title)}</span>{sub_html}'
        f"</summary><ul>{inner}</ul></details>"
    )


def _plan_details(p: Plan) -> str:
    items = [_q(q.format(p=p.title)) if p.topics[code] else _x(q.format(p=p.title)) for code, q, _ in TOPICS]
    items.append(_q(f"Compare {p.title} with another plan"))
    items.append(_note("then the section chips of the page, and “back to the line”"))
    rows = f" · {p.bound_rows} bound rows" if p.bound_rows else ""
    sub = f"{', '.join(p.aliases)}{rows}" if p.aliases else ""
    return _details(p.title, sub, "".join(items))


def _group(title: str, plans: list[Plan], open_: bool = False) -> str:
    return _details(title, f"{len(plans)} plans", "".join(_plan_details(p) for p in plans), open_)


def _line_details(line: str, plans: list[Plan]) -> str:
    label = LINE_LABEL.get(line, line)
    if line != "general":
        return _group(label, plans)
    personal = [p for p in plans if p.key not in COMMERCIAL_GENERAL]
    commercial = [p for p in plans if p.key in COMMERCIAL_GENERAL]
    inner = _group("Personal", personal) + _group("Commercial", commercial)
    return _details(label, f"{len(plans)} plans", inner, open_=True)


def _matrix_row(line: str, p: Plan, cols: list[str]) -> str:
    cells = "".join(
        f'<td class="{"y" if p.topics[c] else "no"}">{"✓" if p.topics[c] else "·"}</td>' for c in cols
    )
    return (
        f'<tr><td class="ln">{_esc(LINE_LABEL.get(line, line))}</td><td class="pt">{_esc(p.title)}</td>'
        f'{cells}<td class="num">{p.bound_rows or "·"}</td></tr>'
    )


def render_html(inv: Inventory) -> str:
    n = len(inv.plans)
    g = gaps(inv)
    cols = [t[0] for t in TOPICS]
    q, note = _q, _note

    def d(text: str, desk: str) -> str:
        return _d(inv, text, desk)

    slots: dict[str, str] = {
        "N": str(n),
        "LINES": str(len(inv.by_line)),
        "GREETING": _esc(GREETING),
        "PROMOTIONS": str(inv.promotions),
        "CONCEPTS": str(len(inv.concepts)),
        "BRANCH_PRODUCT": (
            q("What does a plan cover? What is not covered? Who can buy it?")
            + note("browse by line → plan → the topic ring, below")
            + q("Tell me what happened — I'll find the plan")
            + q("What does a term mean? — excess, nomination, commencement date, policy schedule")
        ),
        "BRANCH_CLAIMS": (
            q("How do I make a claim?")
            + q("What documents does a claim need?")
            + q("How long does a claim take?")
            + d("Track my claim", "portal")
            + d("Submit a claim", "claims")
        ),
        "BRANCH_POLICY": (
            d("Log in and view my policy", "portal")
            + d("Renew online (general insurance)", "renewal")
            + d("Update my details or nominee — steps, then", "portal")
            + d("Cancel, refund, payments — steps, then", "contact")
            + q("Where is the policy wording for a plan?")
        ),
        "BRANCH_BUY": (
            q("Which kinds of insurance do you sell?")
            + q("How do I buy a plan?")
            + d("Get a quote — the price depends on your details", "portal")
            + d("Speak to an adviser — a recommendation is theirs to give", "contact")
        ),
        "BRANCH_PROMO": q("Is there a promotion for a plan?") + d("All current promotions", "promotions"),
        "BRANCH_HELP": (
            d("Talk to a person", "contact")
            + d("Report a scam or suspicious message — a person only", "contact")
            + d("App and portal help — login, OTP, password", "portal")
            + q("Who underwrites these policies?")
            + q("What can you help with?")
        ),
        "LINES_HTML": "".join(
            _line_details(line, inv.by_line[line]) for line in LINE_ORDER if line in inv.by_line
        ),
        "INCIDENT_HTML": (
            "".join(q(i) for i in INCIDENTS)
            + note(
                "the plans in that line are offered as chips; "
                "“I'm not sure which plan I have” goes to the portal"
            )
        ),
        "CONCEPT_HTML": "".join(q(concept_question(c)) for c in inv.concepts),
        "CLAIMS_EXPANDED": (
            q("How do I make a claim? — asks which plan, then that plan's steps")
            + q("What documents does a claim need? — then the documents section")
            + q("How long does a claim take? — only where a page states it")
            + d("Track my claim", "portal")
            + d("Submit a claim", "claims")
            + note("claim journeys compiled today:")
            + "".join(note(j) for j in inv.journeys)
        ),
        "NEXT_ROWS": "".join(
            f"<tr><td>{_esc(a)}</td><td>{_esc(o)}</td><td>{_esc(y)}</td></tr>" for a, o, y in NEXT_CHIPS
        ),
        "HEAD_CELLS": "".join(f"<th>{c}</th>" for c in cols),
        "MATRIX_ROWS": "".join(
            _matrix_row(line, p, cols) for line in LINE_ORDER for p in inv.by_line.get(line, [])
        ),
        "GAPS": "".join(
            f"<li><b>{_esc(head)}</b> {_esc(body)}</li>"
            for head, body in (
                (
                    f"No route to buy on {len(g['no_buy'])} plans",
                    f"({', '.join(g['no_buy'])}). The how-to-buy chip is missing; "
                    "a channel binding on each page closes it.",
                ),
                (
                    f"No claim steps on {len(g['no_claim'])} plans",
                    f"({', '.join(g['no_claim'])}). The claims chip is missing; "
                    "a claims section or a claim journey page closes it.",
                ),
                (
                    f"No eligibility page or FAQ on {len(g['no_eligibility'])} plans.",
                    "Who-can-buy falls to the eligibility steps; the eligibility table in "
                    "DESIGN-v2.9 (C2) closes it with bound ages.",
                ),
                (
                    f"Benefit-table rows on {len(g['with_rows'])} of {n} plans",
                    f"({', '.join(g['with_rows'])}). Everywhere else a limit answer is the wording "
                    "with its figures trimmed — the largest content gap, and a document-extraction problem.",
                ),
                (
                    "Policy servicing is uncompiled.",
                    "The crawled policy-services page is not a journey, so the My policy branch is "
                    "desk-led. Compiling it makes those steps corpus-backed.",
                ),
                (
                    "The app has no corpus content.",
                    "App and portal help is two buttons until its pages are compiled.",
                ),
            )
        ),
    }
    page = TEMPLATE.read_text(encoding="utf-8")
    for key, value in slots.items():
        page = page.replace("{{" + key + "}}", value)
    return page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the guidance map from a bundle.")
    parser.add_argument("--bundle", type=Path, default=Path("okf-real"))
    parser.add_argument("--md", type=Path, default=Path("GUIDANCE-MAP.md"))
    parser.add_argument("--html", type=Path, default=Path(".eval-reports/guidance-map.html"))
    args = parser.parse_args(argv)

    bundle = Bundle.load(args.bundle)
    inv = inventory(bundle)
    args.md.write_text(render_markdown(inv, args.bundle.name), encoding="utf-8")
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.html.write_text(render_html(inv), encoding="utf-8")
    g = gaps(inv)
    print(f"{len(inv.plans)} plans · {len(inv.by_line)} lines · {inv.promotions} promotions")
    print(f"wrote {args.md} and {args.html}")
    print(
        f"gaps: no route to buy {len(g['no_buy'])} · no claim steps {len(g['no_claim'])} · "
        f"no eligibility page {len(g['no_eligibility'])} · bound rows on {len(g['with_rows'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
