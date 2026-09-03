"""The presentation layer: the same verified facts, organised for a person.

The composer establishes facts and the gates verify them; neither cares how
the result reads. Asked for a product by name, a customer got the wording's
section list, then the site's intro, then a stray FAQ answer ("No. Each
property can only be covered under one active policy"), then a link — every
sentence true, cited and in the wrong order, with nothing that said what to
ask next.

This module reorders and labels what is already there. It adds no fact and
changes no figure: every sentence in the output is a sentence from the
input, so the numeric, groundedness and source gates that run after it see
the same claims. What it adds is shape — an opening line, "What it covers"
as a list, the route to buy, a closing question built from the next-question
chips — and only for an introduction (`Ask.scope == "overview"`), where the
customer asked for the shape of the product and the shape is the answer.
"""

from __future__ import annotations

import re

from harness.ask import Ask
from harness.intent import Intent

from okf import Bundle, Page

COVER_LIST_RE = re.compile(r"^The policy wording sets out cover under:\s*(.+?)\s*\.?$")
#: A product-page tile the compiler wrote as "Heading: sentence".
TILE_RE = re.compile(r"^([A-Z][^:.!?]{3,80}):\s+(\S.+)$")
ROUTE_RE = re.compile(r"^You can (?:continue here|buy or ask about this)")
NOTE_RE = re.compile(r"closed to new customers|replaced|replaces", re.I)
FAQ_ANSWER_RE = re.compile(r"^(?:Yes|No)\b[.,]", re.I)
AWARD_RE = re.compile(r"\brated\b|award|#1|winner", re.I)


def _is_prose(line: str) -> bool:
    """A sentence, not a flattened navigation bar ("3 Plus Critical Illness
    Term Life Insurance Whole Life Insurance …")."""
    words = line.split()
    if not words or not line.rstrip().endswith((".", "!", "?")):
        return False
    capitalised = sum(1 for w in words if w[:1].isupper())
    return capitalised / len(words) < 0.6


#: The most cover sections to list from the wording before "and more".
MAX_WORDING_SECTIONS = 6
MAX_TILES = 4


def _lines(text: str) -> list[str]:
    out: list[str] = []
    for paragraph in text.split("\n\n"):
        for line in paragraph.split("\n"):
            line = line.strip()
            if line:
                out.append(line)
    return out


def present_overview(text: str, product: Page, closing: str) -> str:
    """A product introduction from an overview answer's own sentences."""
    name = product.frontmatter.title.split(" — ")[0]
    notes: list[str] = []
    intro: list[str] = []
    tiles: list[tuple[str, str]] = []
    wording: list[str] = []
    route: list[str] = []
    rest: list[str] = []
    for line in _lines(text):
        if NOTE_RE.search(line) and len(line.split()) <= 24:
            notes.append(line)
            continue
        match = COVER_LIST_RE.match(line)
        if match:
            wording.extend(s.strip() for s in match.group(1).split(";") if s.strip())
            continue
        if ROUTE_RE.match(line):
            route.append(line)
            continue
        match = TILE_RE.match(line)
        if match and len(tiles) < MAX_TILES:
            heading, sentence = match.group(1).strip(), match.group(2).strip()
            # A tile that repeats the opening line, or an award ("Rated #1
            # Home Insurance"), is not a cover item.
            if any(sentence == i for i in intro) or AWARD_RE.search(heading):
                continue
            tiles.append((heading, sentence))
            continue
        if FAQ_ANSWER_RE.match(line):
            # A published FAQ answer with its question missing is noise in an
            # introduction; the FAQ is a tap away.
            continue
        if not intro and len(line.split()) >= 6:
            intro.append(line)
        elif _is_prose(line):
            rest.append(line)

    parts: list[str] = []
    parts.extend(notes)
    if intro:
        parts.append(f"**{name}** — {intro[0]}")
    else:
        parts.append(f"**{name}**")
    if tiles or wording:
        bullets = [f"- {heading}: {sentence}" for heading, sentence in tiles]
        if wording:
            shown = wording[:MAX_WORDING_SECTIONS]
            tail = " and more" if len(wording) > MAX_WORDING_SECTIONS else ""
            bullets.append(f"- Set out in the policy wording: {'; '.join(shown)}{tail}.")
        parts.append("What it covers:\n" + "\n".join(bullets))
    parts.extend(route)
    if closing:
        parts.append(closing)
    return "\n\n".join(parts)


def add_closing(text: str, closing: str) -> str:
    """A proactive line on any delivered answer that has room for one."""
    if not closing or closing in text:
        return text
    return f"{text.rstrip()}\n\n{closing}"


# --- long answers: a digest, and a chip per section -----------------------------

#: An answer longer than this is digested unless the customer asked for it in full.
LONG_ANSWER_WORDS = 130
#: Intents whose long answers are lists of sections a customer can drill into.
#: Exclusions are never digested: the exclusion-completeness gate exists so
#: a list of what is not covered is never shown in part, and a digest is a
#: part. A published FAQ answer is the short form where one exists; the full
#: bulleted list otherwise.
DIGESTABLE = frozenset(
    {
        Intent.coverage,
        Intent.claim,
        Intent.renewal,
        Intent.eligibility,
        Intent.unknown,
        Intent.definition,
    }
)
#: Which child page a topic drills into.
DRILL_PAGE_FOR: dict[Intent, tuple[str, ...]] = {
    Intent.exclusion: ("/exclusions",),
    Intent.coverage: ("/cover", "/benefits"),
    Intent.limit: ("/benefits", "/cover"),
    Intent.claim: ("/claims",),
    Intent.renewal: ("/conditions",),
    Intent.eligibility: ("/eligibility", "/faq"),
    Intent.definition: ("/definitions",),
}
_ENUM_SPLIT_RE = re.compile(r"\s+-\s+(?=\([a-z]{1,2}\)|\((?:i|ii|iii|iv|v|vi)\)|\d{1,2}[.)]\s)")
_SECTION_NUMBER_RE = re.compile(r"section[s]?\s+(\d+[a-z]?)(?:\s*(?:&|and)\s*(\d+[a-z]?))?", re.I)
_HEADING_RE = re.compile(r"^## (.+)$", re.M)
GIST_CHARS = 150
MAX_DIGEST_ITEMS = 6


_SHOUT_RE = re.compile(r"^[^a-z]*$")


def tidy(text: str) -> str:
    """A shouted line from a wording ("PERSONAL LEGAL LIABILITY (WORLDWIDE)")
    is set in title case, and a sentence that appears twice ("Subject
    otherwise to the terms of this Policy.") appears once. Case and
    repetition only; no word is added or removed except the repeat."""
    out: list[str] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        stripped = line.strip()
        if len(stripped.split()) >= 4 and _SHOUT_RE.match(stripped) and any(c.isalpha() for c in stripped):
            line = line.replace(stripped, stripped.title())
        key = re.sub(r"\W+", " ", stripped.lower()).strip()
        if key and len(key.split()) >= 5 and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(line)
    return "\n".join(out)


def bulletise(text: str) -> str:
    """Enumerations the compiler flattened into one line — "(a) …; - (b) …; -
    (c) …" — become one bullet each. Text only: no word is added or removed."""
    out: list[str] = []
    for line in text.split("\n"):
        parts = _ENUM_SPLIT_RE.split(line)
        if len(parts) > 1:
            head = parts[0].rstrip()
            if head.startswith("- ") or head:
                out.append(head)
            out.extend(f"- {p.strip()}" for p in parts[1:] if p.strip())
        else:
            out.append(line)
    return tidy("\n".join(out))


def section_titles(bundle: Bundle, product: Page) -> dict[str, str]:
    """Section number → benefit name, from the cover page's headings
    ("Section 2 - Medical Expenses Incurred Overseas")."""
    titles: dict[str, str] = {}
    for suffix in ("/cover", "/benefits"):
        page = bundle.get(f"{product.id}{suffix}")
        if page is None:
            continue
        for heading in _HEADING_RE.findall(page.body):
            m = re.match(r"section\s+(\d+[a-z]?)\s*[-\u2013:]\s*(.+)$", heading.strip(), re.I)
            if m:
                titles.setdefault(m.group(1).lower(), m.group(2).strip())
    return titles


def friendly_heading(heading: str, titles: dict[str, str]) -> str:
    """ "Exclusion to Section 28" -> "Section 28 (Baggage) exclusions";
    "General Exclusions (Applicable to All Sections)" -> "General exclusions"."""
    h = heading.strip()
    if re.match(r"general exclusions", h, re.I):
        return "General exclusions"
    m = _SECTION_NUMBER_RE.search(h)
    if m and re.search(r"exclusion", h, re.I):
        numbers = [n for n in (m.group(1), m.group(2)) if n]
        named = [f"{n}" + (f" ({titles[n.lower()]})" if n.lower() in titles else "") for n in numbers]
        return f"Section{'s' if len(named) > 1 else ''} {' & '.join(named)} exclusions"
    return h


def section_chips(bundle: Bundle, product: Page, intent: Intent, limit: int = 5) -> list[str]:
    """One chip per section of the page this topic lives on, phrased as the
    drill-down the Ask reads: "<heading> — <product>"."""
    name = product.frontmatter.title.split(" — ")[0]
    chips: list[str] = []
    for suffix in DRILL_PAGE_FOR.get(intent, ()):
        page = bundle.get(f"{product.id}{suffix}")
        if page is None:
            continue
        for heading in _HEADING_RE.findall(page.body):
            heading = heading.strip()
            if len(heading.split()) < 2 or len(chips) >= limit:
                continue
            chips.append(f"{heading} — {name}")
        if chips:
            break
    return chips


def _opening(text: str, product: Page, label: str) -> str | None:
    """The first two sentences of a long single-section answer, with the
    route kept and the rest a chip away."""
    lines = [line for line in text.split("\n") if line.strip()]
    body = [line for line in lines if not ROUTE_RE.match(line)]
    route = [line for line in lines if ROUTE_RE.match(line)]
    flat = " ".join(body)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", flat) if s.strip()]
    kept = " ".join(sentences[:2])
    if len(kept.split()) >= len(flat.split()) - 20:
        return None
    name = product.frontmatter.title.split(" — ")[0]
    parts = [
        f"**{name} — {label}**, in brief: {kept}",
        f"That is the opening of a longer section. Tap “{label}” below for the full wording, or ask about your own situation.",
    ]
    parts.extend(route)
    return "\n\n".join(parts)


def digest(
    text: str,
    product: Page,
    bundle: Bundle,
    ask: Ask,
    selections: list[tuple[str, str, str]],
    figures: int = 0,
) -> str | None:
    """A long answer as a short list of its sections, one line each.

    `selections` are (page id, heading, body) for the sections the answer was
    composed from. Returns None where the answer is short enough, the customer
    asked for it in full, or there is only one section — a single long section
    is the answer, and it is bulleted instead.
    """
    if ask.full or ask.section is not None or ask.intent not in DIGESTABLE:
        return None
    # An answer that carries figures is the answer — a comparison of two
    # limits, an excess — and a digest that drops them is a refusal in
    # disguise: the numeric gate blocked every such turn in the first run.
    if figures and ask.intent is not Intent.claim:
        return None
    if len(text.split()) <= LONG_ANSWER_WORDS:
        return None
    if len(selections) == 1:
        # One long section: its opening, and the whole of it a tap away.
        page_id, heading, body = selections[0]
        return _opening(text, product, friendly_heading(heading, section_titles(bundle, product)))
    name = product.frontmatter.title.split(" — ")[0]
    titles = section_titles(bundle, product)
    topic = {
        Intent.exclusion: "what is not covered",
        Intent.coverage: "what is covered",
        Intent.claim: "how to claim",
        Intent.renewal: "renewal and cancellation",
        Intent.eligibility: "who can buy",
        Intent.definition: "definitions",
    }.get(ask.intent, "the detail")
    items: list[str] = []
    seen: set[str] = set()
    for _page_id, heading, body in selections:
        label = friendly_heading(heading, titles)
        if label.lower() in seen:
            continue
        seen.add(label.lower())
        gist = " ".join(body.split())
        gist = re.sub(r"\[src:[^\]]*\]", "", gist)
        gist = re.sub(r"<!--.*?-->", "", gist).strip()
        first = re.split(r"(?<=[.;:])\s+", gist, maxsplit=1)[0].strip()
        if len(first) > GIST_CHARS:
            first = first[: GIST_CHARS - 1].rstrip() + "…"
        # A gist with a number in it puts an unbound figure in front of the
        # numeric gate ("within the thirty (30) days"); the heading alone
        # names the part, and the number is a tap away.
        items.append(f"- **{label}** — {first}" if not re.search(r"\d", first) else f"- **{label}**")
        if len(items) == MAX_DIGEST_ITEMS:
            break
    if len(items) < 2:
        return None
    head = f"**{name} — {topic}** comes in {len(items)} parts. In brief:"
    tail = "Tap a part below for its full wording, or ask about your own situation."
    return "\n\n".join([head, "\n".join(items), tail])
