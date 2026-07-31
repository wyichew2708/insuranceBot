"""Baseline schema: all core tables (§4.2), pgvector required.

Revision ID: 0001
Revises:
Create Date: 2026-07-31
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES_DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE kb_chunks (
    chunk_id    text PRIMARY KEY,
    block_id    text NOT NULL,
    bundle_id   text NOT NULL,
    text        text NOT NULL,
    dense       vector(1024),
    sparse      jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata    jsonb NOT NULL DEFAULT '{}'::jsonb,
    active      boolean NOT NULL DEFAULT false
);
CREATE INDEX kb_chunks_block_idx ON kb_chunks (block_id);
CREATE INDEX kb_chunks_active_idx ON kb_chunks (active);
CREATE INDEX kb_chunks_bundle_idx ON kb_chunks (bundle_id);
CREATE INDEX kb_chunks_metadata_idx ON kb_chunks USING gin (metadata);

CREATE TABLE web_chunks (
    chunk_id        text PRIMARY KEY,
    url             text NOT NULL,
    canonical_url   text NOT NULL,
    brand           text NOT NULL,
    text            text NOT NULL,
    dense           vector(1024),
    sparse          jsonb NOT NULL DEFAULT '{}'::jsonb,
    fetched_at      timestamptz NOT NULL,
    expires_at      timestamptz NOT NULL,
    accurate_as_of  date,
    page_type       text NOT NULL DEFAULT 'other',
    demoted         boolean NOT NULL DEFAULT false
);
CREATE INDEX web_chunks_brand_idx ON web_chunks (brand);
CREATE INDEX web_chunks_expiry_idx ON web_chunks (expires_at);
CREATE INDEX web_chunks_canonical_idx ON web_chunks (canonical_url);

CREATE TABLE catalogue_products (
    product_code text PRIMARY KEY,
    brand        text[] NOT NULL,
    line         text NOT NULL,
    name         text NOT NULL,
    data         jsonb NOT NULL DEFAULT '{}'::jsonb,
    bundle_id    text NOT NULL
);

CREATE TABLE actions (
    action_id text NOT NULL,
    brand     text NOT NULL,
    kind      text NOT NULL CHECK (kind IN ('link', 'phone', 'email')),
    value     text NOT NULL,
    label     text NOT NULL,
    verbatim  boolean NOT NULL DEFAULT false,
    PRIMARY KEY (action_id, brand)
);

CREATE TABLE sessions (
    session_id text PRIMARY KEY,
    channel    text NOT NULL,
    brand      text NOT NULL,
    audience   text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    state      jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE messages (
    id               bigserial PRIMARY KEY,
    session_id       text NOT NULL REFERENCES sessions (session_id),
    role             text NOT NULL,
    content          text NOT NULL,
    redacted_content text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_session_idx ON messages (session_id);

CREATE TABLE feedback (
    id         bigserial PRIMARY KEY,
    session_id text NOT NULL,
    message_id bigint REFERENCES messages (id),
    rating     smallint NOT NULL,
    comment    text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id         bigserial PRIMARY KEY,
    session_id text NOT NULL,
    event      text NOT NULL,
    payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_session_idx ON audit_log (session_id);

CREATE TABLE eval_runs (
    id         bigserial PRIMARY KEY,
    bundle_id  text,
    git_sha    text,
    suite      text NOT NULL,
    pass_rate  double precision NOT NULL,
    report     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
"""

DROP_DDL = """
DROP TABLE IF EXISTS eval_runs, audit_log, feedback, messages, sessions,
    actions, catalogue_products, web_chunks, kb_chunks CASCADE;
"""


def upgrade() -> None:
    op.execute(TABLES_DDL)


def downgrade() -> None:
    op.execute(DROP_DDL)
