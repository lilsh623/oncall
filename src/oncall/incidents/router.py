"""Incident mutation routes for approval workflow."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.audit.service import append_audit_event
from oncall.auth.dependencies import get_db_session, require_roles
from oncall.graph.contracts import ActionPlanDraft
from oncall.incidents.state import IncidentStatus, ensure_transition
from oncall.models import ActionPlan, Approval, Incident
from oncall.policy.engine import evaluate_action_plan


router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])
ApproverUser = Annotated[object, Depends(require_roles("approver", "admin"))]
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


async def _latest_plan(session: AsyncSession, incident_id: UUID) -> ActionPlan | None:
    result = await session.execute(
        select(ActionPlan)
        .where(ActionPlan.incident_id == incident_id)
        .order_by(ActionPlan.version.desc(), ActionPlan.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.post("/{incident_id}/approvals", response_model=ApprovalResponse)
async def approve_incident(
    incident_id: UUID,
    payload: ApprovalRequest,
    session: Session,
    current_user: ApproverUser,
) -> ApprovalResponse:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    if incident.status != IncidentStatus.WAITING_APPROVAL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="incident is not waiting for approval")
    plan = await _latest_plan(session, incident_id)
    if plan is None or plan.plan_hash != payload.action_plan_hash:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="action plan hash changed")
    decision = evaluate_action_plan(
        ActionPlanDraft(
            summary=plan.summary,
            risk_level=plan.risk_level,
            prerequisites=plan.prerequisites,
            rollback=plan.rollback,
            verification_criteria=plan.verification_criteria,
            plan_hash=plan.plan_hash,
        ),
        incident,
    )
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
    incident.status = ensure_transition(incident.status, IncidentStatus.EXECUTING)
    session.add(approval)
    await session.flush()
    await append_audit_event(
        session,
        incident.id,
        "incident.approved",
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


@router.post("/{incident_id}/rejections", response_model=ApprovalResponse)
async def reject_incident(
    incident_id: UUID,
    payload: ApprovalRequest,
    session: Session,
    current_user: ApproverUser,
) -> ApprovalResponse:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="incident not found")
    plan = await _latest_plan(session, incident_id)
    if plan is None or plan.plan_hash != payload.action_plan_hash:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="action plan hash changed")
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
