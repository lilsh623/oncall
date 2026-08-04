"""Add reviewed cross-Incident experience memory.

Revision ID: 0005_experience_memory
Revises: 0004_action_plan_graph_run
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_experience_memory"
down_revision: str | None = "0004_action_plan_graph_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experience_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("pattern_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("symptoms", sa.JSON(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("action", sa.JSON(), nullable=False),
        sa.Column("verification", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("redaction_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["duplicate_of_id"], ["experience_candidates.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", name="uq_experience_candidates_incident"),
    )
    for name, columns in (
        ("ix_experience_candidates_incident_id", ["incident_id"]),
        ("ix_experience_candidates_project_id", ["project_id"]),
        ("ix_experience_candidates_environment", ["environment"]),
        ("ix_experience_candidates_service", ["service"]),
        ("ix_experience_candidates_pattern_fingerprint", ["pattern_fingerprint"]),
        ("ix_experience_candidates_status", ["status"]),
        ("ix_experience_candidates_duplicate_of_id", ["duplicate_of_id"]),
        ("ix_experience_candidates_reviewed_by", ["reviewed_by"]),
    ):
        op.create_index(name, "experience_candidates", columns)

    op.create_table(
        "experiences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("pattern_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("source_incident_ids", sa.JSON(), nullable=False),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["experience_candidates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", name="uq_experiences_candidate"),
        sa.UniqueConstraint("content_hash", name="uq_experiences_content_hash"),
    )
    for name, columns in (
        ("ix_experiences_candidate_id", ["candidate_id"]),
        ("ix_experiences_project_id", ["project_id"]),
        ("ix_experiences_environment", ["environment"]),
        ("ix_experiences_service", ["service"]),
        ("ix_experiences_pattern_fingerprint", ["pattern_fingerprint"]),
        ("ix_experiences_published_by", ["published_by"]),
        ("ix_experiences_status", ["status"]),
    ):
        op.create_index(name, "experiences", columns)


def downgrade() -> None:
    op.drop_table("experiences")
    op.drop_table("experience_candidates")
