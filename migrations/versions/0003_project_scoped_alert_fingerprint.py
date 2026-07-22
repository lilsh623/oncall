"""Scope alert fingerprints to a project.

Revision ID: 0003_project_alert_fingerprint
Revises: 0002_refresh_token_family
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0003_project_alert_fingerprint"
down_revision: str | None = "0002_refresh_token_family"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_alerts_source_fingerprint", "alerts", type_="unique")
    op.create_unique_constraint(
        "uq_alerts_source_project_fingerprint",
        "alerts",
        ["source", "project_id", "fingerprint"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_alerts_source_project_fingerprint", "alerts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_alerts_source_fingerprint", "alerts", ["source", "fingerprint"]
    )
