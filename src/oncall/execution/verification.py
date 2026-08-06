"""Read-only verification after a completed Recovery MCP operation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from oncall.config import get_settings
from oncall.mcp_gateway.client import McpGateway
from oncall.mcp_gateway.schemas import LogLevel
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
    """Verify recovery by checking the post-action Tencent Cloud CLS error logs."""

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
        logs = await client.call_read_tool(
            "query_service_logs",
            {
                **scope,
                "start_time": now - timedelta(minutes=5),
                "end_time": now,
                "levels": (LogLevel.ERROR,),
                "limit": 100,
            },
            incident_id=str(incident.id),
            agent_name="verification",
        )
    except Exception:
        return VerificationResult(
            passed=False,
            checks=[{"criterion": criterion, "status": "unavailable"} for criterion in criteria],
            reason="Required read-only recovery verification data is unavailable.",
        )

    entries = logs.data.get("entries")
    error_count = len(entries) if isinstance(entries, list) else 0
    error_ok = error_count == 0
    checks.append(
        {
            "name": "cls_error_logs",
            "status": "passed" if error_ok else "failed",
            "value": error_count,
            "threshold": 0,
        }
    )
    passed = error_ok
    return VerificationResult(
        passed=passed,
        checks=checks,
        reason=None if passed else "Tencent Cloud CLS still reports error logs after recovery.",
    )
