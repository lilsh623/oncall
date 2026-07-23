"""SSE replay and Redis live subscription for Incident audit events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import Request
from redis.asyncio import Redis
from starlette.responses import StreamingResponse

from oncall.audit.service import incident_event_channel
from oncall.config import get_settings
from oncall.database import async_session
from oncall.incidents.queries import incident_event_by_id, incident_events_after
from oncall.incidents.schemas import AuditEventSummary


def event_cursor(event: AuditEventSummary) -> str:
    return f"{event.created_at.isoformat()}|{event.id}"


def parse_cursor(value: str | None) -> tuple[datetime | None, UUID | None]:
    if not value:
        return None, None
    try:
        timestamp_text, event_id = value.rsplit("|", 1)
        return datetime.fromisoformat(timestamp_text), UUID(event_id)
    except (TypeError, ValueError):
        return None, None


def format_sse(event: AuditEventSummary) -> str:
    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, default=str)
    return f"id: {event_cursor(event)}\nevent: {event.event_type}\ndata: {payload}\n\n"


async def _replay(incident_id: UUID, last_event_id: str | None) -> list[AuditEventSummary]:
    created_at, event_id = parse_cursor(last_event_id)
    async with async_session() as session:
        return await incident_events_after(
            session, incident_id, created_at=created_at, event_id=event_id
        )


async def incident_event_stream(
    request: Request, incident_id: UUID, last_event_id: str | None
) -> AsyncIterator[str]:
    """Replay PostgreSQL first, then stream Redis notifications with heartbeats."""

    for event in await _replay(incident_id, last_event_id):
        yield format_sse(event)
    client: Redis | None = None
    pubsub: Any = None
    try:
        client = Redis.from_url(get_settings().redis_url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(incident_event_channel(incident_id))
        while not await request.is_disconnected():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
            if message is None:
                yield ": keep-alive\n\n"
                continue
            try:
                raw = json.loads(str(message["data"]))
                event_id = UUID(str(raw["id"]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            async with async_session() as session:
                event = await incident_event_by_id(session, incident_id, event_id)
            if event is not None:
                yield format_sse(event)
    except Exception:
        # A Redis outage cannot make an Incident unreadable. Durable replay is
        # still available to the client after reconnecting.
        while not await request.is_disconnected():
            await asyncio.sleep(15)
            yield ": redis-unavailable; reconnect for durable replay\n\n"
    finally:
        if pubsub is not None:
            await pubsub.aclose()
        if client is not None:
            await client.aclose()


def sse_response(request: Request, incident_id: UUID, last_event_id: str | None) -> StreamingResponse:
    return StreamingResponse(
        incident_event_stream(request, incident_id, last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
