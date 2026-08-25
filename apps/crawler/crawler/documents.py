"""Document ingestion (§D.1) — the tiers above the website.

The crawler records PDFs without parsing them, which leaves the two
highest-authority tiers of the bundle empty:

    raw/wordings           <- the contract. Governs what is actually covered.
    raw/product-summaries  <- the regulated summary.
    raw/benefit-tables
    raw/web/...            <- marketing copy, ranked below all of the above.

Compiling from the web alone inverts that: a marketing page's headline number
outranks nothing, and a disagreement with the wording is invisible because the
wording was never read. This module fills those tiers.

Two backends, one contract:

  docling   layout-aware, extracts tables as tables. Optional — it pulls in
            torch and transformers, so it is an extra rather than a default.
  builtin   pypdf text extraction. Light and dependency-cheap, but it
            flattens tables into prose, so benefit rows do not survive.

`auto` prefers docling when it is importable. The difference is reported
rather than hidden: a run that produced no tables says so, because a benefit
table that silently became a paragraph is how a limit goes missing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

#: Filename/URL patterns that place a document in an authority tier. Ordered:
#: the first match wins, so the contract beats the summary that describes it.
TIER_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            # `tnc`/`tncs`/`t&c` are how terms-and-conditions actually appear
            # in the wild (travel-infinite-tncs-1-apr-2026.pdf), so match the
            # abbreviation as well as the spelled-out form.
            r"policy[-_ ]?wording|general[-_ ]?provision|policy[-_ ]?contract"
            r"|terms?[-_ ]?(?:and[-_ ]?)?conditions?|t(?:&|n)cs?\b",
            re.I,
        ),
        "wordings",
    ),
    (
        re.compile(r"product[-_ ]?summary|policy[-_ ]?summary|benefit[-_ ]?illustration", re.I),
        "product-summaries",
    ),
    (re.compile(r"brochure|flyer|fact[-_ ]?sheet|faq", re.I), "brochures"),
]

#: Where each tier is written, relative to the bundle's raw/ root.
TIER_DIRS = {
    "wordings": "wordings",
    "product-summaries": "product-summaries",
    # Brochures are marketing. They are kept because they often carry the
    # plain-English phrasing customers use, but they are never authoritative.
    "brochures": "brochures",
}


def classify_document(url: str) -> str | None:
    """Which authority tier a document belongs to, or None to skip it."""
    name = url.rsplit("/", 1)[-1]
    for pattern, tier in TIER_RULES:
        if pattern.search(name) or pattern.search(url):
            return tier
    return None


VERSION_RE = re.compile(r"[-_ ][Vv](\d+)(?:[._](\d+))?(?=[-_. ]|$)")
PATH_DATE_RE = re.compile(r"/(20\d\d)/(\d{2})(?:/|$)")


def pinned_by_site(documents: list[dict[str, str]]) -> set[str]:
    """Documents a live product page links to.

    The site saying "this is the wording for this product" beats any guess
    made from an upload date: a product page still linking a 2023 contract
    means 2023 is the contract in force for it, whatever newer files exist in
    the uploads directory. Only product pages count — a blog post linking an
    old wording is not the insurer designating it.
    """
    return {
        d["url"]
        for d in documents
        if d.get("url") and d.get("referrer") and d.get("referrer_type") == "product"
    }


def recency_key(url: str) -> tuple[int, int, int, int]:
    """How new a document claims to be, most significant first.

    Two signals are available before fetching: the `/uploads/YYYY/MM/` path
    (97% of these documents) and a `V1.23`-style version in the filename
    (11%). The server's `Last-Modified` header is better still but costs a
    request, so it is recorded after fetching rather than used for ordering.

    Sorted descending, this puts the current revision of a contract ahead of
    its predecessors — which is what makes the newest the one that becomes
    canonical instead of whichever the manifest happened to list first.
    """
    date = PATH_DATE_RE.search(url)
    year, month = (int(date.group(1)), int(date.group(2))) if date else (0, 0)
    version = VERSION_RE.search(url.rsplit("/", 1)[-1])
    major, minor = (int(version.group(1)), int(version.group(2) or 0)) if version else (0, 0)
    return (year, month, major, minor)


def _discriminator(url: str) -> str:
    """A stable suffix that tells two same-named documents apart.

    WordPress uploads carry `/uploads/YYYY/MM/`, which is the most meaningful
    thing available — two policy wordings with one filename are usually two
    revisions. Anything else falls back to a short hash of the directory so
    the name is still deterministic across runs.
    """
    directory = url.rsplit("/", 1)[0]
    match = re.search(r"/(\d{4})/(\d{2})(?:/|$)", directory)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return hashlib.sha256(directory.encode()).hexdigest()[:6]


def plan_names(urls: list[str]) -> dict[str, str]:
    """Map each URL to a filename, disambiguating only where names collide.

    Slugifying a basename alone silently loses documents: 18 of Etiqa's
    filenames are reused across upload directories, so a naive run wrote 189
    wordings and left 173 on disk. Clean names are kept for everything that
    does not clash.
    """
    grouped: dict[str, list[str]] = {}
    for url in urls:
        grouped.setdefault(slugify(url.rsplit("/", 1)[-1]), []).append(url)
    names: dict[str, str] = {}
    for slug, group in grouped.items():
        if len(group) == 1:
            names[group[0]] = slug
            continue
        for url in group:
            names[url] = f"{slug}-{_discriminator(url)}"
    return names


def slugify(value: str) -> str:
    value = re.sub(r"\.pdf$", "", value, flags=re.I)
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return re.sub(r"-{2,}", "-", value) or "document"


@dataclass
class ParsedDoc:
    """What a backend got out of one PDF."""

    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    pages: int = 0
    backend: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.text.strip() and not self.tables


class PdfBackend(Protocol):
    name: str

    def parse(self, data: bytes, source: str) -> ParsedDoc: ...


class DoclingBackend:
    """Layout-aware conversion. Tables survive as tables.

    OCR is **off** by default. Insurer PDFs are generated from a publishing
    pipeline and carry a text layer already, so running OCR over them costs
    minutes per document and recovers nothing — a 46-page wording took 272s
    with OCR and a fraction of that without. Pass `ocr=True` for the
    exception: a scanned document, which the builtin backend reports as
    "no extractable text".
    """

    name = "docling"

    def __init__(self, ocr: bool = False) -> None:
        self.ocr = ocr
        self._converter: Any = None

    def _load(self) -> Any:
        if self._converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions()
            options.do_ocr = self.ocr
            options.do_table_structure = True
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return self._converter

    def parse(self, data: bytes, source: str) -> ParsedDoc:
        import tempfile

        from docling.datamodel.base_models import ConversionStatus

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as handle:
            handle.write(data)
            handle.flush()
            result = self._load().convert(Path(handle.name))
        if getattr(result, "status", None) not in (None, ConversionStatus.SUCCESS):
            return ParsedDoc("", backend=self.name)
        document = result.document
        tables: list[list[list[str]]] = []
        for table in getattr(document, "tables", []) or []:
            try:
                # `doc` is required in current docling; older builds accept
                # the no-arg form and warn.
                frame = table.export_to_dataframe(doc=document)
            except TypeError:
                frame = table.export_to_dataframe()
            except Exception:
                continue
            rows = [[str(c) for c in frame.columns]]
            rows += [[str(c) for c in row] for row in frame.itertuples(index=False)]
            tables.append(rows)
        return ParsedDoc(
            text=document.export_to_markdown(),
            tables=tables,
            pages=len(getattr(document, "pages", []) or []),
            backend=self.name,
        )


PIPE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
PIPE_RULE_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")


def parse_pipe_tables(markdown: str) -> list[list[list[str]]]:
    """Lift pipe tables out of a markdown string into rows.

    Backends that emit markdown (markitdown) carry their tables inline; the
    contract wants them separately so `render_document` and the "no tables"
    warning mean the same thing whichever backend ran.
    """
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in markdown.splitlines():
        if PIPE_RULE_RE.match(line):
            continue  # the |---|---| separator carries no data
        match = PIPE_ROW_RE.match(line)
        if match:
            current.append([cell.strip() for cell in match.group(1).split("|")])
            continue
        if current:
            if len(current) > 1:
                tables.append(current)
            current = []
    if len(current) > 1:
        tables.append(current)
    return tables


def _page_count(data: bytes) -> int:
    import io

    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return 0


class MarkItDownBackend:
    """Microsoft MarkItDown — pdfplumber underneath.

    The pragmatic default: it recovers the same benefit rows as docling at
    roughly a seventh of the cost (3.9s vs 29s on a 46-page wording), and its
    dependencies are megabytes rather than gigabytes. docling still wins where
    explicit column boundaries matter — markitdown tends to run a row's cells
    together where the PDF has no ruling lines.
    """

    name = "markitdown"

    def __init__(self) -> None:
        self._converter: Any = None

    def _load(self) -> Any:
        if self._converter is None:
            from markitdown import MarkItDown

            self._converter = MarkItDown()
        return self._converter

    def parse(self, data: bytes, source: str) -> ParsedDoc:
        import io

        try:
            result = self._load().convert_stream(io.BytesIO(data), file_extension=".pdf")
        except Exception:
            return ParsedDoc("", backend=self.name)
        text = result.text_content or ""
        return ParsedDoc(
            text=text,
            tables=parse_pipe_tables(text),
            # markitdown does not report a page count; take it from pypdf so
            # the frontmatter is not a misleading zero.
            pages=_page_count(data),
            backend=self.name,
        )


class BuiltinBackend:
    """pypdf text extraction — light, but tables arrive as flattened prose."""

    name = "builtin"

    def parse(self, data: bytes, source: str) -> ParsedDoc:
        import io

        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            chunks = [page.extract_text() or "" for page in reader.pages]
        except Exception:
            return ParsedDoc("", backend=self.name)
        return ParsedDoc(
            text="\n\n".join(c.strip() for c in chunks if c.strip()),
            pages=len(chunks),
            backend=self.name,
        )


def backend_for(choice: str = "auto", ocr: bool = False) -> PdfBackend:
    """The named backend, or the best one installed.

    `auto` prefers markitdown: it recovers the same benefit rows as docling at
    a seventh of the cost, so it is the right default for a few hundred
    documents. docling is next — worth its runtime when column structure
    matters. builtin is the floor and extracts no tables at all.
    """
    choice = (choice or "auto").lower()
    if choice == "builtin":
        return BuiltinBackend()
    if choice in {"docling", "markitdown"}:
        module = choice
        try:
            __import__(module)
        except ImportError as exc:  # asked for explicitly, so say so plainly
            extra = "docling" if module == "docling" else "markitdown[pdf]"
            raise RuntimeError(
                f"the {module!r} backend was requested but {module} is not installed "
                f"— run `uv sync --extra {extra}`, or use --backend auto"
            ) from exc
        return DoclingBackend(ocr=ocr) if module == "docling" else MarkItDownBackend()
    for backend in (MarkItDownBackend, DoclingBackend):
        try:
            module = "markitdown" if backend is MarkItDownBackend else "docling"
            __import__(module)
        except ImportError:
            continue
        return backend(ocr=ocr) if backend is DoclingBackend else backend()
    return BuiltinBackend()


def render_document(
    url: str,
    tier: str,
    parsed: ParsedDoc,
    fetched_at: str,
    also_at: list[str] | None = None,
    modified: str = "",
    superseded_by: str | None = None,
) -> str:
    """One document as a raw/ snapshot, in the shape the compiler already reads."""
    front = [
        "---",
        f'source_url: "{url}"',
        f'tier: "{tier}"',
        f"pages: {parsed.pages}",
        f"tables: {len(parsed.tables)}",
        f'extractor: "{parsed.backend}"',
        f'fetched_at: "{fetched_at}"',
    ]
    if modified:
        front.append(f'last_modified: "{modified}"')
    if superseded_by:
        # Kept, not deleted: a customer may hold a policy written under this
        # revision, and the version-coherence gate needs it to say so.
        front.append(f'superseded_by: "{superseded_by}"')
    # Another URL served byte-identical content. Usually the two front doors
    # of the direct channel; sometimes a stale URL on one host still serving
    # the current contract under an older version's filename. Either way it is
    # one document, recorded at every address it was found.
    for alternate in also_at or []:
        front.append(f'also_at: "{alternate}"')
    front += [
        "---",
        "",
    ]
    body = [parsed.text.strip()]
    for index, rows in enumerate(parsed.tables, start=1):
        if not rows:
            continue
        width = max(len(r) for r in rows)
        padded = [r + [""] * (width - len(r)) for r in rows]
        body += [
            "",
            f"## Table {index}",
            "",
            "| " + " | ".join(padded[0]) + " |",
            "|" + "---|" * width,
        ]
        body += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in padded[1:]]
    return "\n".join(front) + "\n".join(body).strip() + "\n"


@dataclass
class IngestReport:
    written: dict[str, int] = field(default_factory=dict)
    disambiguated: int = 0
    duplicates: int = 0
    superseded: int = 0
    tables: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    backend: str = ""

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.written.values())


async def ingest(
    manifest_path: Path,
    out_root: Path,
    backend: PdfBackend,
    rps: float = 1.0,
    max_documents: int = 0,
    keep_superseded: bool = False,
    today: str = "",
    client: Any = None,
) -> IngestReport:
    """Fetch every classified document in a crawl manifest and write it into
    its authority tier.

    Politeness matches the crawl it follows from: same user agent, same
    per-host throttle. Documents the crawler already recorded are re-fetched
    here rather than cached during the crawl, so a wording can be re-ingested
    with a better backend without re-crawling the site.
    """
    import asyncio
    import datetime as dt
    import json

    import httpx

    from crawler.crawl import USER_AGENT

    report = IngestReport(backend=backend.name)
    manifest = json.loads(manifest_path.read_text())
    documents = manifest.get("documents", [])
    stamp = today or dt.date.today().isoformat()

    classified = [u for u in dict.fromkeys(d.get("url", "") for d in documents) if u and classify_document(u)]
    # Newest first: the current revision becomes the canonical document, and
    # anything older is recorded against it rather than overwriting it.
    pinned = pinned_by_site(documents)
    # Site-designated first, then newest. Within a logical document the first
    # one seen becomes current, so a page's own link wins over an upload date.
    classified.sort(key=lambda u: (u in pinned, recency_key(u)), reverse=True)
    names = plan_names(classified)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=60.0, follow_redirects=True)
    delay = 1.0 / rps if rps > 0 else 0.0
    seen: set[str] = set()
    by_content: dict[str, Path] = {}
    current: dict[tuple[str, str], str] = {}
    try:
        report.skipped["not an authority document"] = sum(
            1 for u in dict.fromkeys(d.get("url", "") for d in documents) if u and not classify_document(u)
        )
        for url in classified:
            if url in seen:
                continue
            seen.add(url)
            tier = classify_document(url)
            assert tier is not None
            if max_documents and report.total >= max_documents:
                report.skip("document budget")
                break
            try:
                response = await client.get(url)
            except Exception as exc:
                report.skip(f"unreachable ({type(exc).__name__})")
                continue
            if response.status_code != 200:
                report.skip(f"HTTP {response.status_code}")
                continue
            digest = hashlib.sha256(response.content).hexdigest()
            if digest in by_content:
                # Already written from the other host. Record the second
                # address on the existing document rather than writing a
                # duplicate or silently overwriting it.
                existing = by_content[digest]
                existing.write_text(existing.read_text().replace("\n---\n", f'\nalso_at: "{url}"\n---\n', 1))
                report.duplicates += 1
                continue
            parsed = backend.parse(response.content, url)
            if parsed.is_empty:
                # A PDF that yields nothing is usually a scan. Say so rather
                # than writing an empty page the compiler would treat as real.
                report.skip("no extractable text (scanned?)")
                continue
            directory = out_root / TIER_DIRS[tier]
            directory.mkdir(parents=True, exist_ok=True)
            name = names.get(url) or slugify(url.rsplit("/", 1)[-1])
            path = directory / f"{name}.md"
            modified = response.headers.get("last-modified", "")
            slug = slugify(url.rsplit("/", 1)[-1])
            superseded_by = current.get((tier, slug))
            if superseded_by is not None and not keep_superseded:
                # Only the current revision is served. The older one is left
                # on the site; it is simply not part of the answer corpus.
                report.superseded += 1
                continue
            path.write_text(
                render_document(url, tier, parsed, stamp, modified=modified, superseded_by=superseded_by)
            )
            by_content[digest] = path
            if superseded_by is None:
                # First write for this logical document, and the list is
                # ordered newest-first, so this one is current.
                current[(tier, slug)] = f"{name}.md"
            else:
                report.superseded += 1
            report.written[tier] = report.written.get(tier, 0) + 1
            if name != slugify(url.rsplit("/", 1)[-1]):
                report.disambiguated += 1
            report.tables += len(parsed.tables)
            if delay:
                await asyncio.sleep(delay)
    finally:
        if owns_client:
            await client.aclose()
    return report
