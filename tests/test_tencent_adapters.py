from datetime import UTC

import pytest

from oncall.alerts.adapters.tencent import (
    InvalidTencentAlertPayload,
    normalize_generic_alert,
    normalize_tencent_monitor,
)


def test_tencent_monitor_callback_is_normalized_and_replay_stable():
    payload = {
        "alarmId": "alarm-123",
        "policyName": "HighErrorRate",
        "alarmStatus": 1,
        "alarmLevel": "Serious",
        "firstOccurTime": "2026-08-05 10:00:00",
        "content": "5xx ratio above threshold",
    }
    first = normalize_tencent_monitor(
        payload, project_id="payments", environment="prod", service="checkout"
    )[0]
    second = normalize_tencent_monitor(
        payload, project_id="payments", environment="prod", service="checkout"
    )[0]
    assert first.source == "tencent_monitor"
    assert first.status == "firing"
    assert first.severity == "critical"
    assert first.starts_at.tzinfo is not None
    assert first.starts_at.astimezone(UTC).hour == 2
    assert first.fingerprint == second.fingerprint


def test_generic_callback_supports_recovery_and_rejects_empty_alerts():
    result = normalize_generic_alert(
        {
            "alerts": [
                {
                    "event_id": "evt-1",
                    "alert_name": "PodCrashLoop",
                    "status": "resolved",
                    "starts_at": "2026-08-05T02:00:00Z",
                    "ends_at": "2026-08-05T02:01:00Z",
                    "labels": {"namespace": "payments"},
                }
            ]
        },
        source="tencent_cls",
        project_id="payments",
        environment="prod",
        service="checkout",
    )
    assert result[0].status == "resolved"
    assert result[0].labels["namespace"] == "payments"
    with pytest.raises(InvalidTencentAlertPayload):
        normalize_generic_alert({"alerts": []}, source="generic", project_id="p", environment="prod", service="api")
