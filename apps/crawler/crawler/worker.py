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


async def discover_urls(domain: str, http: httpx.AsyncClient) -> list[str]:
    """sitemap.xml / wp-sitemap.xml, then WP REST, then (Phase 2) HTML crawl."""
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
    logger.warning("no sitemap or WP REST for %s; HTML link crawl not yet implemented", domain)
    return []


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
        canonical_url=canonicalize(url, canonical_map),
        brand=brand,
        page_type=page_type,
        text=text,
        fetched_at=now,
        expires_at=expires_at,
        accurate_as_of=accurate_as_of,
        demoted=is_demoted(text),
    )
