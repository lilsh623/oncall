"""Create the OnCall operational data model.

Revision ID: 0001_initial
Revises: None
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INCIDENT_STATUSES = (
    "RECEIVED",
    "TRIAGING",
    "INVESTIGATING",
    "DIAGNOSED",
    "PLANNING",
    "WAITING_APPROVAL",
    "EXECUTING",
    "VERIFYING",
    "RESOLVED",
    "NEED_HUMAN",
    "FAILED",
)


def _timestamps(*, mutable: bool = False) -> list[sa.Column]:
    columns = [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        )
    ]
    if mutable:
        columns.append(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
    return columns


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, nullable=False)


def upgrade() -> None:
    op.create_table(
        "users",
        _id(),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(mutable=True),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "refresh_tokens",
        _id(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        *_timestamps(mutable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    op.create_table(
        "raw_alert_events",
        _id(),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_raw_alert_events_source", "raw_alert_events", ["source"])
    op.create_index("ix_raw_alert_events_project_id", "raw_alert_events", ["project_id"])

    op.create_table(
        "alerts",
        _id(),
        sa.Column("raw_event_id", sa.Uuid()),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("alert_name", sa.String(256), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("correlation_key", sa.String(256)),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("annotations", sa.JSON(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        *_timestamps(mutable=True),
        sa.ForeignKeyConstraint(["raw_event_id"], ["raw_alert_events.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("source", "fingerprint", name="uq_alerts_source_fingerprint"),
    )
    for column in ("raw_event_id", "project_id", "environment", "service", "correlation_key"):
        op.create_index(f"ix_alerts_{column}", "alerts", [column])

    op.create_table(
        "incidents",
        _id(),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                *INCIDENT_STATUSES,
                name="incident_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("correlation_key", sa.String(256)),
        sa.Column("summary", sa.Text()),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        *_timestamps(mutable=True),
    )
    for column in ("project_id", "environment", "service", "correlation_key"):
        op.create_index(f"ix_incidents_{column}", "incidents", [column])

    op.create_table(
        "incident_alerts",
        _id(),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("incident_id", "alert_id", name="uq_incident_alerts_incident_alert"),
    )
    op.create_index("ix_incident_alerts_incident_id", "incident_alerts", ["incident_id"])
    op.create_index("ix_incident_alerts_alert_id", "incident_alerts", ["alert_id"])

    op.create_table(
        "evidence",
        _id(),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.String(1024), nullable=False),
        sa.Column("query_summary", sa.Text()),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_evidence_incident_id", "evidence", ["incident_id"])

    op.create_table(
        "hypotheses",
        _id(),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("supporting_summary", sa.Text()),
        sa.Column("opposing_summary", sa.Text()),
        sa.Column("next_check", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False),
        *_timestamps(mutable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_hypotheses_incident_id", "hypotheses", ["incident_id"])

    op.create_table(
        "hypothesis_evidence",
        _id(),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("relationship", sa.String(16), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["hypotheses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("hypothesis_id", "evidence_id", name="uq_hypothesis_evidence_pair"),
    )
    op.create_index("ix_hypothesis_evidence_hypothesis_id", "hypothesis_evidence", ["hypothesis_id"])
    op.create_index("ix_hypothesis_evidence_evidence_id", "hypothesis_evidence", ["evidence_id"])

    op.create_table(
        "knowledge_citations",
        _id(),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.String(256), nullable=False),
        sa.Column("document_version", sa.String(128)),
        sa.Column("section", sa.String(512)),
        sa.Column("file_path", sa.String(1024)),
        sa.Column("locator", sa.String(512)),
        sa.Column("excerpt", sa.Text()),
        sa.Column("score", sa.Float()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_knowledge_citations_incident_id", "knowledge_citations", ["incident_id"])
    op.create_index("ix_knowledge_citations_document_id", "knowledge_citations", ["document_id"])

    op.create_table(
        "action_plans",
        _id(),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("prerequisites", sa.JSON(), nullable=False),
        sa.Column("rollback", sa.JSON(), nullable=False),
        sa.Column("verification_criteria", sa.JSON(), nullable=False),
        sa.Column("plan_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        *_timestamps(mutable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_action_plans_incident_id", "action_plans", ["incident_id"])
    op.create_index("ix_action_plans_plan_hash", "action_plans", ["plan_hash"])

    op.create_table(
        "action_steps",
        _id(),
        sa.Column("action_plan_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("tool_name", sa.String(256), nullable=False),
        sa.Column("tool_arguments", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("expected_result", sa.Text()),
        sa.Column("rollback_spec", sa.JSON(), nullable=False),
        *_timestamps(mutable=True),
        sa.ForeignKeyConstraint(["action_plan_id"], ["action_plans.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("action_plan_id", "sequence", name="uq_action_steps_plan_sequence"),
    )
    op.create_index("ix_action_steps_action_plan_id", "action_steps", ["action_plan_id"])

    op.create_table(
        "approvals",
        _id(),
        sa.Column("action_plan_id", sa.Uuid(), nullable=False),
        sa.Column("action_plan_hash", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(mutable=True),
        sa.ForeignKeyConstraint(["action_plan_id"], ["action_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "action_plan_id", "action_plan_hash", "user_id", name="uq_approvals_plan_hash_user"
        ),
    )
    op.create_index("ix_approvals_action_plan_id", "approvals", ["action_plan_id"])
    op.create_index("ix_approvals_user_id", "approvals", ["user_id"])

    op.create_table(
        "executions",
        _id(),
        sa.Column("action_plan_id", sa.Uuid(), nullable=False),
        sa.Column("action_step_id", sa.Uuid()),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("tool_name", sa.String(256), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("response", sa.JSON()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        *_timestamps(mutable=True),
        sa.ForeignKeyConstraint(["action_plan_id"], ["action_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_step_id"], ["action_steps.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_executions_idempotency_key"),
    )
    op.create_index("ix_executions_action_plan_id", "executions", ["action_plan_id"])
    op.create_index("ix_executions_action_step_id", "executions", ["action_step_id"])

    op.create_table(
        "verification_checks",
        _id(),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid()),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("observed_value", sa.JSON(), nullable=False),
        sa.Column("conclusion", sa.String(64)),
        sa.Column("reason", sa.Text()),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        *_timestamps(mutable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_verification_checks_incident_id", "verification_checks", ["incident_id"])
    op.create_index("ix_verification_checks_execution_id", "verification_checks", ["execution_id"])

    op.create_table(
        "audit_events",
        _id(),
        sa.Column("incident_id", sa.Uuid()),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(256), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_audit_events_incident_id", "audit_events", ["incident_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_actor", "audit_events", ["actor"])


def downgrade() -> None:
    for table in (
        "audit_events",
        "verification_checks",
        "executions",
        "approvals",
        "action_steps",
        "action_plans",
        "knowledge_citations",
        "hypothesis_evidence",
        "hypotheses",
        "evidence",
        "incident_alerts",
        "incidents",
        "alerts",
        "raw_alert_events",
        "refresh_tokens",
        "users",
    ):
        op.drop_table(table)
