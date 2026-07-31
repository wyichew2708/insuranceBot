"""Grep-guard (§13): no hard-coded URLs, hotlines, promo codes, coverage
figures, or brand contact facts inside application code. Facts live only in
bundle fixtures, the actions table, or crawler output."""

import re
from pathlib import Path

APPS = Path(__file__).parent.parent / "apps"

FORBIDDEN = [
    ("SG phone literal", re.compile(r"\+65\s?\d")),
    ("currency amount literal", re.compile(r"S\$\s?\d")),
    ("SG SWIFT code literal", re.compile(r"\b[A-Z]{4}SG[A-Z0-9]{2,5}\b")),
    ("promo code literal", re.compile(r"promo[_ ]?code\s*=\s*['\"][A-Z0-9]")),
    ("brand site URL literal", re.compile(r"https?://(www\.)?(etiqa|tiq)\.com\.sg")),
]

SCANNED_SUFFIXES = {".py", ".ts", ".tsx", ".json"}


def test_no_hardcoded_facts_in_apps() -> None:
    offenders: list[str] = []
    for path in APPS.rglob("*"):
        if path.suffix not in SCANNED_SUFFIXES or "node_modules" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        text = path.read_text(errors="ignore")
        for label, pattern in FORBIDDEN:
            for match in pattern.finditer(text):
                offenders.append(f"{path.relative_to(APPS)}: {label}: {match.group()!r}")
    assert not offenders, "hard-coded facts found in apps/:\n" + "\n".join(offenders)
