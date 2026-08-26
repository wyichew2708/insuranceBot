"""Crawler CLI.

    crawl run --allowlist www.etiqa.com.sg www.tiq.com.sg

Writes dated snapshots under okf/raw/web/<host>/<date>/ and a manifest the
compile loop reads. Nothing is ever fetched from a host outside --allowlist.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import sys
from pathlib import Path

import httpx

from crawler.crawl import USER_AGENT, CrawlConfig, CrawlResult, crawl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def cmd_run(args: argparse.Namespace) -> int:
    config = CrawlConfig(
        allowlist=args.allowlist,
        out_dir=args.out,
        max_pages_per_host=args.max_pages,
        requests_per_second=args.rps,
        obey_robots=not args.ignore_robots,
        today=args.today,
    )
    client: httpx.AsyncClient | None = None
    if args.fixture:
        # The synthetic site is served in-process through a MockTransport, so the
        # exact same crawler code path is exercised with no network at all.
        from fixtures.synthetic_site import transport

        client = httpx.AsyncClient(
            transport=transport(),
            headers={"User-Agent": USER_AGENT},
            timeout=config.timeout_s,
            follow_redirects=True,
        )

    async def _go() -> CrawlResult:
        try:
            return await crawl(config, client)
        finally:
            if client is not None:
                await client.aclose()

    result = asyncio.run(_go())

    manifest = {
        "crawled_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "allowlist": config.allowlist,
        "hosts": result.hosts,
        "pages": [
            {
                "url": p.url,
                "canonical": p.canonical,
                "host": p.host,
                "page_type": p.page_type,
                "status": p.status,
                "content_hash": p.content_hash,
                "title": p.title,
                "path": p.path,
                "error": p.error,
            }
            for p in result.pages
        ],
        "documents": result.documents,
        "skipped": result.skipped,
    }
    manifest_path = config.out_dir / "web" / "crawl-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))

    ok = result.ok_pages
    print(f"\ncrawled {len(ok)} pages across {len(result.hosts)} hosts")
    for host, stats in result.hosts.items():
        print(f"  {host}: discovered {stats['discovered']}, fetched {stats['fetched']}")
    by_type: dict[str, int] = {}
    for page in ok:
        by_type[page.page_type] = by_type.get(page.page_type, 0) + 1
    for page_type, count in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"    {page_type:10} {count}")
    if result.documents:
        print(f"  documents recorded (not chunked): {len(result.documents)}")
    if result.skipped:
        print(f"  skipped: {result.skipped}")
    print(f"  manifest: {manifest_path}")

    if not ok:
        print("\nno pages were retrieved — check egress policy for the allowlisted hosts", file=sys.stderr)
        return 1
    return 0


def cmd_faqs(args: argparse.Namespace) -> int:
    """Published FAQs into `raw/faq/`."""
    from crawler.faqs import fetch, write

    pairs = []
    for host in args.allowlist:
        found = fetch(host, rps=args.rps)
        print(f"  {host:24} {len(found):4} pairs" + ("" if found else "   (no REST FAQ endpoint)"))
        pairs.extend(found)
    if not pairs:
        print("no published FAQs found on any allowlisted host")
        return 0
    stats = write(pairs, args.out / "faq")
    print(f"wrote {stats['pairs']} pairs across {stats['products']} products -> {args.out / 'faq'}")
    return 0


def cmd_documents(args: argparse.Namespace) -> int:
    from crawler.documents import backend_for, ingest

    if not args.manifest.is_file():
        print(f"no crawl manifest at {args.manifest} — run `crawl run` first")
        return 1
    backend = backend_for(args.backend, ocr=args.ocr)
    report = asyncio.run(
        ingest(
            manifest_path=args.manifest,
            out_root=args.out,
            backend=backend,
            rps=args.rps,
            max_documents=args.max_documents,
            keep_superseded=args.keep_superseded,
            today=args.today.isoformat(),
        )
    )
    print(f"\ningested {report.total} documents with the {report.backend!r} backend")
    for tier, count in sorted(report.written.items()):
        print(f"  {count:4}  raw/{tier}")
    print(f"  {report.tables:4}  tables extracted")
    if report.duplicates:
        print(f"  {report.duplicates:4}  byte-identical to another URL (recorded, not stored twice)")
    if report.superseded:
        kept = "kept" if args.keep_superseded else "skipped"
        print(f"  {report.superseded:4}  older revisions {kept} (superseded)")
    if report.disambiguated:
        print(f"  {report.disambiguated:4}  names disambiguated (filename reused across directories)")
    if report.backend == "builtin" and report.total:
        # Saying this out loud matters: a benefit table flattened into prose
        # still *looks* like a successful ingest.
        print(
            "\n  note: the builtin backend extracts text only — benefit tables arrive as\n"
            "  prose, not rows. Install the extra for layout-aware tables:\n"
            "      uv sync --extra docling"
        )
    for reason, count in sorted(report.skipped.items(), key=lambda kv: -kv[1]):
        print(f"  skipped {count:4}  {reason}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="crawl")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="crawl the allowlisted hosts into raw snapshots")
    run.add_argument("--allowlist", nargs="+", required=True)
    run.add_argument("--out", type=Path, default=Path("okf/raw"))
    run.add_argument("--max-pages", type=int, default=400)
    run.add_argument("--rps", type=float, default=1.0, help="requests per second, per host")
    run.add_argument(
        "--ignore-robots",
        action="store_true",
        help="only for a site you operate; the default obeys robots.txt",
    )
    run.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    run.add_argument(
        "--fixture",
        action="store_true",
        help="serve the synthetic .example site in-process instead of going to the network",
    )
    run.set_defaults(func=cmd_run)

    docs = sub.add_parser(
        "documents",
        help="parse the PDFs a crawl recorded into raw/wordings and raw/product-summaries",
    )
    docs.add_argument("--manifest", type=Path, default=Path("okf/raw/web/crawl-manifest.json"))
    docs.add_argument("--out", type=Path, default=Path("okf/raw"))
    docs.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "markitdown", "docling", "builtin"],
        help="auto prefers markitdown (fast, tables), then docling (slower, "
        "stronger column structure); builtin is pypdf text with no tables",
    )
    docs.add_argument(
        "--ocr",
        action="store_true",
        help="run OCR (docling only). Off by default: insurer PDFs carry a text "
        "layer, and OCR costs ~9x the time for nothing. Use it for scans.",
    )
    docs.add_argument("--rps", type=float, default=1.0)
    docs.add_argument("--max-documents", type=int, default=0, help="0 = no limit")
    docs.add_argument(
        "--keep-superseded",
        action="store_true",
        help="also write older revisions. Off by default: only the current "
        "wording is served, and a product page's own link decides which that is.",
    )
    docs.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    docs.set_defaults(func=cmd_documents)

    faq = sub.add_parser(
        "faqs",
        help="fetch published FAQ question/answer pairs (WordPress REST API)",
        description=(
            "The FAQs are a custom post type in no sitemap, displayed by a "
            "client-rendered page, so the ordinary crawl cannot see them. This "
            "reads them from the REST API where a host serves one."
        ),
    )
    faq.add_argument("--allowlist", nargs="+", default=["www.tiq.com.sg"])
    faq.add_argument("--out", type=Path, default=Path("okf/raw"))
    faq.add_argument("--rps", type=float, default=1.0)
    faq.set_defaults(func=cmd_faqs)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
