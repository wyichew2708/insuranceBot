"""Dense retrieval over pgvector — recall for the lexical rank, never a bypass.

The lexical scorer is a share-of-information ratio, and on the questions
customers actually ask it ties: 87 pages at an identical score for "how do i
make a claim", 213 for "what is covered". v2 patched that at *product*
selection by handing the model the whole catalogue. It is still unpatched at
*section* retrieval, and it is why "my flat got flooded" and "my home got
flooded" reached different products. That is what vectors are for.

They are bound to one principle by construction: **a chunk found by
similarity is a candidate under the same frontmatter filter, the same
composition and the same nine gates as one found by words.** This module
returns page-level scores that `frontmatter_filter` fuses into its `scored`
dict before the filter ladder runs, so a draft, expired, wrong-jurisdiction or
wrong-channel chunk can never win on similarity. It never produces a claim.
Every admitted chunk still carries a page id and a source ref.

The toggle follows the codebase's two existing ones — `LLM_PROVIDER` and
`GUARDRAILS` — exactly: a lower-cased string, resolved at the consumption
site, where `auto` means "resolve from what is configured, and with nothing,
take the deterministic path". Degradation follows `Understanding.degraded`:
a fault is recorded, never raised into the turn.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import httpx

from okf import Bundle

#: How many sections to pull from the index before pooling to pages. Enough
#: that the right page's best section is present; small enough that the
#: post-filters below are cheap.
TOP_K = 40

#: The same, over `raw/`. Larger because far more of it is thrown away: a raw
#: pull is filtered afterwards by `may_support` — which drops the blog posts,
#: and 586 of the crawled pages are blog posts — by the customer's in-force
#: version, and by `must_include`. Twenty survivors of forty would be a thin
#: fallback; twenty of eighty is a fallback.
RAW_TOP_K = 80

#: Query timeout, seconds. Retrieval sits on the request path of every turn;
#: a slow database must degrade to lexical, not stall the customer.
QUERY_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class VectorHit:
    page_id: str
    heading: str
    similarity: float
    product_key: str | None = None


@dataclass(frozen=True)
class RawHit:
    """A section of an immutable source, found by similarity.

    Deliberately not a `RagHit`: this is what the index returned, before
    `may_support`, the customer's in-force version and `must_include` have had
    a say. `rag_search` applies all three and only then does it become a hit
    the turn may cite — the same discipline `frontmatter_filter` applies to a
    `VectorHit` before it can become an admitted page.
    """

    source_path: str
    heading: str
    similarity: float
    content: str


@dataclass
class VectorHits:
    """What the index returned, and why it did not if it did not."""

    hits: list[VectorHit] = field(default_factory=list)
    #: Why this is empty, when it is. `""` when the search ran. Recorded on
    #: the trace so a turn served lexically says which of the ways it was.
    degraded: str = ""

    @property
    def by_page(self) -> dict[str, float]:
        """Best section similarity per page — the score a page enters fusion
        with. Max rather than mean: one section that answers is enough, and a
        page is not penalised for also carrying a definitions list."""
        best: dict[str, float] = {}
        for hit in self.hits:
            if hit.similarity > best.get(hit.page_id, 0.0):
                best[hit.page_id] = hit.similarity
        return best

    @property
    def by_section(self) -> dict[tuple[str, str], float]:
        """Similarity per `(page_id, heading)` — the hit as the index actually
        found it, before `by_page` pools it away.

        The index is built one row per section and queried one row per section,
        and until now the heading was dropped on arrival: a page was lifted
        into the candidate set by a similarity nothing downstream could see, and
        the composer then picked which section to answer from on word overlap
        alone. That is the case this module's opening note describes and did not
        close — "my flat got flooded" and "my home got flooded" reach the same
        product now, and still pick different sections of it. `select_sections`
        reads this.

        Headings match the composer's exactly: both sides split with
        `compose.split_sections`, the indexer at build time and the composer at
        answer time, so a key is either present or the section genuinely was
        not in the index.
        """
        best: dict[tuple[str, str], float] = {}
        for hit in self.hits:
            key = (hit.page_id, hit.heading)
            if hit.similarity > best.get(key, 0.0):
                best[key] = hit.similarity
        return best


@dataclass
class RawHits:
    """What the raw index returned, and why it did not if it did not."""

    hits: list[RawHit] = field(default_factory=list)
    degraded: str = ""


class VectorSearch:
    """A resolved searcher: the mode decided, the endpoints known."""

    def __init__(
        self,
        dsn: str,
        embed_url: str,
        embed_model: str,
        mode: str,
        fail_closed: bool,
        rerank_url: str = "",
    ) -> None:
        self.dsn = dsn
        self.embed_url = embed_url.rstrip("/")
        self.embed_model = embed_model
        self.mode = mode
        self.fail_closed = fail_closed
        self.rerank_url = rerank_url.rstrip("/")
        self._memo: tuple[str, list[float]] | None = None

    def rerank(self, question: str, hits: list[VectorHit], texts: dict[str, str]) -> list[VectorHit]:
        """Re-score the top hits with a cross-encoder, where one is configured.

        A bi-encoder ranks "cooling-off period on car insurance" and the
        contribution clause close together because both are about the same
        policy; a cross-encoder reads question and section *together* and is
        far better at "does this section answer this". Optional: with no
        `rerank_url` the fused ranking stands. A fault leaves the order as it
        was — reranking improves precision and is never allowed to remove it.
        """
        if not self.rerank_url or not hits:
            return hits
        keyed = [h for h in hits if f"{h.page_id}#{h.heading}" in texts]
        if not keyed:
            return hits
        try:
            response = httpx.post(
                f"{self.rerank_url}/rerank",
                json={"query": question, "texts": [texts[f"{h.page_id}#{h.heading}"] for h in keyed]},
                timeout=QUERY_TIMEOUT_S,
            )
            response.raise_for_status()
            scored = {int(r["index"]): float(r["score"]) for r in response.json()}
        except Exception:
            return hits
        reranked = [
            VectorHit(h.page_id, h.heading, scored.get(i, h.similarity), h.product_key)
            for i, h in enumerate(keyed)
        ]
        return sorted(reranked, key=lambda h: -h.similarity)

    @property
    def enabled(self) -> bool:
        return self.mode != "off" and bool(self.dsn) and bool(self.embed_url)

    def embed(self, text: str) -> list[float]:
        """One question, one vector. The only embedding the request path does.

        Memoised on the last text, which is what keeps that sentence true now
        that a turn searches two indexes: `search` and `search_raw` embed the
        same question, and paying a second network round trip for a vector we
        are holding would be the request path's largest avoidable cost. The
        memo is one entry and lives on the searcher, and `searcher_for` builds
        a searcher per turn — so it cannot answer one customer's question with
        another's vector.
        """
        if self._memo is not None and self._memo[0] == text:
            return self._memo[1]
        response = httpx.post(
            f"{self.embed_url}/embeddings",
            json={"model": self.embed_model, "input": text},
            timeout=QUERY_TIMEOUT_S,
        )
        response.raise_for_status()
        vector = list(response.json()["data"][0]["embedding"])
        self._memo = (text, vector)
        return vector

    def search(self, bundle: Bundle, question: str, today: dt.date, top_k: int = TOP_K) -> VectorHits:
        """Sections nearest the question that the filter ladder would admit.

        Never raises. Every failure path returns an empty result with
        `degraded` set, and the caller carries on lexically — unless
        `fail_closed`, in which case the caller decides what an unreachable
        index is worth, exactly as `guardrail_fail_closed` does for a silent
        screen.
        """
        if not self.enabled:
            return VectorHits(degraded="not configured")
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError:
            return VectorHits(degraded="pgvector extra not installed")
        try:
            vector = self.embed(question)
        except Exception as exc:  # a provider fault is a degraded turn, never a failed one
            return VectorHits(degraded=f"embed: {type(exc).__name__}")
        # The WHERE clause is `frontmatter_filter`'s ladder, in SQL: approved,
        # in its effective window, not review-overdue, right jurisdiction, and
        # not withdrawn. Doing it here as well as in Python is deliberate —
        # the Python pass is what the trace explains; this is what keeps a
        # stale chunk from ever being the nearest neighbour.
        sql = """
            SELECT page_id, heading, product_key, 1 - (embedding <=> %(v)s) AS similarity, content
            FROM chunk
            WHERE bundle = %(bundle)s
              AND status = 'approved'
              AND jurisdiction = %(jur)s
              AND lifecycle <> 'withdrawn'
              AND (effective_from IS NULL OR effective_from <= %(today)s)
              AND (effective_to IS NULL OR effective_to >= %(today)s)
              AND (review_due IS NULL OR review_due >= %(today)s)
            ORDER BY embedding <=> %(v)s
            LIMIT %(k)s
        """
        params: dict[str, Any] = {
            "v": vector,
            "bundle": bundle.root.name,
            "jur": "SG",
            "today": today,
            "k": top_k,
        }
        try:
            with psycopg.connect(self.dsn, connect_timeout=int(QUERY_TIMEOUT_S)) as conn:
                register_vector(conn)
                conn.execute(f"SET statement_timeout = {int(QUERY_TIMEOUT_S * 1000)}")
                rows = conn.execute(sql, params).fetchall()
        except Exception as exc:
            return VectorHits(degraded=f"query: {type(exc).__name__}")
        hits = [
            VectorHit(page_id=r[0], heading=r[1], product_key=r[2], similarity=float(r[3]))
            for r in rows
            if bundle.get(r[0]) is not None  # the index may be ahead of or behind the bundle
        ]
        texts = {f"{r[0]}#{r[1]}": str(r[4]) for r in rows}
        return VectorHits(hits=self.rerank(question, hits, texts))

    def search_raw(self, bundle: Bundle, question: str, top_k: int = RAW_TOP_K) -> RawHits:
        """Sections of `raw/` nearest the question.

        The dense half of the RAG fallback. The fallback fires on exactly the
        questions the wiki cannot answer — a clause the compiler did not lift,
        a customer on a historic version, a word the corpus has never seen —
        and until now it looked for them by word overlap, which is the one
        retrieval a question phrased in the customer's words rather than the
        contract's is guaranteed to lose.

        Nothing is filtered here beyond the bundle. `may_support`, the
        in-force version and `must_include` are Python — see `rag_search`,
        which applies all three to this and to the lexical list alike before
        either can be cited. Never raises, for the same reason `search` does
        not: an index that is down costs the turn its recall, not its answer.
        """
        if not self.enabled:
            return RawHits(degraded="not configured")
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError:
            return RawHits(degraded="pgvector extra not installed")
        try:
            vector = self.embed(question)
        except Exception as exc:
            return RawHits(degraded=f"embed: {type(exc).__name__}")
        sql = """
            SELECT source_path, heading, 1 - (embedding <=> %(v)s) AS similarity, content
            FROM raw_chunk
            WHERE bundle = %(bundle)s
            ORDER BY embedding <=> %(v)s
            LIMIT %(k)s
        """
        params: dict[str, Any] = {"v": vector, "bundle": bundle.root.name, "k": top_k}
        try:
            with psycopg.connect(self.dsn, connect_timeout=int(QUERY_TIMEOUT_S)) as conn:
                register_vector(conn)
                conn.execute(f"SET statement_timeout = {int(QUERY_TIMEOUT_S * 1000)}")
                rows = conn.execute(sql, params).fetchall()
        except Exception as exc:
            return RawHits(degraded=f"query: {type(exc).__name__}")
        return RawHits(
            hits=[
                RawHit(source_path=r[0], heading=r[1], similarity=float(r[2]), content=str(r[3]))
                for r in rows
            ]
        )


def searcher_for(settings: Any) -> VectorSearch | None:
    """Resolve the vector layer from settings, the way `provider_for` resolves
    a provider. None means the lexical path and nothing else — no stage
    opened, no connection tried — which is what `auto` with an empty DSN, and
    `off`, both mean."""
    mode = (getattr(settings, "pgvector", "") or "auto").lower()
    dsn = getattr(settings, "pgvector_dsn", "") or ""
    embed_url = getattr(settings, "embed_base_url", "") or ""
    if mode in {"off", "none", "false"}:
        return None
    if mode == "auto" and not (dsn and embed_url):
        return None
    return VectorSearch(
        dsn=dsn,
        embed_url=embed_url,
        embed_model=getattr(settings, "embed_model", "BAAI/bge-m3") or "BAAI/bge-m3",
        mode="on" if mode == "on" else "auto",
        fail_closed=bool(getattr(settings, "pgvector_fail_closed", False)),
        rerank_url=getattr(settings, "rerank_base_url", "") or "",
    )
