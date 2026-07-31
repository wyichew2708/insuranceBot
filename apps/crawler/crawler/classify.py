"""URL policy (§7): allowlist, exclusions, page_type classification, canonical
mapping, promo validity parsing. Pure functions -> unit-tested."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

PAGE_TYPES = {"product", "claims", "servicing", "governance", "promo", "blog", "other"}

# Recorded as action links only, never crawled into the web index (§7.4).
EXCLUDED_PREFIXES = ("/loginportal/", "/iconnect/", "/online/", "/buy-online/")

# Recorded (url+title+version) but not chunked — CMS source material.
PDF_RECORD_ONLY_PREFIX = "/policy-wordings/"

_PATH_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/(promo|promotion|offers?|deals?)s?(/|$)"), "promo"),
    (re.compile(r"/claims?(/|$)"), "claims"),
    (re.compile(r"/(policy-services|servicing|customer-service|support)(/|$)"), "servicing"),
    (re.compile(r"/(privacy|terms|governance|compliance|pdpa|security)(/|-|$)"), "governance"),
    (re.compile(r"/(blog|articles?|stories|guides?)(/|$)"), "blog"),
    (re.compile(r"/(products?|plans?|insurance|travel|motor|home|maid|life|savings|invest)(/|$)"), "product"),
]


def in_allowlist(url: str, allowlist: list[str]) -> bool:
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(host == d.lower() or host == f"www.{d.lower()}".removeprefix("www.www.") for d in allowlist)


def is_excluded(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.startswith(EXCLUDED_PREFIXES)


def is_record_only_pdf(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.path.lower().startswith(PDF_RECORD_ONLY_PREFIX) or parsed.path.lower().endswith(".pdf")


def classify_page(url: str) -> str:
    path = urlparse(url).path.lower()
    for pattern, page_type in _PATH_RULES:
        if pattern.search(path):
            return page_type
    return "other"


def load_canonical_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return {str(k): str(v) for k, v in data.items()}


def canonicalize(url: str, canonical_map: dict[str, str]) -> str:
    """Apply known slug-drift rewrites (e.g. tiqinvest -> tiq-invest), strip
    fragments and tracking params, normalise trailing slash."""
    parsed = urlparse(url)
    path = parsed.path
    for old, new in canonical_map.items():
        path = path.replace(old, new)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


ACCURATE_AS_OF_RE = re.compile(
    r"information is accurate as (?:of|at)\s+(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2})", re.IGNORECASE
)
VALID_UNTIL_RE = re.compile(
    # the en-dash alternative in the pattern is intentional (promo date ranges)
    r"(?:valid (?:until|till|through)|ends?(?: on)?|promotion period.{0,40}?(?:to|until|–|-))\s*"  # noqa: RUF001
    r"(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

_DATE_FORMATS = ["%d %B %Y", "%d %b %Y", "%Y-%m-%d"]


def _parse_date(raw: str) -> dt.date | None:
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_promo_validity(text: str) -> tuple[dt.date | None, dt.date | None]:
    """Returns (accurate_as_of, valid_until) parsed from page text."""
    accurate = None
    until = None
    if m := ACCURATE_AS_OF_RE.search(text):
        accurate = _parse_date(m.group(1))
    if m := VALID_UNTIL_RE.search(text):
        until = _parse_date(m.group(1))
    return accurate, until


STALE_SIGNAL_RE = re.compile(
    r"\b(covid-19|fully subscribed|no longer available for purchase)\b", re.IGNORECASE
)


def is_demoted(text: str) -> bool:
    """COVID-era and fully-subscribed pages are retrievable only on explicit ask (§7.5)."""
    return bool(STALE_SIGNAL_RE.search(text))
