"""Durable verification step used by the outer Incident LangGraph."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.audit.service import append_audit_event
from oncall.database import async_session
from oncall.execution.verification import verify_recovery
from oncall.incidents.state import IncidentStatus, ensure_transition
from oncall.models import ActionPlan, Execution, Incident, VerificationCheck


class RecoveryVerificationOutcome(BaseModel):
    """Graph-safe result for one persisted recovery verification."""

    model_config = ConfigDict(extra="forbid")

    status: str
    passed: bool
    checks: list[dict[str, object]] = Field(default_factory=list)
    reason: str | None = None


async def _persisted_checks(
    session: AsyncSession, execution_id: UUID
) -> list[VerificationCheck]:
    result = await session.execute(
        select(VerificationCheck)
        .where(VerificationCheck.execution_id == execution_id)
        .order_by(VerificationCheck.created_at.asc())
    )
    return list(result.scalars())


def _checks_payload(checks: list[VerificationCheck]) -> list[dict[str, object]]:
    return [
        {
            "name": item.name,
            "status": item.status.lower(),
            **(item.observed_value or {}),
        }
        for item in checks
    ]


async def verify_executed_plan(
    incident_id: UUID,
    plan_id: UUID,
    execution_id: UUID,
) -> RecoveryVerificationOutcome:
    """Verify one successful execution and atomically persist its terminal state.

    Existing verification rows are treated as the durable idempotency boundary,
    so a checkpoint retry cannot execute the read-only verification twice after
    its result has already been committed.
    """

    async with async_session() as session:
        incident = await session.scalar(
            select(Incident).where(Incident.id == incident_id).with_for_update()
        )
        plan = await session.scalar(
            select(ActionPlan)
            .where(ActionPlan.id == plan_id, ActionPlan.incident_id == incident_id)
            .with_for_update()
        )
        execution = await session.scalar(
            select(Execution)
            .where(
                Execution.id == execution_id,
                Execution.action_plan_id == plan_id,
            )
            .with_for_update()
        )
        if incident is None or plan is None or execution is None:
            return RecoveryVerificationOutcome(
                status=IncidentStatus.NEED_HUMAN.value,
                passed=False,
                reason="The executed incident plan could not be loaded for verification.",
            )
        if execution.status != "SUCCEEDED":
            return RecoveryVerificationOutcome(
                status=IncidentStatus.NEED_HUMAN.value,
                passed=False,
                reason=f"Execution ended as {execution.status}; verification was not started.",
            )

        existing = await _persisted_checks(session, execution_id)
        if existing:
            passed = (
                incident.status == IncidentStatus.RESOLVED
                and all(item.conclusion == "PASSED" for item in existing)
            )
            return RecoveryVerificationOutcome(
                status=incident.status.value,
                passed=passed,
                checks=_checks_payload(existing),
                reason=existing[-1].reason,
            )

        if incident.status == IncidentStatus.EXECUTING:
            previous = incident.status
            incident.status = ensure_transition(previous, IncidentStatus.VERIFYING)
            await append_audit_event(
                session,
                incident.id,
                "incident.recovery_executed",
                {
                    "from": previous.value,
                    "to": incident.status.value,
                    "execution_id": str(execution.id),
                    "action_plan_id": str(plan.id),
                },
                actor="langgraph:verify_recovery",
            )
        elif incident.status != IncidentStatus.VERIFYING:
            return RecoveryVerificationOutcome(
                status=IncidentStatus.NEED_HUMAN.value,
                passed=False,
                reason=(
                    "Recovery verification cannot start from incident status "
                    f"{incident.status.value}."
                ),
            )
        criteria = list(plan.verification_criteria)
        await session.commit()

    verification = await verify_recovery(criteria, incident=incident)

    async with async_session() as session:
        incident = await session.scalar(
            select(Incident).where(Incident.id == incident_id).with_for_update()
        )
        plan = await session.scalar(
            select(ActionPlan)
            .where(ActionPlan.id == plan_id, ActionPlan.incident_id == incident_id)
            .with_for_update()
        )
        execution = await session.get(Execution, execution_id)
        if incident is None or plan is None or execution is None:
            return RecoveryVerificationOutcome(
                status=IncidentStatus.NEED_HUMAN.value,
                passed=False,
                checks=verification.checks,
                reason="Verification completed but its durable records are unavailable.",
            )

        existing = await _persisted_checks(session, execution_id)
        if existing:
            passed = (
                incident.status == IncidentStatus.RESOLVED
                and all(item.conclusion == "PASSED" for item in existing)
            )
            return RecoveryVerificationOutcome(
                status=incident.status.value,
                passed=passed,
                checks=_checks_payload(existing),
                reason=existing[-1].reason,
            )

        for check in verification.checks:
            session.add(
                VerificationCheck(
                    incident_id=incident.id,
                    execution_id=execution.id,
                    name=str(
                        check.get("name")
                        or check.get("criterion")
                        or "verification"
                    ),
                    status=str(check.get("status", "unknown")).upper(),
                    observed_value={
                        key: value
                        for key, value in check.items()
                        if key not in {"name", "status", "criterion"}
                    },
                    conclusion="PASSED" if verification.passed else "FAILED",
                    reason=verification.reason,
                    checked_at=datetime.now(UTC),
                )
            )

        previous = incident.status
        if verification.passed and previous == IncidentStatus.VERIFYING:
            incident.status = ensure_transition(previous, IncidentStatus.RESOLVED)
            incident.resolved_at = datetime.now(UTC)
            plan.status = "EXECUTED"
            event_type = "incident.resolved"
        elif previous == IncidentStatus.VERIFYING:
            incident.status = ensure_transition(previous, IncidentStatus.NEED_HUMAN)
            event_type = "incident.verification_failed"
        else:
            event_type = "incident.verification_recorded"

        await append_audit_event(
            session,
            incident.id,
            event_type,
            {
                "from": previous.value,
                "to": incident.status.value,
                "execution_id": str(execution.id),
                "passed": verification.passed,
                "reason": verification.reason,
            },
            actor="langgraph:verify_recovery",
        )
        final_status = incident.status.value
        await session.commit()

    return RecoveryVerificationOutcome(
        status=final_status,
        passed=verification.passed,
        checks=verification.checks,
        reason=verification.reason,
    )
