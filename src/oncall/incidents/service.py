"""Persistence operations for deterministic Incident aggregation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.alerts.correlation import INCIDENT_CORRELATION_WINDOW, advisory_lock_key
from oncall.audit.service import append_audit_event
from oncall.incidents.state import IncidentStatus
from oncall.models import Alert, Incident, IncidentAlert


OPEN_INCIDENT_STATUSES = tuple(
    status
    for status in IncidentStatus
    if status not in {IncidentStatus.RESOLVED, IncidentStatus.FAILED}
)


async def _lock_correlation_group(session: AsyncSession, alert: Alert) -> None:
    lock_key = advisory_lock_key(
        "incident-correlation",
        alert.project_id,
        alert.environment,
        alert.service,
        alert.correlation_key or "",
    )
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})


async def find_linked_open_incident(
    session: AsyncSession, alert_id: UUID
) -> Incident | None:
    """Return the current open Incident already owning this alert identity."""

    result = await session.execute(
        select(Incident)
        .join(IncidentAlert, IncidentAlert.incident_id == Incident.id)
        .where(
            IncidentAlert.alert_id == alert_id,
            Incident.status.in_(OPEN_INCIDENT_STATUSES),
        )
        .order_by(Incident.opened_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_or_create_incident(
    session: AsyncSession,
    alert: Alert,
    received_at: datetime,
) -> tuple[Incident, bool]:
    """Aggregate into a matching five-minute open Incident, or create one."""

    existing_link = await find_linked_open_incident(session, alert.id)
    if existing_link is not None:
        return existing_link, False

    await _lock_correlation_group(session, alert)
    window_start = received_at - INCIDENT_CORRELATION_WINDOW
    result = await session.execute(
        select(Incident)
        .where(
            Incident.project_id == alert.project_id,
            Incident.environment == alert.environment,
            Incident.service == alert.service,
            Incident.correlation_key == alert.correlation_key,
            Incident.status.in_(OPEN_INCIDENT_STATUSES),
            Incident.opened_at >= window_start,
            Incident.opened_at <= received_at,
        )
        .order_by(Incident.opened_at.desc())
        .limit(1)
    )
    incident = result.scalar_one_or_none()
    if incident is not None:
        return incident, False

    incident = Incident(
        title=f"[{alert.severity}] {alert.alert_name} on {alert.service}",
        status=IncidentStatus.RECEIVED,
        project_id=alert.project_id,
        environment=alert.environment,
        service=alert.service,
        correlation_key=alert.correlation_key,
        summary=alert.annotations.get("summary"),
        opened_at=received_at,
    )
    session.add(incident)
    await session.flush()
    await append_audit_event(
        session,
        incident.id,
        "incident.created",
        {
            "alert_id": str(alert.id),
            "correlation_key": alert.correlation_key,
            "status": IncidentStatus.RECEIVED.value,
        },
        actor="alert-hub",
    )
    return incident, True


async def link_alert_to_incident(
    session: AsyncSession, incident: Incident, alert: Alert
) -> bool:
    """Idempotently link an alert and audit only a newly created relation."""

    statement = (
        postgres_insert(IncidentAlert)
        .values(incident_id=incident.id, alert_id=alert.id)
        .on_conflict_do_nothing(constraint="uq_incident_alerts_incident_alert")
        .returning(IncidentAlert.id)
    )
    linked_id = (await session.execute(statement)).scalar_one_or_none()
    if linked_id is None:
        return False
    await append_audit_event(
        session,
        incident.id,
        "incident.alert_linked",
        {
            "alert_id": str(alert.id),
            "fingerprint": alert.fingerprint,
            "status": alert.status,
        },
        actor="alert-hub",
    )
    return True
