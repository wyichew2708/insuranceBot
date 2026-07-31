"""Fix bundle versioning: kb_chunks keyed by (chunk_id, language, bundle_id).

The original chunk_id PK meant (a) a new bundle overwrote the previous
bundle's rows, so "keep last 3 bundles" and rollback were impossible, and
(b) language variants of the same block id collided and silently lost all
but one language. Language is denormalised into a real column (backfilled
from metadata) so it can participate in the key.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-31
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS language text NOT NULL DEFAULT 'en';
        UPDATE kb_chunks SET language = COALESCE(metadata->>'language', 'en');
        ALTER TABLE kb_chunks DROP CONSTRAINT kb_chunks_pkey;
        ALTER TABLE kb_chunks ADD PRIMARY KEY (chunk_id, language, bundle_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE kb_chunks DROP CONSTRAINT kb_chunks_pkey;
        ALTER TABLE kb_chunks ADD PRIMARY KEY (chunk_id);
        ALTER TABLE kb_chunks DROP COLUMN language;
        """
    )
