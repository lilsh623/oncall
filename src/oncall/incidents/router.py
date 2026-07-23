"""Incident approval, rejection, and safe investigation-retry routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import PlainTextResponse

from oncall.audit.service import append_audit_event
from oncall.auth.dependencies import CurrentUser, get_db_session, require_roles
from oncall.graph.contracts import ActionPlanDraft
from oncall.incidents.queries import get_incident_detail, incident_events, list_incidents
from oncall.incidents.report import render_incident_report
from oncall.incidents.schemas import (
    AuditEventSummary,
    IncidentDetail,
    IncidentListResponse,
)
from oncall.incidents.state import IncidentStatus, ensure_transition
from oncall.incidents.sse import sse_response
from oncall.jobs.tasks import resume_incident, start_incident
from oncall.models import ActionPlan, Approval, Incident, User
from oncall.policy.engine import evaluate_action_plan


router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])
ApproverUser = Annotated[User, Depends(require_roles("approver", "admin"))]
WorkflowUser = Annotated[User, Depends(require_roles("operator", "approver", "admin"))]
Session = Annotated[AsyncSession, Depends(get_db_session)]


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    comment: str | None = Field(default=None, max_length=4096)


class ApprovalResponse(BaseModel):
    approval_id: UUID
    incident_id: UUID
    action_plan_id: UUID
    decision: str
    status: str


class RetryResponse(BaseModel):
    incident_id: UUID
    status: str
    checkpoint_version: int


@router.get("", response_model=IncidentListResponse)
async def read_incidents(
    session: Session,
    _: CurrentUser,
    incident_status: str | None = Query(default=None, alias="status", max_length=32),
    severity: str | None = Query(default=None, max_length=32),
    service: str | None = Query(default=None, max_length=128),
    opened_after: datetime | None = None,
    opened_before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> IncidentListResponse:
    return await list_incidents(
        session,
        status=incident_status,
        severity=severity,
        service=service,
        opened_after=opened_after,
        opened_before=opened_before,
        limit=limit,
        offset=offset,
    )


@router.get("/{incident_id}", response_model=IncidentDetail)
async def read_incident(incident_id: UUID, session: Session, _: CurrentUser) -> IncidentDetail:
    detail = await get_incident_detail(session, incident_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    return detail


@router.get("/{incident_id}/events", response_model=list[AuditEventSummary])
async def read_incident_events(
    incident_id: UUID,
    session: Session,
    _: CurrentUser,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[AuditEventSummary]:
    if await session.get(Incident, incident_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    return await incident_events(session, incident_id, limit=limit)


@router.get("/{incident_id}/stream")
async def stream_incident_events(
    incident_id: UUID,
    request: Request,
    session: Session,
    _: CurrentUser,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
):
    if await session.get(Incident, incident_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    return sse_response(request, incident_id, last_event_id)


@router.get("/{incident_id}/report", response_class=PlainTextResponse)
async def read_incident_report(
    incident_id: UUID, session: Session, _: CurrentUser
) -> PlainTextResponse:
    detail = await get_incident_detail(session, incident_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    return PlainTextResponse(render_incident_report(detail), media_type="text/markdown")


async def _locked_incident_and_plan(
    session: AsyncSession, incident_id: UUID
) -> tuple[Incident, ActionPlan] | None:
    incident = await session.scalar(
        select(Incident).where(Incident.id == incident_id).with_for_update()
    )
    if incident is None:
        return None
    plan = await session.scalar(
        select(ActionPlan)
        .where(ActionPlan.incident_id == incident_id)
        .order_by(ActionPlan.version.desc(), ActionPlan.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if plan is None:
        return None
    return incident, plan


def _plan_draft(plan: ActionPlan) -> ActionPlanDraft:
    return ActionPlanDraft(
        summary=plan.summary,
        risk_level=plan.risk_level,
        prerequisites=plan.prerequisites,
        rollback=plan.rollback,
        verification_criteria=plan.verification_criteria,
        plan_hash=plan.plan_hash,
    )


@router.post("/{incident_id}/approvals", response_model=ApprovalResponse)
async def approve_incident(
    incident_id: UUID,
    payload: ApprovalRequest,
    session: Session,
    current_user: ApproverUser,
) -> ApprovalResponse:
    """Approve the exact plan hash, commit, then enqueue durable graph resume."""

    locked = await _locked_incident_and_plan(session, incident_id)
    if locked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident or action plan not found")
    incident, plan = locked
    if incident.status != IncidentStatus.WAITING_APPROVAL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="incident is not waiting for approval")
    if plan.plan_hash != payload.action_plan_hash:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="action plan hash changed")
    if plan.status != "PENDING_APPROVAL" or not plan.graph_run_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="action plan is no longer approvable")
    decision = evaluate_action_plan(_plan_draft(plan), incident)
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.message)
    approval = Approval(
        action_plan_id=plan.id,
        action_plan_hash=plan.plan_hash,
        user_id=current_user.id,
        decision="APPROVED",
        comment=payload.comment,
    )
    plan.status = "APPROVED"
    session.add(approval)
    await session.flush()
    await append_audit_event(
        session,
        incident.id,
        "incident.approved",
        {
            "action_plan_id": str(plan.id),
            "action_plan_hash": plan.plan_hash,
            "approval_id": str(approval.id),
        },
        actor=current_user.username,
    )
    # The durable approval decision is committed before any worker receives it.
    await session.commit()
    resume_incident.delay(str(incident.id), str(plan.id))
    return ApprovalResponse(
        approval_id=approval.id,
        incident_id=incident.id,
        action_plan_id=plan.id,
        decision=approval.decision,
        status=incident.status.value,
    )


@router.post("/{incident_id}/rejections", response_model=ApprovalResponse)
async def reject_incident(
    incident_id: UUID,
    payload: ApprovalRequest,
    session: Session,
    current_user: ApproverUser,
) -> ApprovalResponse:
    """Reject a plan without resuming the graph or calling any write tool."""

    locked = await _locked_incident_and_plan(session, incident_id)
    if locked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident or action plan not found")
    incident, plan = locked
    if incident.status != IncidentStatus.WAITING_APPROVAL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="incident is not waiting for approval")
    if plan.plan_hash != payload.action_plan_hash:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="action plan hash changed")
    if plan.status != "PENDING_APPROVAL":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="action plan is no longer rejectable")
    approval = Approval(
        action_plan_id=plan.id,
        action_plan_hash=plan.plan_hash,
        user_id=current_user.id,
        decision="REJECTED",
        comment=payload.comment,
    )
    plan.status = "REJECTED"
    incident.status = ensure_transition(incident.status, IncidentStatus.NEED_HUMAN)
    session.add(approval)
    await session.flush()
    await append_audit_event(
        session,
        incident.id,
        "incident.rejected",
        {"action_plan_id": str(plan.id), "action_plan_hash": plan.plan_hash},
        actor=current_user.username,
    )
    await session.commit()
    return ApprovalResponse(
        approval_id=approval.id,
        incident_id=incident.id,
        action_plan_id=plan.id,
        decision=approval.decision,
        status=incident.status.value,
    )


@router.post("/{incident_id}/retries", response_model=RetryResponse)
async def retry_investigation(
    incident_id: UUID,
    session: Session,
    current_user: WorkflowUser,
) -> RetryResponse:
    """Start a new read-only investigation attempt; it never retries recovery."""

    incident = await session.scalar(
        select(Incident).where(Incident.id == incident_id).with_for_update()
    )
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    if incident.status != IncidentStatus.NEED_HUMAN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only an incident requiring human review can be retried",
        )
    incident.status = ensure_transition(incident.status, IncidentStatus.TRIAGING)
    checkpoint_version = int(datetime.now(timezone.utc).timestamp())
    await append_audit_event(
        session,
        incident.id,
        "incident.investigation_retried",
        {"checkpoint_version": checkpoint_version},
        actor=current_user.username,
    )
    await session.commit()
    start_incident.delay(str(incident.id), checkpoint_version)
    return RetryResponse(
        incident_id=incident.id,
        status=incident.status.value,
        checkpoint_version=checkpoint_version,
    )
