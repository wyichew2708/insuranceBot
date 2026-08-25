"""The crawl itself: discovery, polite fetching, dated snapshots.

Discovery order follows the design: robots.txt sitemaps, then well-known
sitemap paths (including sitemap indexes), then the WordPress REST API, then a
bounded same-host link crawl. Fetching is rate-limited per host and honours
Crawl-delay; snapshots are content-hashed so an unchanged page is a no-op for
the compile loop downstream (§D.1).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from crawler.extract import Extracted, extract
from crawler.policy import (
    Robots,
    absolutise,
    canonical_url,
    classify,
    fetch_priority,
    host_of,
    in_allowlist,
    is_document,
    is_excluded,
    is_record_only,
)

logger = logging.getLogger("crawler")

USER_AGENT = "EtiqaKnowledgeBot/0.2 (+internal knowledge pipeline; contact: knowledge-eng)"
LOC_RE = re.compile(r"(?is)<loc>\s*(.*?)\s*</loc>")
SITEMAP_INDEX_RE = re.compile(r"(?is)<sitemapindex")
WELL_KNOWN_SITEMAPS = ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap-index.xml")


@dataclass
class CrawlConfig:
    allowlist: list[str]
    out_dir: Path = Path("okf/raw")
    max_pages_per_host: int = 400
    requests_per_second: float = 1.0
    timeout_s: float = 25.0
    obey_robots: bool = True
    today: dt.date = field(default_factory=dt.date.today)


@dataclass
class PageRecord:
    url: str
    canonical: str
    host: str
    page_type: str
    status: int
    content_hash: str
    title: str
    path: str | None = None
    error: str = ""


@dataclass
class CrawlResult:
    pages: list[PageRecord] = field(default_factory=list)
    documents: list[dict[str, str]] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    hosts: dict[str, dict[str, object]] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    @property
    def ok_pages(self) -> list[PageRecord]:
        return [p for p in self.pages if p.status == 200 and not p.error]


class RateLimiter:
    """One token bucket per host, so a slow site is never hammered."""

    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self._next: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        if self.interval <= 0:
            return
        now = time.monotonic()
        earliest = self._next.get(host, 0.0)
        if earliest > now:
            await asyncio.sleep(earliest - now)
        self._next[host] = max(now, earliest) + self.interval


class Crawler:
    def __init__(self, config: CrawlConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.result = CrawlResult()
        self._client = client
        self._limiter = RateLimiter(config.requests_per_second)
        self._robots: dict[str, Robots] = {}

    async def _get(self, url: str) -> httpx.Response | None:
        assert self._client is not None
        await self._limiter.wait(host_of(url))
        try:
            return await self._client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("fetch failed %s: %s", url, exc)
            return None

    async def robots_for(self, host: str) -> Robots:
        if host in self._robots:
            return self._robots[host]
        robots = Robots()
        if self.config.obey_robots:
            response = await self._get(f"https://{host}/robots.txt")
            if response is not None and response.status_code == 200:
                robots = Robots.parse(response.text, USER_AGENT)
                if robots.crawl_delay > 0:
                    # A site that asks for slower is not negotiated with.
                    self._limiter.interval = max(self._limiter.interval, robots.crawl_delay)
        self._robots[host] = robots
        return robots

    async def discover(self, host: str) -> list[str]:
        robots = await self.robots_for(host)
        for sitemap_url in [*robots.sitemaps, *(f"https://{host}{p}" for p in WELL_KNOWN_SITEMAPS)]:
            urls = await self._read_sitemap(sitemap_url, depth=0)
            if urls:
                logger.info("%s: %d urls from %s", host, len(urls), sitemap_url)
                return urls

        response = await self._get(f"https://{host}/wp-json/wp/v2/pages?per_page=100")
        if response is not None and response.status_code == 200:
            try:
                urls = [item["link"] for item in response.json() if "link" in item]
                if urls:
                    logger.info("%s: %d urls from the WordPress REST API", host, len(urls))
                    return urls
            except (json.JSONDecodeError, TypeError):
                pass

        logger.info("%s: no sitemap or REST index; falling back to a link crawl", host)
        return await self._link_crawl(host)

    async def _read_sitemap(self, url: str, depth: int) -> list[str]:
        if depth > 2:
            return []
        response = await self._get(url)
        if response is None or response.status_code != 200 or "<loc" not in response.text.lower():
            return []
        locations = [canonical_url(loc) for loc in LOC_RE.findall(response.text)]
        if SITEMAP_INDEX_RE.search(response.text):
            nested: list[str] = []
            for child in locations:
                nested.extend(await self._read_sitemap(child, depth + 1))
            return nested
        return locations

    async def _link_crawl(self, host: str) -> list[str]:
        queue: deque[str] = deque([f"https://{host}/"])
        seen: set[str] = set()
        found: list[str] = []
        while queue and len(seen) < self.config.max_pages_per_host:
            url = queue.popleft()
            if url in seen or host_of(url) != host:
                continue
            seen.add(url)
            response = await self._get(url)
            if response is None or response.status_code != 200:
                continue
            if "text/html" not in response.headers.get("content-type", ""):
                continue
            found.append(url)
            for href in extract(response.text, url).links:
                link = absolutise(href, url)
                if host_of(link) == host and link not in seen:
                    queue.append(link)
        return found

    def _admit(self, url: str, robots: Robots) -> bool:
        if not in_allowlist(url, self.config.allowlist):
            self.result.skip("off allowlist")
            return False
        if is_excluded(url):
            self.result.skip("excluded path")
            return False
        if self.config.obey_robots and not robots.allows(url):
            self.result.skip("robots.txt")
            return False
        return True

    async def crawl_host(self, host: str) -> None:
        robots = await self.robots_for(host)
        discovered = await self.discover(host)
        # Stable sort by type: what a budget cut drops should be the least
        # authoritative content, not whatever the sitemap happened to list
        # last. Within a type, discovery order is preserved.
        discovered = sorted(discovered, key=fetch_priority)
        seen: set[str] = set()
        fetched = 0

        for raw_url in discovered:
            url = canonical_url(raw_url)
            if url in seen:
                continue
            seen.add(url)
            if not self._admit(url, robots):
                continue
            if is_record_only(url):
                # Documents are recorded, not chunked: they are source material
                # for the compile loop, not web copy.
                self.result.documents.append(
                    {
                        "url": url,
                        "host": host,
                        "kind": "document" if is_document(url) else "wording",
                        "referrer": "",  # found in the sitemap, not on a page
                        "referrer_type": "",
                    }
                )
                continue
            if fetched >= self.config.max_pages_per_host:
                self.result.skip("page budget")
                break

            response = await self._get(url)
            fetched += 1
            if response is None:
                self.result.pages.append(
                    PageRecord(url, url, host, classify(url), 0, "", "", error="unreachable")
                )
                continue
            if response.status_code != 200:
                self.result.pages.append(
                    PageRecord(url, url, host, classify(url), response.status_code, "", "")
                )
                continue
            if "text/html" not in response.headers.get("content-type", ""):
                self.result.skip("not html")
                continue

            self.result.pages.append(self._snapshot(url, host, response.text))

        self.result.hosts[host] = {
            "discovered": len(discovered),
            "fetched": fetched,
            "robots_crawl_delay": robots.crawl_delay,
            "sitemaps": robots.sitemaps,
        }

    def _record_documents(self, page: Extracted, url: str, host: str) -> None:
        """Wordings and product summaries are the highest-authority sources
        (§D.2) but they are PDFs — recorded here as an inventory for the
        compile loop rather than chunked as web copy. Discovery via sitemap
        rarely lists them; they hang off the product pages."""
        known = {d["url"] for d in self.result.documents}
        for href in page.links:
            link = canonical_url(absolutise(href, url))
            if not is_record_only(link) or link in known:
                continue
            if not in_allowlist(link, self.config.allowlist):
                continue
            known.add(link)
            self.result.documents.append(
                {
                    "url": link,
                    "host": host,
                    "kind": "document" if is_document(link) else "wording",
                    # Which page pointed at it, and what kind of page that was.
                    # A wording linked from a product page is the site's own
                    # statement of which contract governs that product, which
                    # outranks any guess made from an upload date.
                    "referrer": url,
                    "referrer_type": classify(url),
                }
            )

    def _snapshot(self, url: str, host: str, html: str) -> PageRecord:
        page = extract(html, url)
        self._record_documents(page, url, host)
        canonical = canonical_url(page.canonical) if page.canonical else url
        digest = hashlib.sha256(page.text.encode()).hexdigest()[:16]
        path = write_snapshot(self.config, host, url, canonical, page, digest)
        return PageRecord(
            url=url,
            canonical=canonical,
            host=host,
            page_type=classify(url),
            status=200,
            content_hash=digest,
            title=page.title,
            path=str(path),
        )

    async def run(self) -> CrawlResult:
        owns_client = self._client is None
        if owns_client:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT},
                timeout=self.config.timeout_s,
                follow_redirects=True,
            )
        try:
            for host in self.config.allowlist:
                await self.crawl_host(host.lower())
            return self.result
        finally:
            if owns_client and self._client is not None:
                await self._client.aclose()


def slugify_url(url: str) -> str:
    path = url.split("://", 1)[-1].split("/", 1)[-1] if "/" in url.split("://", 1)[-1] else "index"
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return (slug or "index")[:80]


def write_snapshot(
    config: CrawlConfig, host: str, url: str, canonical: str, page: Extracted, digest: str
) -> Path:
    """Dated, immutable snapshot under raw/web/<host>/<date>/ (§C.1)."""
    directory = config.out_dir / "web" / host / config.today.isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slugify_url(url)}.md"

    frontmatter = {
        "source_url": url,
        "canonical_url": canonical,
        "host": host,
        "title": page.title,
        "description": page.description,
        "page_type": classify(url),
        "fetched_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "content_hash": digest,
        "extractor": page.extractor,
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {json.dumps(value)}")
    lines.append("---")
    lines.append("")
    if page.title:
        lines.append(f"# {page.title}")
        lines.append("")
    lines.append(page.text)
    for index, table in enumerate(page.tables, start=1):
        lines += ["", f"## Table {index}", "", table.as_markdown()]
    path.write_text("\n".join(lines).rstrip() + "\n")
    return path


async def crawl(config: CrawlConfig, client: httpx.AsyncClient | None = None) -> CrawlResult:
    return await Crawler(config, client).run()
