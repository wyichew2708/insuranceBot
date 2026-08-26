"""Content extraction: strip the furniture, keep the substance.

trafilatura when installed; otherwise a built-in extractor that drops nav,
header, footer, script, style, aside and cookie/consent blocks, then keeps the
densest remaining region. Tables are pulled out separately — a benefit table is
the most valuable thing on an insurance product page and must not be flattened
into prose (§C.3 rule 2).
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field

FURNITURE = re.compile(
    r"(?is)<(head|script|style|noscript|svg|nav|header|footer|aside|form|iframe|template)\b.*?</\1\s*>"
)
COMMENT = re.compile(r"(?s)<!--.*?-->")
FURNITURE_CLASS = re.compile(
    r"(?is)<(div|section|ul|p)\b[^>]*\b(class|id)\s*=\s*[\"'][^\"']*"
    r"(cookie|consent|banner|breadcrumb|newsletter|subscribe|social|share|menu|nav|"
    r"skip-link|back-to-top|chat-widget)[^\"']*[\"'][^>]*>.*?</\1\s*>"
)
TAG = re.compile(r"(?s)<[^>]+>")
TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
H1_RE = re.compile(r"(?is)<h1[^>]*>(.*?)</h1>")
HEADING_RE = re.compile(r"(?is)<h([2-3])[^>]*>(.*?)</h\1\s*>")
CANONICAL_RE = re.compile(r"(?is)<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)[\"']")
CANONICAL_ALT_RE = re.compile(r"(?is)<link[^>]+href=[\"']([^\"']+)[\"'][^>]+rel=[\"']canonical[\"']")
META_DESC_RE = re.compile(r"(?is)<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']*)[\"']")
HREF_RE = re.compile(r"(?is)<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']")
# "Travel Insurance | Etiqa" — the site name is furniture in a <title>. The
# plain hyphen belongs here too: every Etiqa product page ends " - Etiqa
# Insurance Singapore" and every Tiq one " - Leading digital insurance company
# in Singapore", so without it the tagline became part of the product name and
# the wiki compiled 64 products called things like "Enrich saver - Etiqa
# Insurance Singapore". Spaces are required around the separator, so hyphenated
# names ("Tiq 3-Year Endowment") survive intact.
TITLE_SUFFIX = re.compile("\\s+[|\u2013\u2014-]\\s+")
TABLE_RE = re.compile(r"(?is)<table\b.*?</table\s*>")
ROW_RE = re.compile(r"(?is)<tr\b.*?</tr\s*>")
CELL_RE = re.compile(r"(?is)<t[hd]\b[^>]*>(.*?)</t[hd]\s*>")


def text_of(fragment: str) -> str:
    return " ".join(html_lib.unescape(TAG.sub(" ", fragment)).split())


@dataclass
class Table:
    caption: str = ""
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    def as_markdown(self) -> str:
        lines = []
        if self.header:
            lines.append("| " + " | ".join(self.header) + " |")
            lines.append("|" + "|".join(["---"] * len(self.header)) + "|")
        for row in self.rows:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)


@dataclass
class Extracted:
    title: str = ""
    description: str = ""
    canonical: str | None = None
    text: str = ""
    headings: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    extractor: str = "builtin"


def parse_tables(html: str) -> list[Table]:
    tables: list[Table] = []
    for block in TABLE_RE.findall(html):
        rows = [[text_of(c) for c in CELL_RE.findall(r)] for r in ROW_RE.findall(block)]
        rows = [r for r in rows if any(cell for cell in r)]
        if len(rows) < 2:
            continue
        header, body = rows[0], rows[1:]
        # A header row is only a header if it is not itself data.
        if not any(re.search(r"\d", cell) for cell in header):
            tables.append(Table(header=header, rows=body))
        else:
            tables.append(Table(rows=rows))
    return tables


def _strip_furniture(html: str) -> str:
    cleaned = COMMENT.sub(" ", html)
    cleaned = FURNITURE.sub(" ", cleaned)
    for _ in range(3):  # nested wrappers need more than one pass
        replaced = FURNITURE_CLASS.sub(" ", cleaned)
        if replaced == cleaned:
            break
        cleaned = replaced
    return cleaned


def _main_region(html: str) -> str:
    """Prefer an explicit <main>/<article>; otherwise take the whole body."""
    for pattern in (
        r"(?is)<main\b[^>]*>(.*?)</main\s*>",
        r"(?is)<article\b[^>]*>(.*?)</article\s*>",
        r"(?is)<body\b[^>]*>(.*?)</body\s*>",
    ):
        match = re.search(pattern, html)
        if match and len(text_of(match.group(1))) > 120:
            return match.group(1)
    return html


def _best_title(heading: str, head: str) -> str:
    """Pick between the first `<h1>` and the `<title>`.

    The h1 is usually the better name — it is what the page calls itself. But
    on these sites the first h1 is often a section heading from page furniture,
    "You might also be interested in" being the common one, and taking it made
    eleven different products share that name in the compiled wiki.

    A real h1 shares vocabulary with the `<title>`; furniture does not. So the
    h1 wins when it overlaps, and the `<title>` wins when it does not.
    """
    if not heading:
        return head
    if not head:
        return heading
    stop = {"the", "a", "an", "of", "for", "and", "in", "to", "your", "you", "insurance"}

    def words(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in stop}

    return heading if words(heading) & words(head) else head


def extract(html: str, url: str = "") -> Extracted:
    out = Extracted()

    raw_title = TITLE_RE.search(html)
    h1 = H1_RE.search(html)
    head = text_of(raw_title.group(1)) if raw_title else ""
    heading = text_of(h1.group(1)) if h1 else ""
    out.title = _best_title(heading, head)
    # Site name suffixes ("Travel Insurance | Etiqa") are furniture in a title.
    out.title = re.split(TITLE_SUFFIX, out.title)[0].strip() if out.title else ""

    desc = META_DESC_RE.search(html)
    out.description = text_of(desc.group(1)) if desc else ""

    canonical = CANONICAL_RE.search(html) or CANONICAL_ALT_RE.search(html)
    out.canonical = html_lib.unescape(canonical.group(1)) if canonical else None

    out.links = [html_lib.unescape(h) for h in HREF_RE.findall(html)]

    body = _strip_furniture(html)
    out.tables = parse_tables(body)
    region = _main_region(body)
    out.headings = [text_of(m.group(2)) for m in HEADING_RE.finditer(region)]

    try:
        import trafilatura

        extracted = trafilatura.extract(html, include_tables=False, include_comments=False)
        if extracted and len(extracted) > 120:
            out.text = extracted.strip()
            out.extractor = "trafilatura"
            return out
    except ImportError:
        pass

    out.text = _to_markdownish(region)
    return out


def _to_markdownish(fragment: str) -> str:
    """Keep heading structure and paragraph breaks; drop everything else."""
    work = TABLE_RE.sub(" ", fragment)
    work = re.sub(r"(?is)<h([1-3])[^>]*>(.*?)</h\1\s*>", lambda m: f"\n\n## {text_of(m.group(2))}\n\n", work)
    work = re.sub(r"(?is)<li\b[^>]*>(.*?)</li\s*>", lambda m: f"\n- {text_of(m.group(1))}", work)
    work = re.sub(r"(?is)</(p|div|section|br)\s*>", "\n\n", work)
    lines = [line.strip() for line in text_of_preserving_newlines(work).splitlines()]
    out: list[str] = []
    for line in lines:
        if not line:
            if out and out[-1]:
                out.append("")
            continue
        # Drop the one-word leftovers typical of stripped navigation.
        if len(line.split()) < 3 and not line.startswith(("##", "-")):
            continue
        out.append(line)
    return "\n".join(out).strip()


def text_of_preserving_newlines(fragment: str) -> str:
    stripped = TAG.sub(" ", fragment)
    unescaped = html_lib.unescape(stripped)
    return re.sub(r"[ \t]+", " ", unescaped)
