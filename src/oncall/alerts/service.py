"""Transactional Alert Hub ingestion service."""

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.alerts.adapters.alertmanager import normalize_alertmanager
from oncall.alerts.correlation import advisory_lock_key
from oncall.alerts.schemas import AlertEnvelope, AlertIngestionCommand, IngestionResult
from oncall.audit.service import append_audit_event
from oncall.incidents.service import (
    find_linked_open_incident,
    get_or_create_incident,
    link_alert_to_incident,
)
from oncall.models import Alert, RawAlertEvent


class AlertIdentityConflict(ValueError):
    """Raised when one source fingerprint is reused for different core facts."""


async def _lock_alert_identity(session: AsyncSession, source: str, fingerprint: str) -> None:
    lock_key = advisory_lock_key("alert-identity", source, fingerprint)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})


async def _upsert_alert(
    session: AsyncSession,
    raw_event: RawAlertEvent,
    envelope: AlertEnvelope,
    received_at: datetime,
) -> tuple[Alert, bool, str | None]:
    """Serialize one identity and return alert, was-duplicate and previous status."""

    await _lock_alert_identity(session, envelope.source, envelope.fingerprint)
    result = await session.execute(
        select(Alert).where(
            Alert.source == envelope.source,
            Alert.fingerprint == envelope.fingerprint,
        )
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        alert = Alert(
            raw_event_id=raw_event.id,
            source=envelope.source,
            fingerprint=envelope.fingerprint,
            project_id=envelope.project_id,
            environment=envelope.environment,
            service=envelope.service,
            alert_name=envelope.alert_name,
            severity=envelope.severity,
            status=envelope.status,
            correlation_key=envelope.correlation_key,
            labels=envelope.labels,
            annotations=envelope.annotations,
            starts_at=envelope.starts_at,
            ends_at=envelope.ends_at,
            last_seen=received_at,
            occurrence_count=1,
        )
        session.add(alert)
        await session.flush()
        return alert, False, None

    identity = (alert.project_id, alert.environment, alert.service, alert.alert_name)
    incoming_identity = (
        envelope.project_id,
        envelope.environment,
        envelope.service,
        envelope.alert_name,
    )
    if identity != incoming_identity:
        raise AlertIdentityConflict("source fingerprint conflicts with an existing alert")

    previous_status = alert.status
    alert.raw_event_id = raw_event.id
    alert.project_id = envelope.project_id
    alert.environment = envelope.environment
    alert.service = envelope.service
    alert.alert_name = envelope.alert_name
    alert.severity = envelope.severity
    alert.status = envelope.status
    alert.correlation_key = envelope.correlation_key
    alert.labels = envelope.labels
    alert.annotations = envelope.annotations
    alert.starts_at = envelope.starts_at
    alert.ends_at = envelope.ends_at
    alert.last_seen = received_at
    alert.occurrence_count += 1
    await session.flush()
    return alert, True, previous_status


async def ingest_alerts(
    session: AsyncSession, raw_payload: AlertIngestionCommand
) -> IngestionResult:
    """Persist a raw webhook, de-duplicate alerts and correlate open Incidents."""

    received_at = datetime.now(UTC)
    envelopes = normalize_alertmanager(
        raw_payload.payload,
        raw_payload.project_id,
        raw_payload.stable_label_names,
    )
    raw_event = RawAlertEvent(
        source="alertmanager",
        project_id=raw_payload.project_id,
        payload=raw_payload.payload,
        received_at=received_at,
    )
    session.add(raw_event)
    await session.flush()

    incident_ids = set()
    deduplicated = 0
    for envelope in sorted(
        envelopes,
        key=lambda item: (
            item.project_id,
            item.environment,
            item.service,
            item.correlation_key,
            item.fingerprint,
        ),
    ):
        alert, duplicate, previous_status = await _upsert_alert(
            session, raw_event, envelope, received_at
        )
        deduplicated += int(duplicate)

        incident = await find_linked_open_incident(session, alert.id)
        if envelope.status == "firing" and incident is None:
            incident, _ = await get_or_create_incident(session, alert, received_at)
            await link_alert_to_incident(session, incident, alert)

        if incident is not None:
            incident_ids.add(incident.id)
            if previous_status is not None and previous_status != envelope.status:
                await append_audit_event(
                    session,
                    incident.id,
                    "alert.status_changed",
                    {
                        "alert_id": str(alert.id),
                        "from": previous_status,
                        "to": envelope.status,
                    },
                    actor="alert-hub",
                )

    return IngestionResult(
        incident_ids=sorted(incident_ids, key=str),
        deduplicated=deduplicated,
    )
