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

from okf import Page

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
MAX_WORDING_SECTIONS = 8
MAX_TILES = 6


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
    for line in rest[:2]:
        parts.append(line)
    parts.extend(route)
    if closing:
        parts.append(closing)
    return "\n\n".join(parts)


def add_closing(text: str, closing: str) -> str:
    """A proactive line on any delivered answer that has room for one."""
    if not closing or closing in text:
        return text
    return f"{text.rstrip()}\n\n{closing}"
