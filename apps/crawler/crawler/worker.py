"""Crawler worker skeleton (§7): sitemap discovery -> extraction -> upsert.

Full crawl scheduling (nightly + 6-hourly promo refresh) is wired via
APScheduler when the optional `extract` extra is installed. Network fetching
is isolated behind fetch_* functions so the pipeline is testable offline.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass

import httpx
from contracts.settings import Settings

from crawler.classify import (
    canonicalize,
    classify_page,
    in_allowlist,
    is_demoted,
    is_excluded,
    is_record_only_pdf,
    parse_promo_validity,
)

logger = logging.getLogger("crawler")

_SITEMAP_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>")


@dataclass
class CrawledPage:
    url: str
    canonical_url: str
    brand: str
    page_type: str
    text: str
    fetched_at: dt.datetime
    expires_at: dt.datetime
    accurate_as_of: dt.date | None
    demoted: bool


async def discover_urls(domain: str, http: httpx.AsyncClient, max_html_pages: int = 200) -> list[str]:
    """sitemap.xml / wp-sitemap.xml, then WP REST, then HTML link crawl."""
    for path in ("/sitemap.xml", "/wp-sitemap.xml"):
        try:
            resp = await http.get(f"https://{domain}{path}")
            if resp.status_code == 200:
                return _SITEMAP_LOC_RE.findall(resp.text)
        except httpx.HTTPError:
            continue
    try:
        resp = await http.get(f"https://{domain}/wp-json/wp/v2/pages", params={"per_page": 100})
        if resp.status_code == 200:
            return [p["link"] for p in resp.json() if "link" in p]
    except httpx.HTTPError:
        pass
    logger.warning("no sitemap or WP REST for %s; falling back to HTML link crawl", domain)
    return await html_link_crawl(domain, http, max_pages=max_html_pages)


def _same_host(url: str, domain: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host == domain.lower() or host == domain.lower().removeprefix("www.")


async def html_link_crawl(domain: str, http: httpx.AsyncClient, max_pages: int = 200) -> list[str]:
    """Breadth-first link crawl bounded to the domain host (final fallback,
    §7.1). Host comparison, not substring — an off-site URL that merely
    mentions the domain in its path/query must not be crawled."""
    start = f"https://{domain}/"
    queue = [start]
    visited: set[str] = set()
    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited or not _same_host(url, domain):
            continue
        visited.add(url)
        try:
            resp = await http.get(url)
        except httpx.HTTPError:
            continue
        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
            continue
        for link in extract_links(resp.text, url):
            if _same_host(link, domain) and link not in visited:
                queue.append(link)
    return sorted(visited)


_CANONICAL_LINK_RE = re.compile(
    r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)[\"']", re.IGNORECASE
)


def rel_canonical(html: str) -> str | None:
    """href of <link rel=canonical>, honoured over our own canonicalisation."""
    m = _CANONICAL_LINK_RE.search(html)
    return m.group(1) if m else None


_HREF_RE = re.compile(r"<a[^>]+href=[\"']([^\"']+)[\"']", re.IGNORECASE)


def extract_links(html: str, base_url: str) -> list[str]:
    """Absolute links for the fallback HTML crawl; fragments stripped (a link
    to page#section is still a link to the page)."""
    from urllib.parse import urljoin

    seen: set[str] = set()
    for href in _HREF_RE.findall(html):
        absolute = urljoin(base_url, href).split("#")[0]
        if absolute.startswith("http") and absolute:
            seen.add(absolute)
    return sorted(seen)


def extract_text(html: str) -> str:
    """Boilerplate-stripping extraction; trafilatura when available."""
    try:
        import trafilatura

        extracted = trafilatura.extract(html)
        if extracted:
            return str(extracted)
    except ImportError:
        pass
    # crude fallback: strip tags
    return re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(script|style|nav|footer).*?</\1>", " ", html)).strip()


def build_page(
    url: str,
    html: str,
    brand: str,
    settings: Settings,
    canonical_map: dict[str, str],
    now: dt.datetime | None = None,
) -> CrawledPage | None:
    """Classification + TTL policy for one fetched URL. None => skip indexing."""
    if not in_allowlist(url, settings.allowlisted_domains):
        return None
    if is_excluded(url) or is_record_only_pdf(url):
        return None
    now = now or dt.datetime.now(dt.UTC)
    text = extract_text(html)
    page_type = classify_page(url)
    canonical_override = rel_canonical(html)
    accurate_as_of, valid_until = parse_promo_validity(text)
    ttl_hours = (
        settings.crawl_promo_refresh_hours if page_type == "promo" else settings.crawl_default_refresh_hours
    )
    expires_at = now + dt.timedelta(hours=ttl_hours)
    if page_type == "promo" and valid_until is not None:
        promo_end = dt.datetime.combine(valid_until, dt.time.max, tzinfo=dt.UTC)
        expires_at = min(expires_at, promo_end)
    return CrawledPage(
        url=url,
        canonical_url=canonicalize(canonical_override or url, canonical_map),
        brand=brand,
        page_type=page_type,
        text=text,
        fetched_at=now,
        expires_at=expires_at,
        accurate_as_of=accurate_as_of,
        demoted=is_demoted(text),
    )


def domain_brand(domain: str) -> str:
    # NB: "etiqa" contains the substring "tiq" — check the more specific
    # brand first or every Etiqa page gets indexed under the wrong brand.
    return "etiqa" if "etiqa" in domain else "tiq"


async def crawl_domain(domain: str, settings: Settings, promo_only: bool = False) -> int:
    """One crawl pass over a domain. Returns number of pages indexed."""
    from pathlib import Path

    import psycopg

    from crawler.classify import load_canonical_map
    from crawler.indexer import index_page, make_embedder

    canonical_map = load_canonical_map(Path(__file__).parent / "canonical_map.yml")
    brand = domain_brand(domain)
    embedder = make_embedder(settings)
    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    indexed = 0
    async with (
        httpx.AsyncClient(timeout=20, follow_redirects=True) as http,
        await psycopg.AsyncConnection.connect(dsn) as conn,
    ):
        for url in await discover_urls(domain, http):
            if promo_only and classify_page(url) != "promo":
                continue
            try:
                resp = await http.get(url)
            except httpx.HTTPError as exc:
                logger.warning("fetch failed %s: %s", url, exc)
                continue
            if resp.status_code != 200:
                continue
            page = build_page(url, resp.text, brand, settings, canonical_map)
            if page is None:
                continue
            indexed += await index_page(conn, page, embedder)
    if embedder is not None:
        await embedder.aclose()
    logger.info("crawl %s (%s): %d chunks indexed", domain, "promo" if promo_only else "full", indexed)
    return indexed


async def run_forever(settings: Settings) -> None:
    """Nightly full refresh + promo refresh every CRAWL_PROMO_REFRESH_HOURS (§7.6)."""
    import asyncio

    promo_interval = settings.crawl_promo_refresh_hours * 3600
    full_interval = settings.crawl_default_refresh_hours * 3600
    last_full = 0.0
    import time

    while True:
        now = time.monotonic()
        full = now - last_full >= full_interval or last_full == 0.0
        for domain in settings.allowlisted_domains:
            try:
                await crawl_domain(domain, settings, promo_only=not full)
            except Exception:
                logger.exception("crawl pass failed for %s", domain)
        if full:
            last_full = time.monotonic()
        await asyncio.sleep(promo_interval)


def main() -> None:
    import asyncio

    from contracts.settings import get_settings

    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever(get_settings()))


if __name__ == "__main__":
    main()
