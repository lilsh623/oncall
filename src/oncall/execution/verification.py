"""Read-only verification after a completed Recovery MCP operation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from oncall.config import get_settings
from oncall.mcp_gateway.client import McpGateway
from oncall.mcp_gateway.schemas import MetricName
from oncall.models import Incident


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    checks: list[dict[str, object]] = Field(default_factory=list)
    reason: str | None = None


async def verify_recovery(
    criteria: list[str],
    *,
    incident: Incident,
    gateway: McpGateway | None = None,
    wait_seconds: float | None = None,
) -> VerificationResult:
    """Wait one scrape period, then verify release, health, and 5xx ratio.

    The function has no Recovery MCP or Podman access. Any unavailable read-only
    observation fails verification and returns the Incident to a human.
    """

    delay = (
        wait_seconds
        if wait_seconds is not None
        else get_settings().recovery_verification_wait_seconds
    )
    if delay > 0:
        await asyncio.sleep(delay)
    client = gateway or McpGateway()
    scope = {
        "project_id": incident.project_id,
        "environment": incident.environment,
        "service": incident.service,
    }
    now = datetime.now(timezone.utc)
    checks: list[dict[str, object]] = []
    try:
        release, health, metrics = await asyncio.gather(
            client.call_read_tool(
                "get_current_release", scope, incident_id=str(incident.id), agent_name="verification"
            ),
            client.call_read_tool(
                "get_service_health",
                {
                    **scope,
                    "start_time": now - timedelta(minutes=5),
                    "end_time": now,
                    "limit": 1,
                },
                incident_id=str(incident.id),
                agent_name="verification",
            ),
            client.call_read_tool(
                "query_metrics",
                {
                    **scope,
                    "start_time": now - timedelta(minutes=5),
                    "end_time": now,
                    "metric": MetricName.HTTP_ERROR_RATE,
                    "rate_window_seconds": 30,
                    "limit": 30,
                },
                incident_id=str(incident.id),
                agent_name="verification",
            ),
        )
    except Exception:
        return VerificationResult(
            passed=False,
            checks=[{"criterion": criterion, "status": "unavailable"} for criterion in criteria],
            reason="Required read-only recovery verification data is unavailable.",
        )

    release_version = ((release.data.get("release") or {}).get("version"))
    release_ok = release_version == "v1"
    checks.append(
        {"name": "release_version", "status": "passed" if release_ok else "failed", "value": release_version}
    )
    health_status = health.data.get("status")
    health_ok = health_status == "healthy"
    checks.append(
        {"name": "service_health", "status": "passed" if health_ok else "failed", "value": health_status}
    )
    points = metrics.data.get("points")
    numeric_points = [
        float(point["value"])
        for point in points if isinstance(point, dict) and point.get("value") is not None
    ] if isinstance(points, list) else []
    error_rate = numeric_points[-1] if numeric_points else None
    error_ok = error_rate is not None and error_rate < 0.05
    checks.append(
        {
            "name": "http_error_rate",
            "status": "passed" if error_ok else "failed",
            "value": error_rate,
            "threshold": 0.05,
        }
    )
    passed = release_ok and health_ok and error_ok
    return VerificationResult(
        passed=passed,
        checks=checks,
        reason=None if passed else "Release, health, or error-rate verification did not pass.",
    )
