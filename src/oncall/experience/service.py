"""Eligibility, extraction, de-duplication, review, and publication services."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.audit.service import append_audit_event
from oncall.alerts.correlation import advisory_lock_key
from oncall.experience.extraction import (
    REDACTION_VERSION,
    candidate_document,
    canonical_hash,
    pattern_fingerprint,
)
from oncall.incidents.state import IncidentStatus
from oncall.models import (
    ActionPlan,
    Evidence,
    Execution,
    Experience,
    ExperienceCandidate,
    Hypothesis,
    Incident,
    KnowledgeCitation,
    User,
    VerificationCheck,
)


class ExperienceNotEligible(ValueError):
    """The source Incident has not met the deterministic publication prerequisites."""


async def create_candidate_from_resolved_incident(
    session: AsyncSession, incident_id: UUID
) -> ExperienceCandidate | None:
    """Idempotently extract one grounded candidate inside the resolution transaction."""

    incident = await session.scalar(
        select(Incident).where(Incident.id == incident_id).with_for_update()
    )
    if incident is None or incident.status != IncidentStatus.RESOLVED:
        raise ExperienceNotEligible("only a resolved Incident can produce experience")
    existing = await session.scalar(
        select(ExperienceCandidate).where(ExperienceCandidate.incident_id == incident_id)
    )
    if existing is not None:
        if existing.status == "PENDING_REVIEW":
            existing.content_hash = canonical_hash(_published_content(existing))
        return None

    plan = await session.scalar(
        select(ActionPlan)
        .where(ActionPlan.incident_id == incident_id, ActionPlan.status == "EXECUTED")
        .order_by(ActionPlan.version.desc())
        .limit(1)
    )
    if plan is None:
        raise ExperienceNotEligible("an executed action plan is required")
    execution = await session.scalar(
        select(Execution)
        .where(Execution.action_plan_id == plan.id, Execution.status == "SUCCEEDED")
        .order_by(Execution.completed_at.desc())
        .limit(1)
    )
    if execution is None:
        raise ExperienceNotEligible("a successful execution is required")

    checks = list(
        (
            await session.scalars(
                select(VerificationCheck)
                .where(VerificationCheck.incident_id == incident_id)
                .order_by(VerificationCheck.checked_at.asc(), VerificationCheck.id.asc())
            )
        ).all()
    )
    if not checks or any(check.conclusion != "PASSED" for check in checks):
        raise ExperienceNotEligible("all persisted verification checks must pass")
    evidence = list(
        (
            await session.scalars(
                select(Evidence)
                .where(Evidence.incident_id == incident_id)
                .order_by(Evidence.collected_at.asc())
            )
        ).all()
    )
    citations = list(
        (
            await session.scalars(
                select(KnowledgeCitation)
                .where(KnowledgeCitation.incident_id == incident_id)
                .order_by(KnowledgeCitation.created_at.asc())
            )
        ).all()
    )
    if not evidence or not citations:
        raise ExperienceNotEligible("grounded evidence and an approved knowledge citation are required")
    hypothesis = await session.scalar(
        select(Hypothesis)
        .where(Hypothesis.incident_id == incident_id)
        .order_by(Hypothesis.confidence.desc().nullslast(), Hypothesis.updated_at.desc())
        .limit(1)
    )
    if hypothesis is None:
        raise ExperienceNotEligible("a persisted root-cause hypothesis is required")

    symptoms = [item.observation for item in evidence[:10] if item.observation.strip()]
    verification = [
        {
            "name": check.name,
            "status": check.status,
            "observed_value": check.observed_value,
            "conclusion": check.conclusion,
        }
        for check in checks[:20]
    ]
    action = {
        "action_type": str(plan.rollback.get("action_type", "rollback_release")),
        "rollback": plan.rollback,
    }
    root_cause = hypothesis.description
    document = candidate_document(
        title=f"{incident.service}: verified recovery experience",
        summary=incident.summary or incident.title,
        symptoms=symptoms,
        root_cause=root_cause,
        action=action,
        verification=verification,
        warnings=[
            "Re-check the current release before reusing this action.",
            "Human approval and deterministic policy evaluation remain mandatory.",
        ],
    )
    fingerprint = pattern_fingerprint(
        project_id=incident.project_id,
        environment=incident.environment,
        service=incident.service,
        root_cause=str(document["root_cause"]),
        action_type=str(document["action"]["action_type"]),
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": advisory_lock_key("experience-pattern", fingerprint)},
    )
    duplicate = await session.scalar(
        select(ExperienceCandidate)
        .where(
            ExperienceCandidate.pattern_fingerprint == fingerprint,
            ExperienceCandidate.status.in_(("PENDING_REVIEW", "PUBLISHED")),
        )
        .order_by(ExperienceCandidate.created_at.asc())
        .limit(1)
    )
    source_refs = {
        "incident_ids": [str(incident.id)],
        "evidence_ids": [str(item.id) for item in evidence],
        "knowledge_citation_ids": [str(item.id) for item in citations],
        "action_plan_ids": [str(plan.id)],
        "execution_ids": [str(execution.id)],
        "verification_check_ids": [str(item.id) for item in checks],
    }
    confidence = float(hypothesis.confidence or 0.0)
    publishable_document = {
        **document,
        "provenance": source_refs,
        "confidence": confidence,
        "redaction_version": REDACTION_VERSION,
    }
    candidate = ExperienceCandidate(
        incident_id=incident.id,
        project_id=incident.project_id,
        environment=incident.environment,
        service=incident.service,
        pattern_fingerprint=fingerprint,
        content_hash=canonical_hash(publishable_document),
        title=str(document["title"]),
        summary=str(document["summary"]),
        symptoms=list(document["symptoms"]),
        root_cause=str(document["root_cause"]),
        action=dict(document["action"]),
        verification=list(document["verification"]),
        warnings=list(document["warnings"]),
        source_refs=source_refs,
        confidence=confidence,
        redaction_version=REDACTION_VERSION,
        status="PENDING_REVIEW",
        duplicate_of_id=duplicate.id if duplicate is not None else None,
    )
    session.add(candidate)
    await session.flush()
    await append_audit_event(
        session,
        incident.id,
        "experience.candidate_created",
        {
            "candidate_id": str(candidate.id),
            "content_hash": candidate.content_hash,
            "duplicate_of_id": str(candidate.duplicate_of_id) if candidate.duplicate_of_id else None,
        },
        actor="system:experience_extractor",
    )
    return candidate


def _published_content(candidate: ExperienceCandidate) -> dict[str, Any]:
    return {
        "title": candidate.title,
        "summary": candidate.summary,
        "symptoms": candidate.symptoms,
        "root_cause": candidate.root_cause,
        "action": candidate.action,
        "verification": candidate.verification,
        "warnings": candidate.warnings,
        "provenance": candidate.source_refs,
        "confidence": candidate.confidence,
        "redaction_version": candidate.redaction_version,
    }


async def publish_candidate(
    session: AsyncSession,
    candidate: ExperienceCandidate,
    actor: User,
    *,
    comment: str | None,
) -> Experience:
    """Publish exactly the reviewed content hash; duplicate patterns require merging first."""

    existing = await session.scalar(
        select(Experience).where(Experience.candidate_id == candidate.id)
    )
    if existing is not None:
        return existing
    if candidate.status != "PENDING_REVIEW":
        raise ValueError("candidate is no longer pending review")
    if candidate.duplicate_of_id is not None:
        raise ValueError("duplicate candidates cannot be published; reject this candidate")
    expected_hash = canonical_hash(_published_content(candidate))
    if candidate.content_hash != expected_hash:
        candidate.content_hash = expected_hash
    candidate.status = "PUBLISHED"
    candidate.reviewed_by = actor.id
    candidate.review_comment = comment
    candidate.reviewed_at = datetime.now(UTC)
    experience = Experience(
        candidate_id=candidate.id,
        project_id=candidate.project_id,
        environment=candidate.environment,
        service=candidate.service,
        pattern_fingerprint=candidate.pattern_fingerprint,
        content_hash=candidate.content_hash,
        version=1,
        title=candidate.title,
        content=_published_content(candidate),
        source_incident_ids=[str(candidate.incident_id)],
        published_by=actor.id,
        published_at=datetime.now(UTC),
        status="PUBLISHED",
    )
    session.add(experience)
    await session.flush()
    await append_audit_event(
        session,
        candidate.incident_id,
        "experience.published",
        {
            "candidate_id": str(candidate.id),
            "experience_id": str(experience.id),
            "content_hash": candidate.content_hash,
        },
        actor=actor.username,
    )
    return experience


async def reject_candidate(
    session: AsyncSession,
    candidate: ExperienceCandidate,
    actor: User,
    *,
    comment: str | None,
) -> ExperienceCandidate:
    if candidate.status != "PENDING_REVIEW":
        raise ValueError("candidate is no longer pending review")
    candidate.status = "REJECTED"
    candidate.reviewed_by = actor.id
    candidate.review_comment = comment
    candidate.reviewed_at = datetime.now(UTC)
    await session.flush()
    await append_audit_event(
        session,
        candidate.incident_id,
        "experience.rejected",
        {"candidate_id": str(candidate.id), "comment": comment},
        actor=actor.username,
    )
    return candidate
