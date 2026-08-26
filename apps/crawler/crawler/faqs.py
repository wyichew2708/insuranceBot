"""Published FAQs — the closest thing in the corpus to a real customer question.

Every other question this project evaluates against is generated: derived from a
table row or a page heading, phrased by a template. These are not. They are the
questions the insurer chose to publish because customers actually ask them, and
each arrives with the answer the insurer chose to give. That makes them the only
material here with genuine ground truth attached.

They were also missed entirely by the sitemap crawl, for two compounding
reasons. The FAQs are a WordPress custom post type (`cpt_1730`) that appears in
no sitemap, and the page that displays them renders client-side — the crawled
HTML for `/faq` contains a list of product names and not one question mark. A
crawler that follows sitemaps and reads server-rendered HTML cannot see them,
which is exactly the sort of gap that looks like complete coverage until someone
asks for something specific.

The REST API is the route in. `www.tiq.com.sg` serves it; `www.etiqa.com.sg`
answers 403, so its FAQs remain out of reach and this module covers one host.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

#: The FAQ post type. Numeric because it is a generated custom-post-type name;
#: `/wp-json/wp/v2/types` is what maps it to the label "FAQs".
FAQ_POST_TYPE = "cpt_1730"

#: An accordion pane is a *category* ("Product Coverage and Benefits"), and the
#: questions live inside it as a numbered list. Two levels, so two passes.
_TAB_TITLE = re.compile(r'id="elementor-tab-title-(\d+)"[^>]*>(.*?)(?=<div id="elementor-tab-content-)', re.S)
_TAB_CONTENT = re.compile(r'id="elementor-tab-content-(\d+)"[^>]*>(.*?)</div>\s*</div>', re.S)
_NUMBERED = re.compile(r"(?:^|\s)(\d{1,2})\.\s+")
_QUESTION = re.compile(r"(.{8,180}?\?)\s*(.*)", re.S)


def _text(fragment: str) -> str:
    """Markup to prose. Script and style go first — Elementor inlines CSS into
    the content body, and without this every answer begins with a stylesheet."""
    fragment = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", fragment or "")
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    return " ".join(html_lib.unescape(fragment).split())


@dataclass(frozen=True)
class FaqPair:
    """One published question and the answer published with it."""

    host: str
    product: str
    section: str
    question: str
    answer: str
    source_url: str


def parse_entry(entry: dict[str, Any], host: str) -> list[FaqPair]:
    """Pull every question/answer pair out of one FAQ post."""
    product = _text(entry.get("title", {}).get("rendered", ""))
    body = entry.get("content", {}).get("rendered", "") or ""
    url = entry.get("link", "") or ""
    titles = {key: _text(value) for key, value in _TAB_TITLE.findall(body)}
    contents = {key: _text(value) for key, value in _TAB_CONTENT.findall(body)}

    out: list[FaqPair] = []
    for key, section in titles.items():
        text = contents.get(key, "")
        if not text:
            continue
        # split() on a capturing group yields [pre, n, chunk, n, chunk, ...]
        parts = _NUMBERED.split(text)
        for i in range(1, len(parts) - 1, 2):
            match = _QUESTION.match(parts[i + 1].strip())
            if not match:
                continue
            question, answer = match.group(1).strip(), match.group(2).strip()
            # A one-line answer is usually a stray numbered bullet inside a
            # longer answer rather than an answer of its own.
            if len(answer) < 25:
                continue
            out.append(
                FaqPair(
                    host=host,
                    product=product,
                    section=section,
                    question=question,
                    answer=answer[:1500],
                    source_url=url,
                )
            )
    return out


def fetch(host: str, *, rps: float = 1.0, client: httpx.Client | None = None) -> list[FaqPair]:
    """Every published FAQ pair on `host`, or an empty list if it does not
    serve them. A 403 is the expected answer from a host that blocks its REST
    API, and is not an error worth stopping a crawl over."""
    owned = client is None
    client = client or httpx.Client(
        timeout=30.0, follow_redirects=True, headers={"User-Agent": "insurancebot/0.2 (+faq)"}
    )
    pairs: list[FaqPair] = []
    try:
        page = 1
        while page <= 20:
            response = client.get(
                f"https://{host}/wp-json/wp/v2/{FAQ_POST_TYPE}",
                params={"per_page": 100, "page": page},
            )
            if response.status_code != 200:
                break
            batch = response.json()
            if not batch:
                break
            for entry in batch:
                pairs.extend(parse_entry(entry, host))
            total_pages = int(response.headers.get("X-WP-TotalPages", "1") or 1)
            if page >= total_pages:
                break
            page += 1
            if rps > 0:
                time.sleep(1.0 / rps)
    except Exception:
        return pairs
    finally:
        if owned:
            client.close()
    return pairs


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "faq"


def write(pairs: list[FaqPair], out_dir: Path) -> dict[str, int]:
    """One Markdown file per product, plus a JSON index the eval harness reads.

    Written under `raw/faq/` alongside the other sources so the compiler and the
    authority ordering treat them like anything else the site published.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    by_product: dict[str, list[FaqPair]] = {}
    for pair in pairs:
        by_product.setdefault(pair.product, []).append(pair)

    for product, group in sorted(by_product.items()):
        lines = [
            "---",
            f'source_url: "{group[0].source_url}"',
            f'host: "{group[0].host}"',
            f'title: "{product}"',
            'page_type: "faq"',
            f"pairs: {len(group)}",
            "---",
            "",
            f"# {product} — published FAQs",
            "",
        ]
        for pair in group:
            lines += [f"## {pair.question}", "", pair.answer, ""]
        (out_dir / f"{_slug(product)}.md").write_text("\n".join(lines))

    (out_dir / "faq-pairs.json").write_text(json.dumps([p.__dict__ for p in pairs], indent=1) + "\n")
    return {"products": len(by_product), "pairs": len(pairs)}
