"""Add reviewed Experience-to-Skill evolution.

Revision ID: 0006_learned_skills
Revises: 0005_experience_memory
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_learned_skills"
down_revision: str | None = "0005_experience_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("experience_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("skill_name", sa.String(length=64), nullable=False),
        sa.Column("proposed_version", sa.String(length=32), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_experience_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["experience_id"], ["experiences.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experience_id", name="uq_skill_candidates_experience"),
    )
    for name, columns in (
        ("ix_skill_candidates_experience_id", ["experience_id"]),
        ("ix_skill_candidates_project_id", ["project_id"]),
        ("ix_skill_candidates_environment", ["environment"]),
        ("ix_skill_candidates_service", ["service"]),
        ("ix_skill_candidates_skill_name", ["skill_name"]),
        ("ix_skill_candidates_status", ["status"]),
        ("ix_skill_candidates_reviewed_by", ["reviewed_by"]),
    ):
        op.create_index(name, "skill_candidates", columns)

    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("skill_name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_experience_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["skill_candidates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", name="uq_skill_versions_candidate"),
        sa.UniqueConstraint(
            "project_id", "skill_name", "version", name="uq_skill_versions_identity"
        ),
    )
    for name, columns in (
        ("ix_skill_versions_candidate_id", ["candidate_id"]),
        ("ix_skill_versions_project_id", ["project_id"]),
        ("ix_skill_versions_environment", ["environment"]),
        ("ix_skill_versions_service", ["service"]),
        ("ix_skill_versions_skill_name", ["skill_name"]),
        ("ix_skill_versions_status", ["status"]),
        ("ix_skill_versions_published_by", ["published_by"]),
    ):
        op.create_index(name, "skill_versions", columns)


def downgrade() -> None:
    op.drop_table("skill_versions")
    op.drop_table("skill_candidates")
