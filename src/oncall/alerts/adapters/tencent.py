"""Adapters for Tencent Cloud Monitor, CLS and normalized custom callbacks."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from oncall.alerts.fingerprint import stable_fingerprint
from oncall.alerts.schemas import AlertEnvelope


class InvalidTencentAlertPayload(ValueError):
    """Raised when an authenticated Tencent callback cannot be normalized."""


_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _text(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


def _time(value: Any, *, default: datetime | None = None) -> datetime | None:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=UTC)
    if not isinstance(value, str):
        raise InvalidTencentAlertPayload("告警时间格式无效")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise InvalidTencentAlertPayload("告警时间格式无效") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SHANGHAI)
    return parsed.astimezone(UTC)


def _status(value: Any) -> str:
    if value in (0, "0", False, "resolved", "recovered", "RECOVERED", "恢复"):
        return "resolved"
    if value in (1, "1", True, "firing", "FIRING", "triggered", "告警"):
        return "firing"
    raise InvalidTencentAlertPayload("不支持的告警状态")


def _severity(value: Any) -> str:
    normalized = str(value or "warning").strip().lower()
    return {
        "serious": "critical",
        "critical": "critical",
        "严重": "critical",
        "warn": "warning",
        "warning": "warning",
        "警告": "warning",
        "remind": "info",
        "info": "info",
        "提醒": "info",
    }.get(normalized, "warning")


def _fingerprint(
    source_event_id: str | None,
    *,
    project_id: str,
    environment: str,
    service: str,
    alert_name: str,
    labels: dict[str, Any],
) -> str:
    if source_event_id:
        labels = {**labels, "source_event_id": source_event_id}
        stable_names = ("source_event_id",)
    else:
        stable_names = ()
    return stable_fingerprint(
        project_id=project_id,
        environment=environment,
        service=service,
        alert_name=alert_name,
        labels=labels,
        stable_label_names=stable_names,
    )


def normalize_tencent_monitor(
    payload: Mapping[str, Any],
    *,
    project_id: str,
    environment: str,
    service: str,
) -> list[AlertEnvelope]:
    """Normalize the JSON callback emitted by Tencent Cloud Monitor."""

    alert_name = _text(payload, "policyName", "alarmPolicyName", "alarmName", "metricName")
    if alert_name is None:
        raise InvalidTencentAlertPayload("腾讯云监控回调缺少策略或告警名称")
    status = _status(payload.get("alarmStatus", payload.get("status", 1)))
    starts_at = _time(
        payload.get("firstOccurTime", payload.get("startsAt")),
        default=datetime.now(UTC),
    )
    ends_at = _time(payload.get("recoverTime", payload.get("endsAt"))) if status == "resolved" else None
    source_event_id = _text(payload, "alertId", "alarmId", "sessionId", "eventId")
    labels: dict[str, Any] = {
        "project_id": project_id,
        "environment": environment,
        "service": service,
        "source": "tencent_monitor",
    }
    if source_event_id:
        labels["source_event_id"] = source_event_id
    for key in ("namespace", "metricName", "region", "productName", "policyId"):
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)):
            labels[key] = value
    summary = _text(payload, "content", "alarmContent", "message") or alert_name
    try:
        return [
            AlertEnvelope(
                source="tencent_monitor",
                project_id=project_id,
                environment=environment,
                service=service,
                alert_name=alert_name,
                severity=_severity(payload.get("alarmLevel", payload.get("severity"))),
                status=status,
                starts_at=starts_at,
                ends_at=ends_at,
                fingerprint=_fingerprint(
                    source_event_id,
                    project_id=project_id,
                    environment=environment,
                    service=service,
                    alert_name=alert_name,
                    labels=labels,
                ),
                correlation_key=_text(payload, "correlationKey") or alert_name,
                labels=labels,
                annotations={"summary": summary},
            )
        ]
    except ValidationError as exc:
        raise InvalidTencentAlertPayload("腾讯云监控告警标准化失败") from exc


def normalize_generic_alert(
    payload: Mapping[str, Any],
    *,
    source: str,
    project_id: str,
    environment: str,
    service: str,
) -> list[AlertEnvelope]:
    """Normalize a deliberately small contract suitable for CLS custom callbacks."""

    items = payload.get("alerts", [payload])
    if not isinstance(items, list) or not items:
        raise InvalidTencentAlertPayload("告警回调必须包含至少一条告警")
    envelopes: list[AlertEnvelope] = []
    for raw_item in items:
        if not isinstance(raw_item, Mapping):
            raise InvalidTencentAlertPayload("alerts 中的元素必须是 JSON 对象")
        alert_name = _text(raw_item, "alert_name", "alertName", "name", "alarmName")
        if alert_name is None:
            raise InvalidTencentAlertPayload("自定义告警缺少 alert_name")
        item_status = _status(raw_item.get("status", "firing"))
        starts_at = _time(raw_item.get("starts_at", raw_item.get("startsAt")), default=datetime.now(UTC))
        ends_at = _time(raw_item.get("ends_at", raw_item.get("endsAt"))) if item_status == "resolved" else None
        labels = raw_item.get("labels") if isinstance(raw_item.get("labels"), dict) else {}
        labels = {
            **labels,
            "project_id": project_id,
            "environment": environment,
            "service": service,
            "source": source,
        }
        event_id = _text(raw_item, "event_id", "eventId", "fingerprint")
        annotations = raw_item.get("annotations") if isinstance(raw_item.get("annotations"), dict) else {}
        summary = _text(raw_item, "summary", "message")
        if summary:
            annotations = {**annotations, "summary": summary}
        try:
            envelopes.append(
                AlertEnvelope(
                    source=source,
                    project_id=project_id,
                    environment=environment,
                    service=service,
                    alert_name=alert_name,
                    severity=_severity(raw_item.get("severity")),
                    status=item_status,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    fingerprint=_fingerprint(
                        event_id,
                        project_id=project_id,
                        environment=environment,
                        service=service,
                        alert_name=alert_name,
                        labels=labels,
                    ),
                    correlation_key=_text(raw_item, "correlation_key", "correlationKey") or alert_name,
                    labels=labels,
                    annotations=annotations,
                )
            )
        except ValidationError as exc:
            raise InvalidTencentAlertPayload("自定义告警标准化失败") from exc
    return envelopes
