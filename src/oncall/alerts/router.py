"""Authenticated Alertmanager webhook route."""

import hmac
from typing import Annotated, Any

import structlog
from celery.exceptions import CeleryError
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from kombu.exceptions import KombuError
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.alerts.adapters.alertmanager import InvalidAlertmanagerPayload
from oncall.alerts.schemas import AlertIngestionCommand, IngestionResult
from oncall.alerts.service import AlertIdentityConflict, ingest_alerts
from oncall.auth.dependencies import get_db_session
from oncall.config import Settings, get_settings
from oncall.jobs.tasks import start_incident


router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
logger = structlog.get_logger(__name__)


def _verify_project_secret(
    project_id: str,
    authorization: str | None,
    settings: Settings,
) -> None:
    expected = settings.alertmanager_webhook_secrets.get(project_id)
    if expected is None or authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, separator, credential = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(credential, expected.get_secret_value())
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post(
    "/alertmanager/{project_id}",
    response_model=IngestionResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_alertmanager_webhook(
    project_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> IngestionResult:
    """Authenticate, persist and enqueue an Alertmanager delivery."""

    _verify_project_secret(project_id, authorization, settings)
    try:
        payload: Any = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Webhook body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Webhook body must be a JSON object")

    try:
        result = await ingest_alerts(
            session,
            AlertIngestionCommand(
                project_id=project_id,
                payload=payload,
                stable_label_names=settings.alert_fingerprint_stable_labels,
            ),
        )
    except (InvalidAlertmanagerPayload, AlertIdentityConflict) as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()

    try:
        for incident_id in result.incident_ids:
            start_incident.delay(str(incident_id), 1)
    except (CeleryError, KombuError) as exc:
        # Facts are already durable. A retry safely de-duplicates them and submits
        # the same idempotent task once Redis is available again.
        logger.error("incident_enqueue_failed", incident_ids=[str(i) for i in result.incident_ids])
        raise HTTPException(status_code=503, detail="Alert persisted; job queue unavailable") from exc
    return result
