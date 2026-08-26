"""Published FAQ extraction."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from crawler.faqs import FaqPair, fetch, parse_entry, write

# Elementor's shape: an accordion pane per category, numbered questions inside.
ENTRY = {
    "title": {"rendered": "Tiq Travel Insurance"},
    "link": "https://www.tiq.com.sg/faq/travel/",
    "content": {
        "rendered": """
        <style>.page_btn{color:#3a3a3a}</style>
        <div class="elementor-accordion">
          <div id="elementor-tab-title-11" class="elementor-tab-title">
            <span class="elementor-accordion-icon"><i class="fas"></i></span>
            <a class="elementor-accordion-title">Product Coverage and Benefits</a>
          </div>
          <div id="elementor-tab-content-11" class="elementor-tab-content">
            <p>1. What does Tiq Travel cover? It covers overseas medical expenses,
            trip cancellation and baggage up to the plan limits.</p>
            <p>2. Is COVID-19 covered? Yes, subject to the terms of the policy wording.</p>
            <p>3. Too short? Nope.</p>
          </div>
        </div></div>
        """
    },
}


def test_pairs_are_extracted_with_their_published_answer() -> None:
    pairs = parse_entry(ENTRY, "www.tiq.com.sg")
    assert [p.question for p in pairs] == [
        "What does Tiq Travel cover?",
        "Is COVID-19 covered?",
    ]
    assert pairs[0].product == "Tiq Travel Insurance"
    assert pairs[0].section == "Product Coverage and Benefits"
    assert "overseas medical expenses" in pairs[0].answer


def test_inline_css_never_reaches_the_answer() -> None:
    """Elementor inlines a stylesheet into the content body. Without stripping
    it, every answer begins with CSS — which is what the first extraction did."""
    pairs = parse_entry(ENTRY, "www.tiq.com.sg")
    assert all("page_btn" not in p.answer and "#3a3a3a" not in p.answer for p in pairs)


def test_a_numbered_line_with_no_real_answer_is_dropped() -> None:
    """Numbered bullets inside a longer answer look like questions. A pair with
    almost no answer is one of those, not a question the insurer published."""
    assert all(len(p.answer) >= 25 for p in parse_entry(ENTRY, "www.tiq.com.sg"))


def test_a_host_without_the_endpoint_yields_nothing_rather_than_failing() -> None:
    """etiqa.com.sg answers 403. That is a fact about the host, not a reason to
    fail a crawl."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch("www.etiqa.com.sg", client=client) == []


def test_pagination_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params).get("page", "1"))
        if page > 2:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[ENTRY], headers={"X-WP-TotalPages": "2"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pairs = fetch("www.tiq.com.sg", rps=0, client=client)
    assert len(pairs) == 4  # two questions, two pages


def test_written_files_carry_the_question_as_a_heading(tmp_path: Path) -> None:
    pairs = [
        FaqPair("www.tiq.com.sg", "Tiq Travel", "Cover", "What is covered?", "Medical and baggage.", "u"),
    ]
    stats = write(pairs, tmp_path)
    assert stats == {"products": 1, "pairs": 1}
    body = (tmp_path / "tiq-travel.md").read_text()
    assert "## What is covered?" in body and "Medical and baggage." in body
    assert json.loads((tmp_path / "faq-pairs.json").read_text())[0]["question"] == "What is covered?"
