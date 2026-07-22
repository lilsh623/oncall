"""Audit trail write operations."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from oncall.models import AuditEvent


async def append_audit_event(
    session: AsyncSession,
    incident_id: UUID | None,
    event_type: str,
    payload: dict[str, Any],
    actor: str,
) -> AuditEvent:
    """Stage an immutable audit event in the caller's transaction."""

    event = AuditEvent(
        incident_id=incident_id,
        event_type=event_type,
        payload=payload,
        actor=actor,
    )
    session.add(event)
    await session.flush()
    return event
