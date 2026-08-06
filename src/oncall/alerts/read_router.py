"""Authenticated alert inbox API."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.alerts.queries import list_alerts
from oncall.alerts.schemas import AlertListResponse
from oncall.auth.dependencies import CurrentUser, get_db_session


router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])
Session = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("", response_model=AlertListResponse)
async def read_alerts(
    session: Session,
    _: CurrentUser,
    alert_status: Literal["firing", "resolved"] | None = Query(
        default=None, alias="status"
    ),
    severity: str | None = Query(default=None, max_length=32),
    service: str | None = Query(default=None, max_length=128),
    project_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AlertListResponse:
    """List normalized, de-duplicated alerts and their latest Incident link."""

    return await list_alerts(
        session,
        status=alert_status,
        severity=severity,
        service=service,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )
