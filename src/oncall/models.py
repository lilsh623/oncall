"""SQLAlchemy models for OnCall runtime facts, audit history, and reviewed experience."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from oncall.database import Base
from oncall.incidents.state import IncidentStatus


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for Python-side defaults."""

    return datetime.now(UTC)


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )


class UpdatedAtMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
        nullable=False,
    )


class User(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    username: Mapped[str] = mapped_column(String(128), index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class RefreshToken(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[UUID] = mapped_column(Uuid, default=uuid4, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RawAlertEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "raw_alert_events"

    source: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Alert(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "project_id",
            "fingerprint",
            name="uq_alerts_source_project_fingerprint",
        ),
    )

    raw_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("raw_alert_events.id", ondelete="SET NULL"), index=True
    )
    source: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(128))
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    environment: Mapped[str] = mapped_column(String(64), index=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    alert_name: Mapped[str] = mapped_column(String(256))
    severity: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    correlation_key: Mapped[str | None] = mapped_column(String(256), index=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    annotations: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)


class Incident(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "incidents"

    title: Mapped[str] = mapped_column(String(512))
    status: Mapped[IncidentStatus] = mapped_column(
        SAEnum(
            IncidentStatus,
            name="incident_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=IncidentStatus.RECEIVED,
    )
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    environment: Mapped[str] = mapped_column(String(64), index=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    correlation_key: Mapped[str | None] = mapped_column(String(256), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncidentAlert(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "incident_alerts"
    __table_args__ = (
        UniqueConstraint("incident_id", "alert_id", name="uq_incident_alerts_incident_alert"),
    )

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    alert_id: Mapped[UUID] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True
    )


class Evidence(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evidence"

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(64))
    source_ref: Mapped[str] = mapped_column(String(1024))
    query_summary: Mapped[str | None] = mapped_column(Text)
    observation: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Hypothesis(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "hypotheses"

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    supporting_summary: Mapped[str | None] = mapped_column(Text)
    opposing_summary: Mapped[str | None] = mapped_column(Text)
    next_check: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="OPEN")


class HypothesisEvidence(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "hypothesis_evidence"
    __table_args__ = (
        UniqueConstraint(
            "hypothesis_id", "evidence_id", name="uq_hypothesis_evidence_pair"
        ),
    )

    hypothesis_id: Mapped[UUID] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="CASCADE"), index=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), index=True
    )
    relationship: Mapped[str] = mapped_column(String(16), default="SUPPORTS")


class KnowledgeCitation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_citations"

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(String(256), index=True)
    document_version: Mapped[str | None] = mapped_column(String(128))
    section: Mapped[str | None] = mapped_column(String(512))
    file_path: Mapped[str | None] = mapped_column(String(1024))
    locator: Mapped[str | None] = mapped_column(String(512))
    excerpt: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)


class ActionPlan(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "action_plans"

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    graph_run_id: Mapped[str | None] = mapped_column(String(256), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(32))
    prerequisites: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rollback: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verification_criteria: Mapped[list[Any]] = mapped_column(JSON, default=list)
    plan_hash: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")


class ActionStep(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "action_steps"
    __table_args__ = (
        UniqueConstraint("action_plan_id", "sequence", name="uq_action_steps_plan_sequence"),
    )

    action_plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("action_plans.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(256))
    tool_name: Mapped[str] = mapped_column(String(256))
    tool_arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_level: Mapped[str] = mapped_column(String(32))
    expected_result: Mapped[str | None] = mapped_column(Text)
    rollback_spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Approval(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint(
            "action_plan_id",
            "action_plan_hash",
            "user_id",
            name="uq_approvals_plan_hash_user",
        ),
    )

    action_plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("action_plans.id", ondelete="CASCADE"), index=True
    )
    action_plan_hash: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    decision: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Execution(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "executions"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_executions_idempotency_key"),)

    action_plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("action_plans.id", ondelete="CASCADE"), index=True
    )
    action_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("action_steps.id", ondelete="SET NULL"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32))
    tool_name: Mapped[str] = mapped_column(String(256))
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class VerificationCheck(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "verification_checks"

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("executions.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32))
    observed_value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    conclusion: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperienceCandidate(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """A grounded, redacted experience proposal extracted from one resolved Incident."""

    __tablename__ = "experience_candidates"
    __table_args__ = (
        UniqueConstraint("incident_id", name="uq_experience_candidates_incident"),
    )

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    environment: Mapped[str] = mapped_column(String(64), index=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    pattern_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text)
    symptoms: Mapped[list[Any]] = mapped_column(JSON, default=list)
    root_cause: Mapped[str] = mapped_column(Text)
    action: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verification: Mapped[list[Any]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    source_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    redaction_version: Mapped[str] = mapped_column(String(32), default="1.0")
    status: Mapped[str] = mapped_column(String(32), default="PENDING_REVIEW", index=True)
    duplicate_of_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experience_candidates.id", ondelete="SET NULL"), index=True
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    review_comment: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Experience(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """An immutable-in-spirit reviewed experience available to future retrieval."""

    __tablename__ = "experiences"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_experiences_candidate"),
        UniqueConstraint("content_hash", name="uq_experiences_content_hash"),
    )

    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("experience_candidates.id", ondelete="RESTRICT"), index=True
    )
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    environment: Mapped[str] = mapped_column(String(64), index=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    pattern_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(512))
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_incident_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    published_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    status: Mapped[str] = mapped_column(String(32), default="PUBLISHED", index=True)


class SkillCandidate(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """A reviewed-experience-derived Skill proposal that has no runtime authority."""

    __tablename__ = "skill_candidates"
    __table_args__ = (
        UniqueConstraint("experience_id", name="uq_skill_candidates_experience"),
    )

    experience_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiences.id", ondelete="RESTRICT"), index=True
    )
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    environment: Mapped[str] = mapped_column(String(64), index=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    skill_name: Mapped[str] = mapped_column(String(64), index=True)
    proposed_version: Mapped[str] = mapped_column(String(32))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    instructions: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_experience_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="PENDING_REVIEW", index=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    review_comment: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SkillVersion(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """A versioned, active-or-retired learned investigation Skill."""

    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_skill_versions_candidate"),
        UniqueConstraint(
            "project_id", "skill_name", "version", name="uq_skill_versions_identity"
        ),
    )

    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("skill_candidates.id", ondelete="RESTRICT"), index=True
    )
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    environment: Mapped[str] = mapped_column(String(64), index=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    skill_name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(32))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    instructions: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_experience_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    published_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Conversation(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """A user-owned, durable natural-language entry point into OnCall."""

    __tablename__ = "conversations"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(256), default="新对话")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    last_intent: Mapped[str | None] = mapped_column(String(32))
    linked_incident_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), index=True
    )


class ConversationMessage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One bounded conversation turn with the router decision preserved."""

    __tablename__ = "conversation_messages"

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(32), index=True)
    action: Mapped[str | None] = mapped_column(String(32))
    entities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    citations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class EvaluationRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One reproducible offline or online Agent evaluation run."""

    __tablename__ = "evaluation_runs"

    mode: Mapped[str] = mapped_column(String(16), index=True)
    dataset_name: Mapped[str] = mapped_column(String(128))
    dataset_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    case_count: Mapped[int] = mapped_column(Integer, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class EvaluationCaseResult(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Explainable result for a single evaluation case."""

    __tablename__ = "evaluation_case_results"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uq_evaluation_result_run_case"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actual: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class AuditEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_events"

    incident_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(256), index=True)
