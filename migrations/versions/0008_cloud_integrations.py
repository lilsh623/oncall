"""Add Tencent Cloud project, service and alert integration catalog.

Revision ID: 0008_cloud_integrations
Revises: 0007_conversations_evaluations
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_cloud_integrations"
down_revision: str | None = "0007_conversations_evaluations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cloud_projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_cloud_projects_project_id"),
    )
    op.create_index("ix_cloud_projects_project_id", "cloud_projects", ["project_id"])
    op.create_index("ix_cloud_projects_created_by", "cloud_projects", ["created_by"])

    op.create_table(
        "cloud_services",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cloud_project_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("runtime_type", sa.String(32), nullable=False),
        sa.Column("resource_config", sa.JSON(), nullable=False),
        sa.Column("observability_config", sa.JSON(), nullable=False),
        sa.Column("recovery_config", sa.JSON(), nullable=False),
        sa.Column("health_config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["cloud_project_id"], ["cloud_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cloud_project_id", "environment", "service", name="uq_cloud_services_scope"),
    )
    for column in ("cloud_project_id", "environment", "service"):
        op.create_index(f"ix_cloud_services_{column}", "cloud_services", [column])

    op.create_table(
        "alert_integrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cloud_project_id", sa.Uuid(), nullable=False),
        sa.Column("integration_key", sa.String(96), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("mapping_config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["cloud_project_id"], ["cloud_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("integration_key", name="uq_alert_integrations_key"),
    )
    for column in ("cloud_project_id", "integration_key", "source"):
        op.create_index(f"ix_alert_integrations_{column}", "alert_integrations", [column])


def downgrade() -> None:
    op.drop_table("alert_integrations")
    op.drop_table("cloud_services")
    op.drop_table("cloud_projects")
