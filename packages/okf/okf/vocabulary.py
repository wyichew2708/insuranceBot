"""Customer words for benefits the corpus names differently.

A page already carries `aliases` — "Tiq Travel", "trip insurance" — because the
name a customer uses is rarely the name a product page uses. This is the same
idea one level down. A customer whose suitcase went missing says "suitcase";
the benefit is called `baggage_loss`. A customer who cannot go home says
"somewhere to live"; the benefit is `alternative_accommodation`.

Nothing in the corpus bridges those, which is why the situational phrasings —
the ones where somebody describes what happened rather than naming a benefit —
retrieve the right *product* and then fail to find the right *section*. They are
also the most valuable questions the assistant gets, because a customer
mid-loss does not know the vocabulary.

Authored rather than inferred, and kept in the bundle rather than in code: it
is corpus content, it is reviewable beside the pages it serves, and a term that
turns out to mislead is edited by whoever owns the wording rather than by
whoever owns the retriever.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

#: `benefit_code` → the words customers use for it.
Vocabulary = dict[str, list[str]]

#: An abbreviation → the words it stands for. Insurance is written in initials
#: and customers type them: "ci product", "do you have PA cover", "which ILP".
#: The tokeniser drops anything under three characters, so "ci" never reached
#: retrieval at all — a question about critical illness was scored on the word
#: "product" alone, and matched an investment-linked plan.
Abbreviations = dict[str, str]


def load_vocabulary(bundle_root: Path) -> Vocabulary:
    """Read `vocabulary.yaml`, or an empty map if the bundle has none.

    Absent is a working state: without it, situational phrasings simply keep
    failing the way they do today rather than the bundle failing to load.
    """
    path = Path(bundle_root) / "vocabulary.yaml"
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    benefits = raw.get("benefits") if isinstance(raw, dict) else None
    if not isinstance(benefits, dict):
        return {}
    return {
        str(code): [str(term).lower() for term in terms if str(term).strip()]
        for code, terms in benefits.items()
        if isinstance(terms, list)
    }


def load_abbreviations(bundle_root: Path) -> Abbreviations:
    """Read the `abbreviations` section of `vocabulary.yaml`."""
    path = Path(bundle_root) / "vocabulary.yaml"
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    entries = raw.get("abbreviations") if isinstance(raw, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {
        str(short).lower(): str(long).lower()
        for short, long in entries.items()
        if str(short).strip() and str(long).strip()
    }


def expand_abbreviations(question: str, abbreviations: Abbreviations) -> str:
    """The question with every abbreviation spelled out beside its initials.

    Both forms are kept: "ci" is what the customer typed and may appear in the
    corpus verbatim ("covered CI"), and "critical illness" is what the product
    pages are titled. Dropping either loses matches.

    Matched only as a whole word — the `ci` in "special" or "decision" is not
    an abbreviation — and once per abbreviation, so a question that repeats it
    does not grow a paragraph of expansions.
    """
    if not abbreviations:
        return question
    text = question or ""
    for short, long in abbreviations.items():
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(short)}(?![a-z0-9])", re.I)
        if pattern.search(text):
            text = pattern.sub(f"{short} {long}", text, count=1)
    return text


def expand_vocabulary(question: str, vocabulary: Vocabulary) -> set[str]:
    """Benefit codes the question implies through customer vocabulary.

    Substring matching on purpose: "broken into" has to fire on "my place was
    broken into and things were taken", and requiring token equality would miss
    every multi-word term in the file.
    """
    text = (question or "").lower()
    return {code for code, terms in vocabulary.items() if any(term in text for term in terms)}
