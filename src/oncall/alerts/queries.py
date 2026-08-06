"""Read-side queries for the authenticated alert inbox."""

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.alerts.schemas import AlertListItem, AlertListResponse
from oncall.models import Alert, AlertIntegration, CloudProject, IncidentAlert


def _active_tencent_scope(column_project, column_environment, column_service):
    """Match only alerts belonging to an active Tencent Cloud integration."""

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


async def list_alerts(
    session: AsyncSession,
    *,
    status: str | None = None,
    severity: str | None = None,
    service: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AlertListResponse:
    """Return normalized alerts without exposing raw vendor payloads."""

    incident_id = (
        select(IncidentAlert.incident_id)
        .where(IncidentAlert.alert_id == Alert.id)
        .order_by(IncidentAlert.created_at.desc())
        .limit(1)
        .correlate(Alert)
        .scalar_subquery()
    )
    filters = []
    if status is not None:
        filters.append(Alert.status == status)
    if severity is not None:
        filters.append(Alert.severity == severity)
    if service is not None:
        filters.append(Alert.service == service)
    if project_id is not None:
        filters.append(Alert.project_id == project_id)
    filters.extend(
        [
            Alert.source.in_(("tencent_monitor", "tencent_cls")),
            _active_tencent_scope(Alert.project_id, Alert.environment, Alert.service),
        ]
    )

    total = int(
        await session.scalar(select(func.count(Alert.id)).where(*filters)) or 0
    )
    rows = (
        await session.execute(
            select(Alert, incident_id.label("incident_id"))
            .where(*filters)
            .order_by(Alert.last_seen.desc(), Alert.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = [
        AlertListItem(
            id=alert.id,
            incident_id=linked_incident_id,
            source=alert.source,
            project_id=alert.project_id,
            environment=alert.environment,
            service=alert.service,
            alert_name=alert.alert_name,
            severity=alert.severity,
            status=alert.status,
            summary=alert.annotations.get("summary"),
            starts_at=alert.starts_at,
            ends_at=alert.ends_at,
            last_seen=alert.last_seen,
            occurrence_count=alert.occurrence_count,
        )
        for alert, linked_incident_id in rows
    ]
    return AlertListResponse(items=items, total=total, limit=limit, offset=offset)
