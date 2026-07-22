"""Alertmanager webhook adapter."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from oncall.alerts.fingerprint import resolve_fingerprint
from oncall.alerts.schemas import AlertEnvelope


class InvalidAlertmanagerPayload(ValueError):
    """Raised when an authenticated webhook does not match the expected contract."""


class _AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str | None = None
    labels: dict[str, Any]
    annotations: dict[str, Any] = Field(default_factory=dict)
    startsAt: datetime
    endsAt: datetime | None = None
    fingerprint: str | None = None


class _AlertmanagerWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    alerts: list[_AlertmanagerAlert]


def _required_label(labels: Mapping[str, Any], name: str) -> str:
    value = labels.get(name)
    if not isinstance(value, str) or not value.strip():
        raise InvalidAlertmanagerPayload(f"missing or invalid alert label: {name}")
    return value.strip()


def _optional_end(value: datetime | None, status: str) -> datetime | None:
    # Alertmanager represents the missing end of a firing alert as Go's year-1 zero time.
    if status == "firing" or value is None or value.year <= 1:
        return None
    return value


def normalize_alertmanager(
    payload: Mapping[str, Any],
    project_id: str,
    stable_label_names: Sequence[str] = (),
) -> list[AlertEnvelope]:
    """Convert one Alertmanager webhook into atomic unified alerts."""

    try:
        webhook = _AlertmanagerWebhook.model_validate(payload)
    except ValidationError as exc:
        raise InvalidAlertmanagerPayload("invalid Alertmanager webhook payload") from exc

    if not webhook.alerts:
        raise InvalidAlertmanagerPayload("Alertmanager webhook contains no alerts")

    envelopes: list[AlertEnvelope] = []
    for item in webhook.alerts:
        labels = dict(item.labels)
        labeled_project = labels.get("project_id")
        if labeled_project is not None and labeled_project != project_id:
            raise InvalidAlertmanagerPayload("project label does not match authenticated project")

        environment = _required_label(labels, "environment")
        service = _required_label(labels, "service")
        alert_name = _required_label(labels, "alertname")
        severity = _required_label(labels, "severity")
        status = item.status or webhook.status
        if status not in {"firing", "resolved"}:
            raise InvalidAlertmanagerPayload(f"unsupported alert status: {status!r}")
        correlation_key = labels.get("correlation_key") or alert_name
        if not isinstance(correlation_key, str) or not correlation_key.strip():
            raise InvalidAlertmanagerPayload("correlation_key must be a non-empty string")

        fingerprint = resolve_fingerprint(
            item.fingerprint,
            project_id=project_id,
            environment=environment,
            service=service,
            alert_name=alert_name,
            labels=labels,
            stable_label_names=stable_label_names,
        )
        try:
            envelopes.append(
                AlertEnvelope(
                    project_id=project_id,
                    environment=environment,
                    service=service,
                    alert_name=alert_name,
                    severity=severity,
                    status=status,
                    starts_at=item.startsAt,
                    ends_at=_optional_end(item.endsAt, status),
                    fingerprint=fingerprint,
                    correlation_key=correlation_key.strip(),
                    labels=labels,
                    annotations=dict(item.annotations),
                )
            )
        except ValidationError as exc:
            raise InvalidAlertmanagerPayload("invalid normalized alert") from exc
    return envelopes
