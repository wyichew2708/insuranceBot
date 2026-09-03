"""How much of each raw source reached a wiki page — per source, every compile.

The compile report counts what it skipped in aggregate ("49 paragraphs past
the 60-paragraph page cap") and says nothing about *which* source lost the
most. On the real bundle four words in five never reach a page. Some of that
is right — footers, article teasers, campaign paperwork — and some of it is
the very content customers ask for: the tiq.com.sg travel product page sat in
the corpus, in the travel page's own `authority` list, parsed cleanly into six
benefit tiles, and was read by nothing until a customer asked for a summary
and got two tier-gated figures.

This pass runs after the wiki is written. It attributes every sentence that
carries a `[src:<ref>#<anchor>]` to that source, counts the words, and writes
`coverage.json` beside the conflict tickets: per source, words in, words
reached, and the share. The compile command prints the largest sources with
the smallest share, so a human can read the top of the list and decide whether
what was dropped matters. It changes nothing about what is compiled.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from okf.linter import SOURCE_REF_RE

from okf import UNCOMPILED_MARK, Bundle

#: A source below this many words is a stub or a redirect; listing it as
#: "0% reached" would be noise at the top of the report.
MIN_SOURCE_WORDS = 120
#: How many sources the compile command prints.
REPORT_TOP = 15


@dataclass
class SourceCoverage:
    ref: str
    words: int
    reached: int = 0
    #: Wiki page ids that cite this source.
    pages: list[str] = field(default_factory=list)

    @property
    def share(self) -> float:
        return round(self.reached / self.words, 3) if self.words else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "words": self.words,
            "reached": self.reached,
            "share": self.share,
            "pages": sorted(self.pages),
        }


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\]])\s+(?=[A-Z\"'(\[{|-])")
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.S)
_MARKUP_RE = re.compile(r"\{\{[^}]*\}\}|<!--.*?-->|\[src:[^\]]*\]|[#|*_>`-]")


def _raw_words(path: Path) -> int:
    text = path.read_text(errors="replace")
    text = _FRONTMATTER_RE.sub("", text, count=1)
    return len(_MARKUP_RE.sub(" ", text).split())


def _attribute(body: str) -> dict[str, int]:
    """Words per source ref, over the sentences that cite it.

    A sentence that carries a ref is counted for that ref. A table row that
    carries one is counted the same way. Prose with no ref — the compiler's
    own connective lines, cross-references — is counted for nothing, which is
    correct: it reached the page from nowhere.
    """
    out: dict[str, int] = {}
    for line in body.splitlines():
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            refs = [m.group(1) for m in SOURCE_REF_RE.finditer(sentence)]
            if not refs:
                continue
            words = len(_MARKUP_RE.sub(" ", sentence).split())
            # A sentence with two refs is credited to each; the double count
            # is small and errs toward "reached", which is the safe direction
            # for a report whose job is to find what did not.
            for ref in refs:
                out[ref] = out.get(ref, 0) + words
    return out


def audit(bundle_root: Path) -> dict[str, SourceCoverage]:
    """Per raw source, how many of its words a wiki page cites."""
    raw_root = bundle_root / "raw"
    sources: dict[str, SourceCoverage] = {}
    for path in sorted(raw_root.rglob("*.md")):
        ref = path.relative_to(bundle_root).as_posix()
        sources[ref] = SourceCoverage(ref=ref, words=_raw_words(path))

    bundle = Bundle.load(bundle_root)
    for page in bundle.pages.values():
        if UNCOMPILED_MARK in page.body:
            continue
        for ref, words in _attribute(page.body).items():
            base = ref.split("#", 1)[0]
            source = sources.get(base)
            if source is None:
                continue
            source.reached += words
            if page.id not in source.pages:
                source.pages.append(page.id)
    for source in sources.values():
        # Two sentences citing the same span, or a page that quotes a source
        # twice, can push the count over the source's own length.
        source.reached = min(source.reached, source.words)
    return sources


def write_report(bundle_root: Path, sources: dict[str, SourceCoverage]) -> Path:
    total_words = sum(s.words for s in sources.values())
    total_reached = sum(s.reached for s in sources.values())
    payload = {
        "total": {
            "sources": len(sources),
            "words": total_words,
            "reached": total_reached,
            "share": round(total_reached / total_words, 3) if total_words else 0.0,
        },
        "sources": {ref: s.as_dict() for ref, s in sorted(sources.items())},
    }
    out = bundle_root / "coverage.json"
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    return out


def least_reached(sources: dict[str, SourceCoverage], top: int = REPORT_TOP) -> list[SourceCoverage]:
    """The sources with the most unreached words — the ones a human should
    read first."""
    candidates = [s for s in sources.values() if s.words >= MIN_SOURCE_WORDS]
    return sorted(candidates, key=lambda s: -(s.words - s.reached))[:top]


def describe(sources: dict[str, SourceCoverage], top: int = REPORT_TOP) -> str:
    total_words = sum(s.words for s in sources.values())
    total_reached = sum(s.reached for s in sources.values())
    share = total_reached / total_words if total_words else 0.0
    lines = [f"  corpus reach: {total_reached:,} of {total_words:,} words on a page ({share:.0%})"]
    lines.append("  largest unreached sources — read these first:")
    for s in least_reached(sources, top):
        lines.append(f"    {s.share:5.0%}  {s.words - s.reached:7,} words unreached  {s.ref}")
    return "\n".join(lines)
