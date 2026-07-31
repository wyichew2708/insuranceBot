import datetime as dt

from crawler.indexer import MAX_CHUNK_WORDS, chunk_page_text, page_metadata
from crawler.worker import CrawledPage, extract_links, rel_canonical


def test_short_text_is_one_chunk() -> None:
    assert chunk_page_text("a short page") == ["a short page"]
    assert chunk_page_text("") == []


def test_long_text_chunks_with_overlap() -> None:
    words = [f"w{i}" for i in range(1000)]
    chunks = chunk_page_text(" ".join(words))
    assert len(chunks) > 1
    assert all(len(c.split()) <= MAX_CHUNK_WORDS for c in chunks)
    # overlap: the start of chunk 2 repeats the tail of chunk 1
    tail = chunks[0].split()[-1]
    assert tail in chunks[1].split()
    # nothing lost
    assert set(words) <= {w for c in chunks for w in c.split()}


def test_page_metadata_carries_freshness_fields() -> None:
    page = CrawledPage(
        url="https://www.example.test/promo",
        canonical_url="https://www.example.test/promo",
        brand="tiq",
        page_type="promo",
        text="x",
        fetched_at=dt.datetime(2026, 7, 31, tzinfo=dt.UTC),
        expires_at=dt.datetime(2026, 8, 31, tzinfo=dt.UTC),
        accurate_as_of=dt.date(2026, 7, 1),
        demoted=False,
    )
    meta = page_metadata(page)
    assert meta["expires_at"].startswith("2026-08-31")
    assert meta["accurate_as_of"] == "2026-07-01"
    assert meta["page_type"] == "promo"


def test_rel_canonical_extraction() -> None:
    html = '<head><link rel="canonical" href="https://www.example.test/tiq-invest" /></head>'
    assert rel_canonical(html) == "https://www.example.test/tiq-invest"
    assert rel_canonical("<head></head>") is None


def test_extract_links_absolute_and_relative() -> None:
    html = '<a href="/travel">t</a> <a href="https://other.test/page">o</a> <a href="#frag">f</a>'
    links = extract_links(html, "https://www.example.test/home")
    assert "https://www.example.test/travel" in links
    assert "https://other.test/page" in links
    assert not any("#" in link for link in links)
