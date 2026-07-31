"""Enable pgcrypto for at-rest encryption of raw message content, and index
messages.created_at for the retention TTL job (§9.2, §10.4).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-31
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE INDEX IF NOT EXISTS messages_created_idx ON messages (created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS messages_created_idx")
