"""Crawl policy: allowlist, robots.txt, exclusions, page classification.

Two rules are absolute. The crawler only ever touches hosts on the allowlist,
and it obeys robots.txt — including Crawl-delay. A knowledge pipeline that
scrapes impolitely is a liability before it is an asset.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

# Recorded as action links only, never crawled into the corpus: these are
# authenticated or transactional surfaces.
EXCLUDED_PREFIXES = (
    "/loginportal/",
    "/iconnect/",
    "/online/",
    "/buy-online/",
    "/checkout/",
    "/cart/",
    "/wp-admin/",
    "/wp-login",
)

PDF_RECORD_ONLY = ("/policy-wordings/", "/policy-wording/", "/documents/")

PAGE_TYPE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/(promotions?|offers?|deals?|campaign)(/|$)"), "promo"),
    (re.compile(r"/(claims?)(/|$)"), "claims"),
    (re.compile(r"/(policy-services|servicing|customer-service|support|help)(/|$)"), "servicing"),
    (re.compile(r"/(privacy|terms|governance|compliance|pdpa|security|disclosure)(/|-|$)"), "governance"),
    (re.compile(r"/(blog|articles?|stories|guides?|news)(/|$)"), "blog"),
    (re.compile(r"/(faqs?)(/|$)"), "faq"),
    (
        re.compile(
            r"/(products?|plans?|insurance|personal|business|travel|motor|car|home|maid|pet|"
            r"life|savings|invest|protection|health|medical|accident|cyber|mobility)(/|-|$)"
        ),
        "product",
    ),
]


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def in_allowlist(url: str, allowlist: list[str]) -> bool:
    """Host equality, www-insensitive. Substring matching would let an
    off-site URL that merely mentions the domain through."""
    host = host_of(url).removeprefix("www.")
    return any(host == d.lower().removeprefix("www.") for d in allowlist)


def is_excluded(url: str) -> bool:
    return urlparse(url).path.lower().startswith(EXCLUDED_PREFIXES)


def is_document(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx"))


def is_record_only(url: str) -> bool:
    """Documents are recorded (url, title, version) but not chunked as web
    copy — they are CMS source material for the compile loop, not marketing."""
    path = urlparse(url).path.lower()
    return is_document(url) or path.startswith(PDF_RECORD_ONLY)


def classify(url: str) -> str:
    path = urlparse(url).path.lower()
    for pattern, page_type in PAGE_TYPE_RULES:
        if pattern.search(path):
            return page_type
    return "other"


def canonical_url(url: str) -> str:
    """Strip fragments, tracking parameters and the trailing slash."""
    parsed = urlparse(url)
    query = "&".join(
        part
        for part in parsed.query.split("&")
        if part and not part.split("=")[0].lower().startswith(("utm_", "gclid", "fbclid", "mc_"))
    )
    path = parsed.path.rstrip("/") or "/"
    base = f"{parsed.scheme}://{parsed.netloc}{path}"
    return f"{base}?{query}" if query else base


@dataclass
class Robots:
    """Minimal robots.txt for our own user-agent plus `*`."""

    disallow: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    crawl_delay: float = 0.0
    sitemaps: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str, user_agent: str = "*") -> Robots:
        robots = cls()
        applies = False
        agent_specific = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field_name, _, value = line.partition(":")
            key, value = field_name.strip().lower(), value.strip()
            if key == "user-agent":
                agent = value.lower()
                if agent == user_agent.lower():
                    applies, agent_specific = True, True
                elif agent == "*" and not agent_specific:
                    applies = True
                else:
                    applies = False
            elif key == "sitemap":
                robots.sitemaps.append(value)  # sitemap lines are group-independent
            elif applies and key == "disallow" and value:
                robots.disallow.append(value)
            elif applies and key == "allow" and value:
                robots.allow.append(value)
            elif applies and key == "crawl-delay":
                with contextlib.suppress(ValueError):
                    robots.crawl_delay = float(value)
        return robots

    def allows(self, url: str) -> bool:
        path = urlparse(url).path or "/"
        longest_allow = max((len(p) for p in self.allow if path.startswith(p)), default=-1)
        longest_deny = max((len(p) for p in self.disallow if path.startswith(p)), default=-1)
        return longest_allow >= longest_deny


def absolutise(href: str, base: str) -> str:
    return canonical_url(urljoin(base, href.strip()))
