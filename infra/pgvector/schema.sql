-- The vector index over the compiled wiki. One row per section — heading
-- bound to body, the unit the composer works in — with the frontmatter
-- columns retrieval filters on, so the WHERE clause can do what
-- `frontmatter_filter` does and a draft or expired chunk can never win on
-- similarity. Rebuilt by `make index`; keyed by content hash so a recompile
-- re-embeds only what changed. Applied once by the postgres container on
-- first start (docker-entrypoint-initdb.d); idempotent for `psql -f`.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunk (
    id               text PRIMARY KEY,           -- page_id#heading
    bundle           text NOT NULL,
    page_id          text NOT NULL,
    heading          text NOT NULL,
    product_key      text,
    page_type        text NOT NULL,
    status           text NOT NULL,
    lifecycle        text NOT NULL,
    jurisdiction     text NOT NULL,
    version_in_force text,
    effective_from   date,
    effective_to     date,
    review_due       date,
    authority        text[],
    compiled_by      text NOT NULL DEFAULT 'compiler',   -- 'compiler' | 'llm'
    contested        boolean NOT NULL DEFAULT false,     -- a figure the compiler filed a conflict on
    source_refs      text[],
    content          text NOT NULL,
    content_hash     text NOT NULL,
    embedding        vector(1024) NOT NULL,              -- BAAI/bge-m3
    indexed_at       timestamptz NOT NULL DEFAULT now()
);

-- HNSW over cosine distance: the query side is `embedding <=> $1`.
CREATE INDEX IF NOT EXISTS chunk_embedding_hnsw
    ON chunk USING hnsw (embedding vector_cosine_ops);
-- The filter columns every query carries.
CREATE INDEX IF NOT EXISTS chunk_bundle_status
    ON chunk (bundle, status, jurisdiction);
CREATE INDEX IF NOT EXISTS chunk_page
    ON chunk (bundle, page_id);

-- What the API compares against the served bundle on /readyz: if the set of
-- content hashes in the index differs from the bundle's, the index is stale
-- and the API says so rather than serving it.
CREATE OR REPLACE VIEW chunk_fingerprint AS
    SELECT bundle, count(*) AS chunks, md5(string_agg(content_hash, ',' ORDER BY id)) AS fingerprint
    FROM chunk GROUP BY bundle;

-- The vector index over the IMMUTABLE sources. One row per `##` section of a
-- raw markdown document — the same unit `api.retrieval.raw_sections` splits at
-- answer time, so an exclusion is never separated from its parent benefit.
--
-- A separate table from `chunk`, not a `layer` column on it, because the two
-- hold different things and are filtered differently. A wiki chunk is a
-- compiled, approved, dated page and its WHERE clause is the frontmatter
-- ladder; a raw chunk has no frontmatter at all — it is a PDF someone
-- published — and what guards it is `okf.sources.may_support` plus the
-- customer's in-force version, both of which are Python, not SQL. Sharing a
-- table would mean one query whose WHERE clause is right for half its rows.
CREATE TABLE IF NOT EXISTS raw_chunk (
    id           text PRIMARY KEY,          -- source_path#heading
    bundle       text NOT NULL,
    source_path  text NOT NULL,             -- 'raw/wordings/etiqa-….md', as cited
    heading      text NOT NULL,
    -- What `okf.sources.page_type_of_text` made of the document. Carried so
    -- the query side can apply `may_support` — 586 of the crawled pages are
    -- blog posts, and the RAG fallback is the one path by which a blog
    -- sentence could reach a customer as the answer.
    doc_type     text NOT NULL,
    content      text NOT NULL,
    content_hash text NOT NULL,
    embedding    vector(1024) NOT NULL,     -- BAAI/bge-m3, as `chunk`
    indexed_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS raw_chunk_embedding_hnsw
    ON raw_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS raw_chunk_bundle
    ON raw_chunk (bundle, source_path);

-- Same fingerprint contract as `chunk_fingerprint`: /readyz compares this
-- against the sources on disk and refuses to call a stale index fresh.
CREATE OR REPLACE VIEW raw_chunk_fingerprint AS
    SELECT bundle, count(*) AS chunks, md5(string_agg(content_hash, ',' ORDER BY id)) AS fingerprint
    FROM raw_chunk GROUP BY bundle;
