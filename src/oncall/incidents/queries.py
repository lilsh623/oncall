"""Read-side aggregation for Incident APIs and reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.incidents.schemas import (
    AuditEventSummary,
    IncidentDetail,
    IncidentListItem,
    IncidentListResponse,
)
from oncall.models import (
    ActionPlan,
    ActionStep,
    Alert,
    AlertIntegration,
    Approval,
    AuditEvent,
    CloudProject,
    Evidence,
    Execution,
    Hypothesis,
    Incident,
    IncidentAlert,
    KnowledgeCitation,
    VerificationCheck,
)


def _active_tencent_scope(column_project, column_environment, column_service):
    return exists(
        select(1)
        .select_from(AlertIntegration)
        .join(CloudProject, CloudProject.id == AlertIntegration.cloud_project_id)
        .where(
            AlertIntegration.status == "active",
            AlertIntegration.source.in_(("tencent_monitor", "tencent_cls")),
            CloudProject.status == "active",
            CloudProject.project_id == column_project,
            AlertIntegration.environment == column_environment,
            AlertIntegration.service == column_service,
        )
    )


def _bounded_value(value: Any, *, string_limit: int = 2048) -> Any:
    if isinstance(value, str):
        return value[:string_limit] + ("…" if len(value) > string_limit else "")
    if isinstance(value, list):
        return [_bounded_value(item, string_limit=string_limit) for item in value[:50]]
    if isinstance(value, dict):
        return {
            str(key): _bounded_value(item, string_limit=string_limit)
            for key, item in list(value.items())[:50]
        }
    return value


async def list_incidents(
    session: AsyncSession,
    *,
    status: str | None = None,
    severity: str | None = None,
    service: str | None = None,
    opened_after: datetime | None = None,
    opened_before: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> IncidentListResponse:
    statement = (
        select(Incident)
        .where(_active_tencent_scope(Incident.project_id, Incident.environment, Incident.service))
        .order_by(Incident.opened_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if severity is not None:
        statement = statement.join(IncidentAlert).join(Alert).where(Alert.severity == severity)
    if status is not None:
        statement = statement.where(Incident.status == status)
    if service is not None:
        statement = statement.where(Incident.service == service)
    if opened_after is not None:
        statement = statement.where(Incident.opened_at >= opened_after)
    if opened_before is not None:
        statement = statement.where(Incident.opened_at <= opened_before)
    incidents = (await session.scalars(statement)).unique().all()
    items: list[IncidentListItem] = []
    for incident in incidents:
        current_severity = await session.scalar(
            select(Alert.severity)
            .join(IncidentAlert, IncidentAlert.alert_id == Alert.id)
            .where(IncidentAlert.incident_id == incident.id)
            .order_by(Alert.last_seen.desc())
            .limit(1)
        )
        items.append(
            IncidentListItem(
                id=incident.id,
                title=incident.title,
                project_id=incident.project_id,
                environment=incident.environment,
                service=incident.service,
                status=incident.status.value,
                severity=current_severity,
                opened_at=incident.opened_at,
                updated_at=incident.updated_at,
            )
        )
    return IncidentListResponse(items=items, limit=limit, offset=offset)


def _audit_summary(event: AuditEvent) -> AuditEventSummary:
    return AuditEventSummary(
        id=event.id,
        event_type=event.event_type,
        actor=event.actor,
        created_at=event.created_at,
        payload=_bounded_value(event.payload),
    )


async def incident_events(
    session: AsyncSession,
    incident_id: UUID,
    *,
    limit: int = 200,
) -> list[AuditEventSummary]:
    rows = (
        await session.scalars(
            select(AuditEvent)
            .where(AuditEvent.incident_id == incident_id)
            .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
            .limit(limit)
        )
    ).all()
    return [_audit_summary(row) for row in rows]


async def incident_events_after(
    session: AsyncSession,
    incident_id: UUID,
    *,
    created_at: datetime | None,
    event_id: UUID | None,
    limit: int = 200,
) -> list[AuditEventSummary]:
    statement = select(AuditEvent).where(AuditEvent.incident_id == incident_id)
    if created_at is not None and event_id is not None:
        statement = statement.where(
            or_(
                AuditEvent.created_at > created_at,
                and_(AuditEvent.created_at == created_at, AuditEvent.id > event_id),
            )
        )
    rows = (
        await session.scalars(
            statement.order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc()).limit(limit)
        )
    ).all()
    return [_audit_summary(row) for row in rows]


async def incident_event_by_id(
    session: AsyncSession, incident_id: UUID, event_id: UUID
) -> AuditEventSummary | None:
    event = await session.scalar(
        select(AuditEvent).where(AuditEvent.incident_id == incident_id, AuditEvent.id == event_id)
    )
    return _audit_summary(event) if event is not None else None


async def get_incident_detail(session: AsyncSession, incident_id: UUID) -> IncidentDetail | None:
    incident = await session.scalar(
        select(Incident).where(
            Incident.id == incident_id,
            _active_tencent_scope(Incident.project_id, Incident.environment, Incident.service),
        )
    )
    if incident is None:
        return None
    alerts = (
        await session.execute(
            select(Alert)
            .join(IncidentAlert, IncidentAlert.alert_id == Alert.id)
            .where(IncidentAlert.incident_id == incident_id)
            .order_by(Alert.last_seen.desc())
        )
    ).scalars().all()
    evidence = (
        await session.scalars(
            select(Evidence).where(Evidence.incident_id == incident_id).order_by(Evidence.collected_at.asc())
        )
    ).all()
    hypotheses = (
        await session.scalars(
            select(Hypothesis).where(Hypothesis.incident_id == incident_id).order_by(Hypothesis.updated_at.asc())
        )
    ).all()
    citations = (
        await session.scalars(
            select(KnowledgeCitation)
            .where(KnowledgeCitation.incident_id == incident_id)
            .order_by(KnowledgeCitation.created_at.asc())
        )
    ).all()
    plans = (
        await session.scalars(
            select(ActionPlan).where(ActionPlan.incident_id == incident_id).order_by(ActionPlan.version.asc())
        )
    ).all()
    plan_ids = [plan.id for plan in plans]
    steps = (
        await session.scalars(
            select(ActionStep).where(ActionStep.action_plan_id.in_(plan_ids)).order_by(ActionStep.sequence.asc())
        )
    ).all() if plan_ids else []
    approvals = (
        await session.scalars(
            select(Approval).where(Approval.action_plan_id.in_(plan_ids)).order_by(Approval.decided_at.asc())
        )
    ).all() if plan_ids else []
    executions = (
        await session.scalars(
            select(Execution).where(Execution.action_plan_id.in_(plan_ids)).order_by(Execution.started_at.asc())
        )
    ).all() if plan_ids else []
    execution_ids = [execution.id for execution in executions]
    verification = (
        await session.scalars(
            select(VerificationCheck)
            .where(VerificationCheck.incident_id == incident_id)
            .order_by(VerificationCheck.checked_at.asc())
        )
    ).all()
    events = await incident_events(session, incident_id)
    steps_by_plan: dict[UUID, list[dict[str, Any]]] = {}
    for step in steps:
        steps_by_plan.setdefault(step.action_plan_id, []).append(
            {
                "sequence": step.sequence,
                "name": step.name,
                "tool_name": step.tool_name,
                "risk_level": step.risk_level,
                "expected_result": step.expected_result,
            }
        )
    return IncidentDetail(
        overview={
            "id": str(incident.id),
            "title": incident.title,
            "status": incident.status.value,
            "project_id": incident.project_id,
            "environment": incident.environment,
            "service": incident.service,
            "summary": incident.summary,
            "opened_at": incident.opened_at,
            "resolved_at": incident.resolved_at,
            "updated_at": incident.updated_at,
        },
        alerts=[
            {
                "id": str(row.id),
                "alert_name": row.alert_name,
                "severity": row.severity,
                "status": row.status,
                "summary": row.annotations.get("summary"),
                "starts_at": row.starts_at,
                "last_seen": row.last_seen,
            }
            for row in alerts
        ],
        # Evidence payloads may contain bounded log lines, so they are never
        # returned by the public API. The observation is the safe summary.
        evidence=[
            {
                "id": str(row.id),
                "source_type": row.source_type,
                "source_ref": row.source_ref,
                "observation": row.observation,
                "collected_at": row.collected_at,
            }
            for row in evidence
        ],
        hypotheses=[
            {
                "id": str(row.id),
                "description": row.description,
                "confidence": row.confidence,
                "supporting_summary": row.supporting_summary,
                "opposing_summary": row.opposing_summary,
                "next_check": row.next_check,
                "status": row.status,
            }
            for row in hypotheses
        ],
        knowledge_citations=[
            {
                "document_id": row.document_id,
                "document_version": row.document_version,
                "section": row.section,
                "file_path": row.file_path,
                "locator": row.locator,
                "excerpt": row.excerpt,
                "score": row.score,
            }
            for row in citations
        ],
        action_plans=[
            {
                "id": str(plan.id),
                "version": plan.version,
                "summary": plan.summary,
                "risk_level": plan.risk_level,
                "prerequisites": _bounded_value(plan.prerequisites),
                "rollback": _bounded_value(plan.rollback),
                "verification_criteria": _bounded_value(plan.verification_criteria),
                "plan_hash": plan.plan_hash,
                "status": plan.status,
                "steps": steps_by_plan.get(plan.id, []),
            }
            for plan in plans
        ],
        approvals=[
            {
                "id": str(row.id),
                "action_plan_id": str(row.action_plan_id),
                "action_plan_hash": row.action_plan_hash,
                "decision": row.decision,
                "comment": row.comment,
                "decided_at": row.decided_at,
            }
            for row in approvals
        ],
        executions=[
            {
                "id": str(row.id),
                "action_plan_id": str(row.action_plan_id),
                "status": row.status,
                "tool_name": row.tool_name,
                "started_at": row.started_at,
                "completed_at": row.completed_at,
                "error": row.error,
            }
            for row in executions
        ],
        verification=[
            {
                "id": str(row.id),
                "execution_id": str(row.execution_id) if row.execution_id else None,
                "name": row.name,
                "status": row.status,
                "conclusion": row.conclusion,
                "reason": row.reason,
                "checked_at": row.checked_at,
            }
            for row in verification
        ],
        audit_events=events,
    )
