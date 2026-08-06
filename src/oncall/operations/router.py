"""Authenticated operational overview for the web console."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.auth.dependencies import CurrentUser, get_db_session
from oncall.incidents.state import IncidentStatus
from oncall.models import Alert, AlertIntegration, CloudProject, Incident, RawAlertEvent
from oncall.operations.schemas import AlertIntegrationSummary, OperationsOverview


router = APIRouter(prefix="/api/v1/operations", tags=["operations"])
Session = Annotated[AsyncSession, Depends(get_db_session)]

PROCESSING_STATUSES = (
    IncidentStatus.RECEIVED,
    IncidentStatus.TRIAGING,
    IncidentStatus.INVESTIGATING,
    IncidentStatus.DIAGNOSED,
    IncidentStatus.PLANNING,
    IncidentStatus.EXECUTING,
    IncidentStatus.VERIFYING,
)
OPEN_STATUSES = PROCESSING_STATUSES + (
    IncidentStatus.WAITING_APPROVAL,
    IncidentStatus.NEED_HUMAN,
)


def _active_tencent_scope(column_project, column_environment=None, column_service=None):
    statement = (
        select(1)
        .select_from(AlertIntegration)
        .join(CloudProject, CloudProject.id == AlertIntegration.cloud_project_id)
        .where(
            AlertIntegration.status == "active",
            AlertIntegration.source.in_(("tencent_monitor", "tencent_cls")),
            CloudProject.status == "active",
            CloudProject.project_id == column_project,
        )
    )
    if column_environment is not None:
        statement = statement.where(AlertIntegration.environment == column_environment)
    if column_service is not None:
        statement = statement.where(AlertIntegration.service == column_service)
    return exists(statement)


async def _count(session: AsyncSession, statement) -> int:
    return int(await session.scalar(statement) or 0)


@router.get("/overview", response_model=OperationsOverview)
async def read_operations_overview(
    session: Session,
    _: CurrentUser,
) -> OperationsOverview:
    """Summarize alert intake and deterministic automation state."""

    since = datetime.now(UTC) - timedelta(hours=24)
    open_incidents = await _count(
        session,
        select(func.count(Incident.id)).where(
            Incident.status.in_(OPEN_STATUSES),
            _active_tencent_scope(Incident.project_id, Incident.environment, Incident.service),
        ),
    )
    processing_incidents = await _count(
        session,
        select(func.count(Incident.id)).where(
            Incident.status.in_(PROCESSING_STATUSES),
            _active_tencent_scope(Incident.project_id, Incident.environment, Incident.service),
        ),
    )
    waiting_approval = await _count(
        session,
        select(func.count(Incident.id)).where(
            Incident.status == IncidentStatus.WAITING_APPROVAL,
            _active_tencent_scope(Incident.project_id, Incident.environment, Incident.service),
        ),
    )
    needs_human = await _count(
        session,
        select(func.count(Incident.id)).where(
            Incident.status == IncidentStatus.NEED_HUMAN,
            _active_tencent_scope(Incident.project_id, Incident.environment, Incident.service),
        ),
    )
    firing_alerts = await _count(
        session,
        select(func.count(Alert.id)).where(
            Alert.status == "firing",
            Alert.source.in_(("tencent_monitor", "tencent_cls")),
            _active_tencent_scope(Alert.project_id, Alert.environment, Alert.service),
        ),
    )
    alert_events_24h = await _count(
        session,
        select(func.count(RawAlertEvent.id)).where(
            RawAlertEvent.received_at >= since,
            RawAlertEvent.source.in_(("tencent_monitor", "tencent_cls")),
            _active_tencent_scope(RawAlertEvent.project_id),
        ),
    )
    resolved_incidents_24h = await _count(
        session,
        select(func.count(Incident.id)).where(
            Incident.status == IncidentStatus.RESOLVED,
            Incident.resolved_at >= since,
            _active_tencent_scope(Incident.project_id, Incident.environment, Incident.service),
        ),
    )
    latest_alert_at = await session.scalar(
        select(func.max(Alert.last_seen)).where(
            Alert.source.in_(("tencent_monitor", "tencent_cls")),
            _active_tencent_scope(Alert.project_id, Alert.environment, Alert.service)
        )
    )
    if needs_human:
        automation_state = "ATTENTION"
    elif waiting_approval:
        automation_state = "AWAITING_APPROVAL"
    elif processing_incidents:
        automation_state = "PROCESSING"
    else:
        automation_state = "IDLE"

    integration_rows = await session.execute(
        select(AlertIntegration, CloudProject.project_id)
        .join(CloudProject, CloudProject.id == AlertIntegration.cloud_project_id)
        .where(
            AlertIntegration.status == "active",
            CloudProject.status == "active",
            AlertIntegration.source.in_(("tencent_monitor", "tencent_cls")),
        )
        .order_by(CloudProject.project_id, AlertIntegration.name)
    )
    integrations = [
        AlertIntegrationSummary(
            source=integration.source,
            project_id=project_id,
            webhook_path=(
                f"/api/v1/webhooks/{integration.source.replace('_', '-')}/"
                f"{integration.integration_key}"
            ),
        )
        for integration, project_id in integration_rows.all()
    ]
    return OperationsOverview(
        automation_state=automation_state,
        open_incidents=open_incidents,
        processing_incidents=processing_incidents,
        waiting_approval=waiting_approval,
        needs_human=needs_human,
        firing_alerts=firing_alerts,
        alert_events_24h=alert_events_24h,
        resolved_incidents_24h=resolved_incidents_24h,
        latest_alert_at=latest_alert_at,
        conversation_live=True,
        integrations=integrations,
    )
