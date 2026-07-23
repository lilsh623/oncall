"""Audit trail write operations and best-effort Redis event fan-out."""

import asyncio
import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from oncall.models import AuditEvent


def incident_event_channel(incident_id: UUID) -> str:
    """Return the fixed Redis channel for one Incident's live event stream."""

    return f"oncall:incidents:{incident_id}:events"


async def _publish_event(event: AuditEvent) -> None:
    """Fan out a persisted-event notification without making audit writes depend on Redis."""

    if event.incident_id is None:
        return
    try:
        from redis.asyncio import Redis

        from oncall.config import get_settings

        client = Redis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            await client.publish(
                incident_event_channel(event.incident_id),
                json.dumps(
                    {
                        "id": str(event.id),
                        "created_at": event.created_at.isoformat(),
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "actor": event.actor,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
        finally:
            await client.aclose()
    except Exception:
        # Database audit history is authoritative; a missed notification is
        # repaired by SSE's PostgreSQL replay on the next client connection.
        return


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
    # The notification is deliberately best-effort. It may arrive before the
    # caller commits; clients always reconcile through durable replay.
    asyncio.create_task(_publish_event(event))
    return event
