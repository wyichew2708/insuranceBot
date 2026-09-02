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

#: Query timeout, seconds. Retrieval sits on the request path of every turn;
#: a slow database must degrade to lexical, not stall the customer.
QUERY_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class VectorHit:
    page_id: str
    heading: str
    similarity: float
    product_key: str | None = None


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


class VectorSearch:
    """A resolved searcher: the mode decided, the endpoints known."""

    def __init__(self, dsn: str, embed_url: str, embed_model: str, mode: str, fail_closed: bool) -> None:
        self.dsn = dsn
        self.embed_url = embed_url.rstrip("/")
        self.embed_model = embed_model
        self.mode = mode
        self.fail_closed = fail_closed

    @property
    def enabled(self) -> bool:
        return self.mode != "off" and bool(self.dsn) and bool(self.embed_url)

    def embed(self, text: str) -> list[float]:
        """One question, one vector. The only embedding the request path does."""
        response = httpx.post(
            f"{self.embed_url}/embeddings",
            json={"model": self.embed_model, "input": text},
            timeout=QUERY_TIMEOUT_S,
        )
        response.raise_for_status()
        return list(response.json()["data"][0]["embedding"])

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
            SELECT page_id, heading, product_key, 1 - (embedding <=> %(v)s) AS similarity
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
        return VectorHits(hits=hits)


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
    )
