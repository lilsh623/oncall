"""Add refresh-token families for reuse detection.

Revision ID: 0002_refresh_token_family
Revises: 0001_initial
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_refresh_token_family"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("family_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE refresh_tokens SET revoked_at = CURRENT_TIMESTAMP "
        "WHERE revoked_at IS NULL"
    )
    op.execute("UPDATE refresh_tokens SET family_id = id WHERE family_id IS NULL")
    op.alter_column("refresh_tokens", "family_id", nullable=False)
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "family_id")
