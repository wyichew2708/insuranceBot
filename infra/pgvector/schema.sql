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
