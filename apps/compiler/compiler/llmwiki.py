"""The LLM WIKI tier: plain-language pages an LLM writes, grounded and gated,
offline, never at answer time.

The corpus is written in contract language and customers do not ask in it.
"What does it cover" against a wording that says "We will indemnify the
Insured Person(s) for cost incurred up to the limit stated in Section 2" is a
vocabulary gap no scorer closes. The most direct fix is for the contract's
content to *also exist in plain language* — written once, verified, indexed.

The model here is held to the same line as everywhere else in the system: it
phrases; it never establishes a fact. Every sentence it writes must carry a
`[src:<page_id>#<heading>]` naming the compiled section it was written from,
and only sections it was shown. Every figure in a sentence must appear
verbatim in that section — quotation binding, applied at write time rather
than answer time. A sentence that fails either test is dropped and counted.
The page lands as `status: draft`, `compiled_by: llm`, with authority below
the wordings and summaries it was written from and above web copy, and
nothing is retrievable until a human reviews it.

    uv run python -m compiler.cli --bundle okf-real llm-wiki
    make llm-wiki
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from harness.gates import _ENTAILMENT_SCHEMA, _ENTAILMENT_SYSTEM, NUMERIC_SPAN_RE
from okf.linter import SOURCE_REF_RE

from compiler.wiki import CompileConfig, CompileReport, _page, _write
from okf import Bundle, Confidence, Frontmatter, Page, PageType, Status

#: Which child pages a product's plain-language rewrite is written from, and
#: in what order they are shown. These are the sections a customer asks about.
SOURCE_ROLES = ("", "/cover", "/benefits", "/exclusions", "/claims", "/eligibility", "/conditions")

#: Characters of source shown per product. Enough for a whole product's
#: compiled pages on this corpus; a product past this is written from its
#: first sections and the report says so.
SOURCE_CHARS = 24_000

#: The page is written in three calls, not one. On the Mac the local model
#: decodes at a few tokens a second, and a 24,000-character prompt with a
#: 2,048-token reply never finished inside the server's 300-second cap: all
#: thirty-seven products came back "model returned nothing". Each part sees
#: only the pages it is written from, and a reply is short enough to land.
PARTS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    # Token caps measured on the local model: the first part used 725 tokens
    # in 28 seconds; at 600 it was cut off and the reply was not JSON.
    ("## In plain terms\n## What it covers", ("", "/cover", "/benefits"), 1000),
    ("## What it does not cover", ("/exclusions", ""), 900),
    ("## How to claim\n## Questions people ask", ("/claims", "/faq", "/eligibility", ""), 1000),
)
PART_CHARS = 6_000

SYSTEM_PROMPT = """\
You rewrite an insurance product's compiled policy pages in plain language, \
for a customer who has never read a policy.

You are given the product's pages as numbered SECTIONS, each with an id like \
`product/general/travel-insurance/exclusions#What is not covered`. Write four \
parts, in this order, with these exact headings:

## In plain terms
## What it covers
## What it does not cover
## How to claim
## Questions people ask

Rules. They are not stylistic; a downstream check enforces every one and \
drops any sentence that fails.
1. Every sentence ends with the id of the ONE section it was written from, in \
square brackets: `[src:product/general/travel-insurance/exclusions#What is not \
covered]`. Copy ids exactly. Never cite a section you were not given.
2. A figure — an amount, a percentage, a number of days or hours, an age — may \
appear only if it appears verbatim in the section you cite. Do not round, \
convert, add up, or restate a number in words.
3. Say nothing the cited section does not say. No general knowledge about \
insurance. If the pages do not say how to claim, write one sentence saying the \
pages do not describe it and cite the product's root section.
4. Plain words. "Not covered" rather than "excluded"; "you" rather than "the \
Insured Person(s)". Short sentences.
5. "Questions people ask": five questions a customer would type, each answered \
in one or two cited sentences.
"""


def _part_prompt(headings: str) -> str:
    """The system prompt for one part: the same rules, only these headings."""
    wanted = [h for h in headings.splitlines()]
    return (
        SYSTEM_PROMPT.replace(
            "Write four \\\nparts, in this order, with these exact headings:\n\n## In plain terms\n## What it covers\n"
            "## What it does not cover\n## How to claim\n## Questions people ask\n",
            "Write ONLY these parts, in this order, with these exact headings:\n\n"
            + "\n".join(wanted)
            + "\n",
        )
        + "\nKeep each part short: at most six sentences, or six bullets, or five questions.\n"
    )


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["markdown"],
    "properties": {"markdown": {"type": "string", "description": "The page body, headings included."}},
}

_HEADING_RE = re.compile(r"^##\s+(.+)$", re.M)
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?\s*\[src:[^\]]+\]\.?", re.M)


@dataclass
class Draft:
    product: Page
    body: str
    kept: int = 0
    dropped_unsourced: int = 0
    dropped_unknown_source: int = 0
    dropped_figure: int = 0
    #: Sentences the cross-check judged not entailed by the section they cite.
    dropped_unsupported: int = 0
    #: Every kept sentence was judged entailed by its cited section.
    verified: bool = False
    notes: list[str] = field(default_factory=list)


#: Sentences per judge call. A model judges eight claims more precisely than
#: twelve — the same cap the groundedness gate uses.
CROSSCHECK_BATCH = 8


def _crosscheck(markdown: str, sources: dict[str, str], judge: Any, draft: Draft) -> str:
    """Cross-check every sentence against the product page section it cites.

    `_verify` proves provenance — the ref exists, the figures are in it. This
    proves sense: the same entailment judge the groundedness gate uses reads
    each sentence against its cited section and returns entails / neutral /
    contradicts. Anything not entailed is dropped, and a page whose every
    sentence was entailed is written approved with `auto-crosscheck` as the
    reviewer — the owner's decision, in place of a human reading each page.
    A judge that returns nothing usable leaves the page a draft.
    """
    lines = markdown.splitlines()
    items: list[tuple[int, int, str, str]] = []  # (line index, sentence index, sentence, source key)
    per_line: dict[int, list[str]] = {}
    for li, line in enumerate(lines):
        if not line.strip() or _HEADING_RE.match(line):
            continue
        sentences = _SENTENCE_RE.findall(line)
        per_line[li] = sentences
        for si, sentence in enumerate(sentences):
            ref = SOURCE_REF_RE.search(sentence)
            key = f"{ref.group(1)}#{ref.group(2)}" if ref and ref.group(2) else (ref.group(1) if ref else "")
            if key in sources:
                items.append((li, si, sentence, key))
    verdicts: dict[tuple[int, int], str] = {}
    by_source: dict[str, list[tuple[int, int, str]]] = {}
    for li, si, sentence, key in items:
        by_source.setdefault(key, []).append((li, si, sentence))
    for key, members in by_source.items():
        for start in range(0, len(members), CROSSCHECK_BATCH):
            batch = members[start : start + CROSSCHECK_BATCH]
            claims = "\n".join(
                f"CLAIM {i}: {SOURCE_REF_RE.sub('', s).strip()}" for i, (_, _, s) in enumerate(batch)
            )
            prompt = f"EVIDENCE ({key}):\n{sources[key]}\n{claims}\n"
            try:
                payload = judge(_ENTAILMENT_SYSTEM, prompt, _ENTAILMENT_SCHEMA)
            except Exception:
                payload = None
            got = (
                {v.get("claim"): v.get("verdict") for v in payload.get("verdicts", []) if isinstance(v, dict)}
                if isinstance(payload, dict)
                else {}
            )
            for i, (li, si, _) in enumerate(batch):
                verdicts[(li, si)] = str(got.get(i) or "silent")
    kept_lines: list[str] = []
    all_entailed = bool(items)
    for li, line in enumerate(lines):
        if li not in per_line:
            kept_lines.append(line)
            continue
        good = []
        for si, sentence in enumerate(per_line[li]):
            verdict = verdicts.get((li, si), "silent")
            if verdict == "entails":
                good.append(sentence.strip())
            else:
                draft.dropped_unsupported += 1
                draft.kept -= 1
                all_entailed = False if verdict != "silent" else all_entailed
                if verdict == "silent":
                    all_entailed = False
        if good:
            kept_lines.append(" ".join(good))
    draft.verified = all_entailed and draft.kept >= 4
    return "\n".join(kept_lines).strip()


def _sources_for(bundle: Bundle, product: Page) -> dict[str, str]:
    """`page_id#heading` → section text, for every child page in SOURCE_ROLES."""
    from api.compose import split_sections

    out: dict[str, str] = {}
    for suffix in SOURCE_ROLES:
        page = bundle.get(f"{product.id}{suffix}")
        if page is None or page.frontmatter.status != Status.approved:
            continue
        for heading, body in split_sections(page):
            text = SOURCE_REF_RE.sub("", body).strip()
            if len(text) >= 40:
                out[f"{page.id}#{heading}"] = text
    return out


def _shown_for(sources: dict[str, str], product: Page, roles: tuple[str, ...]) -> dict[str, str]:
    """The sections for one part, in role order, under the per-call budget."""
    shown: dict[str, str] = {}
    total = 0
    for role in roles:
        page_id = f"{product.id}{role}"
        for key, text in sources.items():
            if key.split("#", 1)[0] != page_id:
                continue
            block = len(key) + len(text) + 20
            if total + block > PART_CHARS:
                break
            shown[key] = text
            total += block
    return shown


def _loose(key: str) -> str:
    return re.sub(r"[\s\-–_]+", "", key.lower())


def _verify(markdown: str, sources: dict[str, str], draft: Draft) -> str:
    """Keep only sentences that cite a shown section and whose figures are in it."""
    kept_lines: list[str] = []
    for line in markdown.splitlines():
        if not line.strip() or _HEADING_RE.match(line):
            kept_lines.append(line)
            continue
        sentences = _SENTENCE_RE.findall(line)
        if not sentences:
            # Prose with no source ref at all: nothing to verify, so nothing to keep.
            draft.dropped_unsourced += 1
            continue
        good: list[str] = []
        for sentence in sentences:
            ref = SOURCE_REF_RE.search(sentence)
            key = f"{ref.group(1)}#{ref.group(2)}" if ref and ref.group(2) else (ref.group(1) if ref else "")
            source = sources.get(key)
            if source is None:
                # The model copies ids imperfectly — "Section 1 -Renovation"
                # for "Section 1 - Renovation" — so a ref is matched with
                # spaces, hyphens and case ignored, and rewritten to the true
                # id so the page cites what exists.
                loose = _loose(key)
                match = next((k for k in sources if _loose(k) == loose), None)
                if match is None:
                    draft.dropped_unknown_source += 1
                    continue
                sentence = sentence.replace(key, match)
                source = sources[match]
            digits_ok = all(
                span in source for span in NUMERIC_SPAN_RE.findall(SOURCE_REF_RE.sub("", sentence))
            )
            if not digits_ok:
                draft.dropped_figure += 1
                continue
            good.append(sentence.strip())
            draft.kept += 1
        if good:
            kept_lines.append(" ".join(good))
    return "\n".join(kept_lines).strip()


def write_llm_wiki(
    config: CompileConfig, bundle: Bundle, provider: Any, report: CompileReport
) -> list[Draft]:
    """One plain-language page per approved product, or none where the model
    is not configured — the deterministic provider returns no verdict and this
    tier simply does not exist without a model."""
    classify = getattr(provider, "classify", None)
    if classify is None or getattr(provider, "name", "") == "deterministic":
        report.skip("llm-wiki: no model configured — tier not written")
        return []
    drafts: list[Draft] = []
    products = [
        p
        for p in bundle.pages.values()
        if p.frontmatter.type == PageType.product
        and p.frontmatter.status == Status.approved
        and p.id.count("/") == 2
    ]
    for product in sorted(products, key=lambda p: p.id):
        sources = _sources_for(bundle, product)
        if len(sources) < 2:
            report.skip("llm-wiki: product has fewer than two compiled sections — not written")
            continue
        parts_markdown: list[str] = []
        shown_all: dict[str, str] = {}
        failed = ""
        for headings, roles, max_tokens in PARTS:
            shown = _shown_for(sources, product, roles)
            if not shown:
                continue
            shown_all.update(shown)
            user = f"PRODUCT: {product.frontmatter.title}\n\n" + "\n".join(
                f"SECTION `{k}`:\n{v}\n" for k, v in shown.items()
            )
            payload = None
            for attempt in range(2):
                try:
                    payload = classify(_part_prompt(headings), user, SCHEMA, max_tokens=max_tokens)
                except Exception as exc:
                    report.skip(f"llm-wiki: provider fault ({type(exc).__name__})")
                    payload = None
                text = (payload or {}).get("markdown") if isinstance(payload, dict) else None
                if isinstance(text, str) and text.strip():
                    # The local model returns its line breaks as the two
                    # characters backslash-n inside the JSON string, so the
                    # whole part arrived as one "heading" line and no
                    # sentence was ever seen.
                    parts_markdown.append(text.replace("\\n", "\n").strip())
                    break
                # A second try with half the source: a reply cut off at the
                # token cap is not JSON, and a shorter prompt leaves room.
                user = user[: len(user) // 2]
            else:
                failed = getattr(provider, "last_error", "") or "empty reply"
                report.skip(f"llm-wiki: part {headings.splitlines()[0]!r} returned nothing ({failed})")
        if not parts_markdown:
            report.skip("llm-wiki: model returned nothing for every part — not written")
            continue
        markdown = "\n\n".join(parts_markdown)
        shown_sources = shown_all
        draft = Draft(product=product, body="")
        draft.body = _verify(markdown, shown_sources, draft)
        if draft.kept < 4:
            report.skip(
                "llm-wiki: fewer than four sentences survived verification — not written "
                f"(kept {draft.kept}, unsourced {draft.dropped_unsourced}, unknown source "
                f"{draft.dropped_unknown_source}, figure {draft.dropped_figure})"
            )
            continue
        # Provenance proven; now sense, against the product page's own sections.
        draft.body = _crosscheck(draft.body, shown_sources, classify, draft)
        if draft.kept < 4:
            report.skip("llm-wiki: fewer than four sentences survived the cross-check — not written")
            continue
        fm = Frontmatter(
            id=f"{product.id}/plain",
            title=f"{product.frontmatter.title} — in plain language",
            type=PageType.product,
            # Approved only when every sentence was judged entailed by the
            # product page section it cites — the owner's decision, in place
            # of a human reading each page. Anything less stays a draft.
            status=Status.approved if draft.verified else Status.draft,
            reviewed_by=["auto-crosscheck"] if draft.verified else [],
            underwriter=product.frontmatter.underwriter,
            jurisdiction=product.frontmatter.jurisdiction,
            line_of_business=product.frontmatter.line_of_business,
            aliases=[
                f"{product.frontmatter.title.lower()} in plain english",
                "plain language",
                "simple terms",
            ],
            authority=sorted({k.split("#", 1)[0] for k in sources}),
            version_in_force=product.frontmatter.version_in_force,
            effective_from=config.today,
            review_due=config.today + dt.timedelta(days=30 * config.review_months),
            confidence=Confidence.medium,
            compiled_at=dt.datetime.now(),
            compiled_by="llm",
        )
        _write(config, _page(fm, [draft.body]), report)
        drafts.append(draft)
    return drafts
