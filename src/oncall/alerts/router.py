"""Authenticated Tencent Cloud webhook routes."""
import hmac
import base64
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from celery.exceptions import CeleryError
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from kombu.exceptions import KombuError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from oncall.alerts.adapters.tencent import (
    InvalidTencentAlertPayload,
    normalize_generic_alert,
    normalize_tencent_monitor,
)
from oncall.alerts.schemas import IngestionResult
from oncall.alerts.service import AlertIdentityConflict, ingest_normalized_alerts
from oncall.auth.dependencies import get_db_session
from oncall.jobs.tasks import start_incident
from oncall.models import AlertIntegration, CloudProject
from oncall.projects.security import verify_integration_secret


router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
logger = structlog.get_logger(__name__)


def _verify_integration_auth(
    integration: AlertIntegration,
    authorization: str | None,
) -> None:
    """Accept Bearer and Tencent Monitor's documented BasicAuth callback form."""

    if authorization is None:
        raise HTTPException(status_code=401, detail="Webhook authentication failed")
    scheme, separator, credential = authorization.partition(" ")
    supplied: str | None = None
    if separator and scheme.lower() == "bearer":
        supplied = credential.strip()
    elif separator and scheme.lower() == "basic":
        try:
            decoded = base64.b64decode(credential, validate=True).decode("utf-8")
            _, password = decoded.split(":", 1)
            supplied = password
        except (ValueError, UnicodeDecodeError):
            supplied = None
    if not supplied or integration.status != "active" or not verify_integration_secret(
        supplied, integration.secret_hash
    ):
        raise HTTPException(
            status_code=401,
            detail="Webhook authentication failed",
            headers={"WWW-Authenticate": "Bearer, Basic"},
        )


async def _receive_catalog_webhook(
    *,
    expected_source: str,
    integration_key: str,
    request: Request,
    session: AsyncSession,
    authorization: str | None,
) -> IngestionResult:
    integration = await session.scalar(
        select(AlertIntegration).where(AlertIntegration.integration_key == integration_key)
    )
    if integration is None or integration.source != expected_source:
        raise HTTPException(status_code=404, detail="告警集成不存在")
    _verify_integration_auth(integration, authorization)
    project = await session.get(CloudProject, integration.cloud_project_id)
    if project is None or project.status != "active":
        raise HTTPException(status_code=409, detail="工程已停用或不存在")
    try:
        payload: Any = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Webhook body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Webhook body must be a JSON object")
    try:
        if expected_source == "tencent_monitor":
            envelopes = normalize_tencent_monitor(
                payload,
                project_id=project.project_id,
                environment=integration.environment,
                service=integration.service,
            )
        else:
            envelopes = normalize_generic_alert(
                payload,
                source=expected_source,
                project_id=project.project_id,
                environment=integration.environment,
                service=integration.service,
            )
        result = await ingest_normalized_alerts(
            session,
            project_id=project.project_id,
            source=expected_source,
            raw_payload=payload,
            envelopes=envelopes,
        )
    except (InvalidTencentAlertPayload, AlertIdentityConflict, ValueError) as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    integration.last_received_at = datetime.now(UTC)
    await session.commit()
    try:
        for incident_id in result.incident_ids:
            start_incident.delay(str(incident_id), 1)
    except (CeleryError, KombuError) as exc:
        logger.error("incident_enqueue_failed", incident_ids=[str(i) for i in result.incident_ids])
        raise HTTPException(status_code=503, detail="Alert persisted; job queue unavailable") from exc
    return result


@router.post(
    "/tencent-monitor/{integration_key}",
    response_model=IngestionResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_tencent_monitor_webhook(
    integration_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> IngestionResult:
    return await _receive_catalog_webhook(
        expected_source="tencent_monitor",
        integration_key=integration_key,
        request=request,
        session=session,
        authorization=authorization,
    )


@router.post(
    "/tencent-cls/{integration_key}",
    response_model=IngestionResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_tencent_cls_webhook(
    integration_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> IngestionResult:
    return await _receive_catalog_webhook(
        expected_source="tencent_cls",
        integration_key=integration_key,
        request=request,
        session=session,
        authorization=authorization,
    )
