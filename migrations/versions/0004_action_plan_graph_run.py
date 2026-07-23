"""Persist the LangGraph thread that owns an approval boundary.

Revision ID: 0004_action_plan_graph_run
Revises: 0003_project_alert_fingerprint
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_action_plan_graph_run"
down_revision: str | None = "0003_project_alert_fingerprint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("action_plans", sa.Column("graph_run_id", sa.String(length=256), nullable=True))
    op.create_index("ix_action_plans_graph_run_id", "action_plans", ["graph_run_id"])


def downgrade() -> None:
    op.drop_index("ix_action_plans_graph_run_id", table_name="action_plans")
    op.drop_column("action_plans", "graph_run_id")
