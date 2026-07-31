"""Add metadata jsonb to web_chunks so search results carry page_type /
expires_at / accurate_as_of uniformly with kb_chunks (needed by the
promo-freshness grader evidence).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-31
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE web_chunks ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE web_chunks DROP COLUMN metadata")
