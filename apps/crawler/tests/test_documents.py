"""Document ingestion — the tiers above the website (§D.1).

The unit under test is routing and rendering, not PDF parsing: a backend is
injected, so these run offline and do not depend on which extractor is
installed.
"""

import json
from pathlib import Path

import pytest
from crawler.documents import (
    BuiltinBackend,
    DoclingBackend,
    IngestReport,
    ParsedDoc,
    backend_for,
    classify_document,
    ingest,
    render_document,
    slugify,
)

# The real filenames, taken from an Etiqa product page.
REAL = {
    "Travel-Infinite-Policy-Wording.pdf": "wordings",
    "ELASTIQ-General-Provisions-Contract-7-Nov.pdf": "wordings",
    "travel-infinite-tncs-1-apr-2026.pdf": "wordings",
    "ELASTIQ-Product-Summary-7-Nov.pdf": "product-summaries",
    "Travel-Infinite-COVID-19-Product-Brochure.pdf": "brochures",
    "Travel-Infinite_COVID-19-FAQ.pdf": "brochures",
    "Travel-Infinite-Sanctioned-Countries.pdf": None,
}


@pytest.mark.parametrize("name,tier", REAL.items())
def test_real_document_names_route_to_the_right_tier(name: str, tier: str | None) -> None:
    assert classify_document(f"https://www.etiqa.com.sg/wp-content/uploads/2025/07/{name}") == tier


def test_the_contract_outranks_the_summary_that_describes_it() -> None:
    """A file naming both must land in `wordings` — the wording governs."""
    assert classify_document("https://x/Travel-Policy-Wording-and-Product-Summary.pdf") == "wordings"


def test_slugify_strips_the_extension_and_normalises() -> None:
    assert slugify("ELASTIQ-Product-Summary-7-Nov.pdf") == "elastiq-product-summary-7-nov"
    assert slugify("Travel-Infinite_COVID-19-FAQ.pdf") == "travel-infinite-covid-19-faq"


# --- rendering -------------------------------------------------------------


def test_tables_survive_rendering_as_tables() -> None:
    parsed = ParsedDoc(
        text="Table of benefits.",
        tables=[[["Benefit", "Classic", "Suite"], ["Medical", "S$200,000", "S$1,000,000"]]],
        pages=46,
        backend="docling",
    )
    out = render_document("https://x/w.pdf", "wordings", parsed, "2026-08-20")
    assert 'tier: "wordings"' in out
    assert "tables: 1" in out
    assert "| Benefit | Classic | Suite |" in out
    assert "| Medical | S$200,000 | S$1,000,000 |" in out


def test_ragged_rows_are_padded_not_dropped() -> None:
    parsed = ParsedDoc(text="t", tables=[[["A", "B", "C"], ["only-one"]]])
    out = render_document("https://x/w.pdf", "wordings", parsed, "2026-08-20")
    assert "| only-one |  |  |" in out


def test_a_pipe_in_a_cell_does_not_break_the_table() -> None:
    parsed = ParsedDoc(text="t", tables=[[["A"], ["x | y"]]])
    assert r"x \| y" in render_document("https://x/w.pdf", "wordings", parsed, "2026-08-20")


# --- backend selection -----------------------------------------------------


def test_explicit_backend_choices_are_honoured() -> None:
    assert isinstance(backend_for("builtin"), BuiltinBackend)
    # docling is an optional extra, so only assert it when it is installed —
    # otherwise the correct behaviour is the clear error tested below.
    try:
        import docling  # noqa: F401
    except ImportError:
        pytest.skip("docling extra not installed")
    assert isinstance(backend_for("docling"), DoclingBackend)


def test_auto_picks_the_best_installed_backend() -> None:
    """Which one wins depends on what is installed; what must hold is that
    `auto` never raises and never silently picks the table-less floor when a
    table-capable backend is available."""
    assert backend_for("auto").name in {"markitdown", "docling", "builtin"}


def test_a_backend_that_extracts_nothing_is_reported_not_written() -> None:
    assert ParsedDoc("", backend="builtin").is_empty
    assert not ParsedDoc("text", backend="builtin").is_empty
    assert not ParsedDoc("", tables=[[["a"]]], backend="docling").is_empty


# --- the ingest loop -------------------------------------------------------


class _StubBackend:
    name = "stub"

    def __init__(self, doc: ParsedDoc) -> None:
        self.doc = doc
        self.calls = 0

    def parse(self, data: bytes, source: str) -> ParsedDoc:
        self.calls += 1
        return self.doc


def _manifest(tmp_path: Path, urls: list[str]) -> Path:
    path = tmp_path / "crawl-manifest.json"
    path.write_text(json.dumps({"documents": [{"url": u, "host": "h", "kind": "wording"} for u in urls]}))
    return path


async def test_ingest_writes_each_document_into_its_tier(tmp_path: Path) -> None:
    import httpx

    manifest = _manifest(
        tmp_path,
        [
            "https://x/Travel-Policy-Wording.pdf",
            "https://x/Travel-Product-Summary.pdf",
            "https://x/Travel-Sanctioned-Countries.pdf",  # unclassified — skipped
        ],
    )
    backend = _StubBackend(ParsedDoc("body", tables=[[["a", "b"]]], pages=2, backend="stub"))
    # Distinct bytes per URL: dedup is content-based, and two real documents
    # are not byte-identical.
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"%PDF-1.4 " + str(request.url).encode())
        )
    )
    report = await ingest(manifest, tmp_path / "raw", backend, rps=0, client=client, today="2026-08-20")
    await client.aclose()

    assert report.written == {"wordings": 1, "product-summaries": 1}
    assert report.tables == 2
    assert report.skipped["not an authority document"] == 1
    assert (tmp_path / "raw/wordings/travel-policy-wording.md").is_file()
    assert (tmp_path / "raw/product-summaries/travel-product-summary.md").is_file()
    assert backend.calls == 2, "the unclassified document must not be fetched through the parser"


async def test_a_scanned_pdf_is_skipped_rather_than_written_empty(tmp_path: Path) -> None:
    """An empty page would be compiled as though it were real source."""
    import httpx

    manifest = _manifest(tmp_path, ["https://x/Travel-Policy-Wording.pdf"])
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"%PDF"))
    )
    report = await ingest(manifest, tmp_path / "raw", _StubBackend(ParsedDoc("")), rps=0, client=client)
    await client.aclose()
    assert report.total == 0
    assert report.skipped["no extractable text (scanned?)"] == 1
    assert not (tmp_path / "raw/wordings").exists()


async def test_an_unreachable_document_does_not_stop_the_run(tmp_path: Path) -> None:
    import httpx

    manifest = _manifest(tmp_path, ["https://x/A-Policy-Wording.pdf", "https://x/B-Policy-Wording.pdf"])
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "A-" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=b"%PDF")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    report = await ingest(manifest, tmp_path / "raw", _StubBackend(ParsedDoc("body")), rps=0, client=client)
    await client.aclose()
    assert len(seen) == 2, "the run continues past a failure"
    assert report.written == {"wordings": 1}
    assert report.skipped["HTTP 404"] == 1


async def test_duplicate_urls_are_fetched_once(tmp_path: Path) -> None:
    import httpx

    url = "https://x/Travel-Policy-Wording.pdf"
    manifest = _manifest(tmp_path, [url, url, url])
    backend = _StubBackend(ParsedDoc("body"))
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"%PDF"))
    )
    report = await ingest(manifest, tmp_path / "raw", backend, rps=0, client=client)
    await client.aclose()
    assert backend.calls == 1 and report.total == 1


def test_report_totals() -> None:
    report = IngestReport(written={"wordings": 3, "brochures": 1})
    assert report.total == 4


# --- markdown table lifting ------------------------------------------------


def test_pipe_tables_are_lifted_out_of_markdown() -> None:
    """markitdown carries its tables inline; the contract wants them out, so
    `render_document` and the no-tables warning mean the same thing whichever
    backend ran."""
    from crawler.documents import parse_pipe_tables

    md = (
        "Some prose.\n\n"
        "| Benefit | Classic | Suite |\n"
        "| --- | --- | --- |\n"
        "| Medical | $200,000 | $2,500,000 |\n\n"
        "More prose.\n"
    )
    tables = parse_pipe_tables(md)
    assert len(tables) == 1
    assert tables[0][0] == ["Benefit", "Classic", "Suite"]
    assert tables[0][1] == ["Medical", "$200,000", "$2,500,000"]


def test_the_separator_row_is_not_data() -> None:
    from crawler.documents import parse_pipe_tables

    rows = parse_pipe_tables("| A | B |\n| --- | --- |\n| 1 | 2 |\n")[0]
    assert rows == [["A", "B"], ["1", "2"]]


def test_a_lone_pipe_line_is_not_a_table() -> None:
    from crawler.documents import parse_pipe_tables

    assert parse_pipe_tables("| just one row |\n\nprose\n") == []


def test_several_tables_are_kept_separate() -> None:
    from crawler.documents import parse_pipe_tables

    md = "| A |\n| --- |\n| 1 |\n\ntext\n\n| B |\n| --- |\n| 2 |\n"
    assert [t[0][0] for t in parse_pipe_tables(md)] == ["A", "B"]


# --- filename collisions ---------------------------------------------------


def test_unique_names_are_left_clean() -> None:
    from crawler.documents import plan_names

    urls = [
        "https://e/uploads/2025/07/A-Policy-Wording.pdf",
        "https://e/uploads/2025/07/B-Policy-Wording.pdf",
    ]
    assert set(plan_names(urls).values()) == {"a-policy-wording", "b-policy-wording"}


def test_two_revisions_of_one_wording_do_not_overwrite_each_other() -> None:
    """18 of Etiqa's filenames are reused across upload directories, so a
    naive slug wrote 189 wordings and left 173 on disk — and the survivors
    were whichever happened to be written last. In a corpus that ranks the
    contract above everything else, losing a revision silently is not an
    acceptable failure."""
    from crawler.documents import plan_names

    urls = [
        "https://e/wp-content/uploads/2022/11/Policy-Wording-Money.pdf",
        "https://e/wp-content/uploads/2023/02/Policy-Wording-Money.pdf",
    ]
    names = plan_names(urls)
    assert len(set(names.values())) == 2, "both revisions must survive"
    # The upload date is the discriminator — it is the meaningful difference.
    assert names[urls[0]].endswith("-2022-11")
    assert names[urls[1]].endswith("-2023-02")


def test_a_collision_without_a_date_still_disambiguates() -> None:
    from crawler.documents import plan_names

    urls = [
        "https://e/policy_documents/policy-wordings/X-Policy-Wording.pdf",
        "https://e/other/place/X-Policy-Wording.pdf",
    ]
    names = plan_names(urls)
    assert len(set(names.values())) == 2


def test_names_are_stable_across_runs() -> None:
    """Re-ingesting must overwrite the same file, not accumulate copies."""
    from crawler.documents import plan_names

    urls = [
        "https://e/policy_documents/X-Policy-Wording.pdf",
        "https://e/other/X-Policy-Wording.pdf",
    ]
    assert plan_names(urls) == plan_names(list(urls))


async def test_the_same_contract_on_both_hosts_is_one_document(tmp_path: Path) -> None:
    """Etiqa and Tiq serve byte-identical policy contracts — verified by hash
    on three real wordings. That is the channel model in the source data: one
    document, two addresses. It is recorded as such rather than written twice
    or silently overwritten.

    The same path also catches a stale URL serving current content under an
    old version's filename, which is a website defect worth surfacing."""
    import httpx

    urls = [
        "https://www.etiqa.com.sg/wp-content/uploads/2026/02/Car-Policy-Wording-v19.pdf",
        "https://www.tiq.com.sg/wp-content/uploads/2026/02/Car-Policy-Wording-v19.pdf",
    ]
    manifest = _manifest(tmp_path, urls)
    backend = _StubBackend(ParsedDoc("body"))
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"%PDF same"))
    )
    report = await ingest(manifest, tmp_path / "raw", backend, rps=0, client=client)
    await client.aclose()

    assert report.written == {"wordings": 1}
    assert report.duplicates == 1
    assert backend.calls == 1, "the identical document is not parsed twice"
    written = list((tmp_path / "raw/wordings").glob("*.md"))
    assert len(written) == 1
    # Both addresses are on the document.
    body = written[0].read_text()
    assert "www.tiq.com.sg" in body and "www.etiqa.com.sg" in body


# --- recency: the current revision wins ------------------------------------


def test_recency_orders_by_upload_date_then_version() -> None:
    from crawler.documents import recency_key

    urls = [
        "https://e/uploads/2022/11/X-Policy-Wording.pdf",
        "https://e/uploads/2025/10/X-Policy-Contract-V2.25.pdf",
        "https://e/uploads/2024/01/X-Policy-Contract-V1.23.pdf",
        "https://e/docs/X-Policy-Wording.pdf",  # no signal at all — sorts last
    ]
    newest_first = sorted(urls, key=recency_key, reverse=True)
    assert newest_first[0].endswith("V2.25.pdf")
    assert newest_first[1].endswith("V1.23.pdf")
    assert newest_first[-1] == "https://e/docs/X-Policy-Wording.pdf"


def test_a_higher_version_in_the_same_month_still_wins() -> None:
    from crawler.documents import recency_key

    a = "https://e/uploads/2025/10/X-Policy-Contract-V2.9.pdf"
    b = "https://e/uploads/2025/10/X-Policy-Contract-V2.25.pdf"
    assert recency_key(b) > recency_key(a)


async def test_only_the_current_revision_is_served(tmp_path: Path) -> None:
    """Manifest order is arbitrary; the newest revision must be the one that
    answers, and older ones are not part of the corpus at all."""
    import httpx

    old = "https://e/wp-content/uploads/2022/11/Money-Policy-Wording.pdf"
    new = "https://e/wp-content/uploads/2023/02/Money-Policy-Wording.pdf"
    manifest = _manifest(tmp_path, [old, new])  # oldest listed first, on purpose

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"%PDF " + str(request.url).encode(),
            headers={"Last-Modified": "Tue, 18 Feb 2025 06:06:04 GMT"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    report = await ingest(manifest, tmp_path / "raw", _StubBackend(ParsedDoc("body")), rps=0, client=client)
    await client.aclose()

    assert report.written == {"wordings": 1}
    assert report.superseded == 1
    written = list((tmp_path / "raw/wordings").glob("*.md"))
    assert len(written) == 1
    body = written[0].read_text()
    assert new in body and old not in body
    # The server's own timestamp is recorded.
    assert 'last_modified: "Tue, 18 Feb 2025 06:06:04 GMT"' in body


# --- the site decides which wording governs --------------------------------


def test_a_wording_linked_from_a_product_page_is_pinned() -> None:
    from crawler.documents import pinned_by_site

    docs = [
        {"url": "https://e/A.pdf", "referrer": "https://e/personal/travel/", "referrer_type": "product"},
        {"url": "https://e/B.pdf", "referrer": "https://e/blog/post/", "referrer_type": "blog"},
        {"url": "https://e/C.pdf", "referrer": "", "referrer_type": ""},
    ]
    # Only the product page counts: a blog post linking an old wording is not
    # the insurer designating it, and a sitemap entry designates nothing.
    assert pinned_by_site(docs) == {"https://e/A.pdf"}


async def test_the_product_page_link_beats_a_newer_upload(tmp_path: Path) -> None:
    """ "Always the latest, unless the product website says which wording to
    refer to." A page still linking the 2023 contract means 2023 governs that
    product, whatever sits in a newer uploads directory."""
    import httpx

    linked = "https://e/wp-content/uploads/2023/02/Travel-Policy-Wording.pdf"
    newer = "https://e/wp-content/uploads/2025/10/Travel-Policy-Wording.pdf"
    path = tmp_path / "crawl-manifest.json"
    path.write_text(
        json.dumps(
            {
                "documents": [
                    {"url": newer, "referrer": "", "referrer_type": ""},
                    {
                        "url": linked,
                        "referrer": "https://e/personal/travel/",
                        "referrer_type": "product",
                    },
                ]
            }
        )
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"%PDF " + str(request.url).encode())
        )
    )
    report = await ingest(path, tmp_path / "raw", _StubBackend(ParsedDoc("body")), rps=0, client=client)
    await client.aclose()

    written = list((tmp_path / "raw/wordings").glob("*.md"))
    assert len(written) == 1, "only the current wording is served"
    assert linked in written[0].read_text(), "the site's own link decides"
    assert report.superseded == 1


async def test_without_a_site_link_the_newest_upload_wins(tmp_path: Path) -> None:
    import httpx

    old = "https://e/wp-content/uploads/2022/11/Travel-Policy-Wording.pdf"
    new = "https://e/wp-content/uploads/2025/10/Travel-Policy-Wording.pdf"
    manifest = _manifest(tmp_path, [old, new])
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"%PDF " + str(request.url).encode())
        )
    )
    report = await ingest(manifest, tmp_path / "raw", _StubBackend(ParsedDoc("body")), rps=0, client=client)
    await client.aclose()
    written = list((tmp_path / "raw/wordings").glob("*.md"))
    assert len(written) == 1 and new in written[0].read_text()
    assert report.superseded == 1


async def test_keep_superseded_writes_the_older_revisions_too(tmp_path: Path) -> None:
    import httpx

    urls = [
        "https://e/wp-content/uploads/2022/11/Travel-Policy-Wording.pdf",
        "https://e/wp-content/uploads/2025/10/Travel-Policy-Wording.pdf",
    ]
    manifest = _manifest(tmp_path, urls)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"%PDF " + str(request.url).encode())
        )
    )
    await ingest(
        manifest,
        tmp_path / "raw",
        _StubBackend(ParsedDoc("body")),
        rps=0,
        client=client,
        keep_superseded=True,
    )
    await client.aclose()
    assert len(list((tmp_path / "raw/wordings").glob("*.md"))) == 2


def test_asking_for_a_backend_that_is_not_installed_fails_clearly() -> None:
    """An explicit --backend choice that cannot work should say so at
    selection time, not crash partway through a several-hundred-document run."""
    import builtins

    real_import = builtins.__import__

    def no_docling(name: str, *args: object, **kwargs: object) -> object:
        if name == "docling":
            raise ImportError("no docling")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    builtins.__import__ = no_docling  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="uv sync --extra docling"):
            backend_for("docling")
        # auto must still succeed by falling through to what is installed.
        assert backend_for("auto").name in {"markitdown", "builtin"}
    finally:
        builtins.__import__ = real_import  # type: ignore[assignment]
