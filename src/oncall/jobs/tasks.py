"""Celery tasks that start or resume deterministic Incident processing."""

import asyncio
from uuid import UUID

from sqlalchemy import select

from oncall.audit.service import append_audit_event
from oncall.database import async_session
from oncall.incidents.state import IncidentStatus, ensure_transition
from oncall.jobs.celery_app import celery_app
from oncall.models import Incident


async def _start_incident(incident_id: UUID, checkpoint_version: int) -> dict[str, str | int]:
    async with async_session() as session:
        result = await session.execute(
            select(Incident).where(Incident.id == incident_id).with_for_update()
        )
        incident = result.scalar_one_or_none()
        if incident is None:
            return {
                "incident_id": str(incident_id),
                "checkpoint_version": checkpoint_version,
                "status": "NOT_FOUND",
            }

        if incident.status == IncidentStatus.RECEIVED:
            target = ensure_transition(incident.status, IncidentStatus.TRIAGING)
            previous = incident.status
            incident.status = target
            await append_audit_event(
                session,
                incident.id,
                "incident.status_changed",
                {
                    "from": previous.value,
                    "to": target.value,
                    "checkpoint_version": checkpoint_version,
                },
                actor="celery:start_incident",
            )
            await session.commit()
        status_value = (
            incident.status.value
            if isinstance(incident.status, IncidentStatus)
            else str(incident.status)
        )
        return {
            "incident_id": str(incident_id),
            "checkpoint_version": checkpoint_version,
            "status": status_value,
        }


@celery_app.task(name="oncall.start_incident")
def start_incident(incident_id: str, checkpoint_version: int) -> dict[str, str | int]:
    """Idempotently advance RECEIVED to TRIAGING; Task 8 will start the Graph."""

    if checkpoint_version < 1:
        raise ValueError("checkpoint_version must be positive")
    return asyncio.run(_start_incident(UUID(incident_id), checkpoint_version))
