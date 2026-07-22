"""Narrow read-only adapters for the local demo observability stack."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_servers.common import BearerAuthMiddleware, BoundedJsonError, get_bounded_json
from oncall.mcp_gateway.schemas import (
    ActiveAlert,
    ActiveAlertsRequest,
    ActiveAlertsResult,
    LogEntry,
    LogsResult,
    MetricName,
    MetricPoint,
    MetricsResult,
    QueryLogsRequest,
    QueryMetricsRequest,
    ServiceHealthRequest,
    ServiceHealthResult,
)


LOG_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "demo-service.log"
DEMO_SECRET = "demo-only-observability-mcp-secret"


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "production"] = "local"
    observability_project_id: str = Field(
        default="demo-shop", pattern=r"^[a-z0-9][a-z0-9-]{1,63}$"
    )
    observability_environment: str = Field(
        default="staging", pattern=r"^[a-z][a-z0-9-]{1,31}$"
    )
    observability_service: str = Field(
        default="order-api", pattern=r"^[a-z][a-z0-9-]{1,63}$"
    )
    observability_prometheus_url: AnyHttpUrl = "http://127.0.0.1:19090"
    observability_alertmanager_url: AnyHttpUrl = "http://127.0.0.1:19093"
    observability_service_url: AnyHttpUrl = "http://127.0.0.1:18080"
    observability_mcp_secret: SecretStr = Field(
        default=SecretStr(DEMO_SECRET), min_length=16
    )

    @model_validator(mode="after")
    def reject_demo_production_secret(self) -> "ServerSettings":
        if (
            self.app_env == "production"
            and self.observability_mcp_secret.get_secret_value() == DEMO_SECRET
        ):
            raise ValueError("production requires a non-demo Observability MCP secret")
        return self


SERVER_SETTINGS = ServerSettings()
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

mcp = FastMCP(
    "oncall-observability",
    instructions=(
        "Read-only, fixed-schema demo observability tools. Raw PromQL, Loki, URLs, "
        "Shell commands, and write operations are not accepted."
    ),
    stateless_http=True,
    json_response=True,
)


def _assert_scope(request: Any) -> None:
    if (
        request.project_id != SERVER_SETTINGS.observability_project_id
        or request.environment != SERVER_SETTINGS.observability_environment
        or request.service != SERVER_SETTINGS.observability_service
    ):
        raise ValueError("scope is not registered on this observability server")


def _retryable(message: str) -> RuntimeError:
    return RuntimeError(f"RETRYABLE: {message}")


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def query_metrics(request: QueryMetricsRequest) -> MetricsResult:
    """Query one server-defined metric; callers cannot submit PromQL."""

    _assert_scope(request)
    expressions = {
        MetricName.HTTP_ERROR_RATE: (
            'sum(rate(demo_http_requests_total{status=~"5.."}[2m])) '
            "/ clamp_min(sum(rate(demo_http_requests_total[2m])), 0.000001)"
        ),
        MetricName.REQUEST_RATE: "sum(rate(demo_http_requests_total[2m]))",
    }
    units = {
        MetricName.HTTP_ERROR_RATE: "ratio",
        MetricName.REQUEST_RATE: "requests_per_second",
    }
    duration = (request.end_time - request.start_time).total_seconds()
    step = max(1, math.ceil(duration / request.limit))
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            payload = await get_bounded_json(
                client,
                f"{str(SERVER_SETTINGS.observability_prometheus_url).rstrip('/')}/api/v1/query_range",
                max_bytes=524_288,
                params={
                    "query": expressions[request.metric],
                    "start": request.start_time.timestamp(),
                    "end": request.end_time.timestamp(),
                    "step": step,
                },
            )
    except (httpx.HTTPError, BoundedJsonError) as exc:
        raise _retryable("Prometheus query failed") from exc
    if payload.get("status") != "success":
        raise _retryable("Prometheus returned an unsuccessful response")

    values: list[list[Any]] = []
    for series in payload.get("data", {}).get("result", []):
        values.extend(series.get("values", []))
    values.sort(key=lambda item: float(item[0]))
    points: list[MetricPoint] = []
    for timestamp, raw_value in values[: request.limit]:
        try:
            value = float(raw_value)
            if not math.isfinite(value):
                value = None
        except (TypeError, ValueError):
            value = None
        points.append(
            MetricPoint(
                timestamp=datetime.fromtimestamp(float(timestamp), tz=timezone.utc),
                value=value,
            )
        )
    return MetricsResult(
        project_id=request.project_id,
        environment=request.environment,
        service=request.service,
        metric=request.metric,
        unit=units[request.metric],
        points=points,
        query_summary=f"server-defined {request.metric.value} over {duration:.0f}s",
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def query_service_logs(request: QueryLogsRequest) -> LogsResult:
    """Read bounded local demo logs using only fixed level and time filters."""

    _assert_scope(request)
    if not LOG_PATH.is_file():
        return LogsResult(
            project_id=request.project_id,
            environment=request.environment,
            service=request.service,
            entries=[],
            returned_bytes=0,
            truncated=False,
            source_status="not_configured",
        )

    entries: list[LogEntry] = []
    returned_bytes = 0
    truncated = False
    allowed_levels = set(request.levels)
    scanned_bytes = 0
    max_scan_bytes = 2 * 1024 * 1024
    with LOG_PATH.open("rb") as log_file:
        while scanned_bytes < max_scan_bytes:
            remaining = max_scan_bytes - scanned_bytes
            raw_line = log_file.readline(min(65_536, remaining + 1))
            if not raw_line:
                break
            scanned_bytes += len(raw_line)
            if scanned_bytes > max_scan_bytes:
                truncated = True
                break
            line = raw_line.decode("utf-8", "replace")
            try:
                item = json.loads(line)
                timestamp = datetime.fromisoformat(
                    str(item["timestamp"]).replace("Z", "+00:00")
                )
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                else:
                    timestamp = timestamp.astimezone(timezone.utc)
                level = str(item.get("level", "INFO")).upper()
                entry = LogEntry(
                    timestamp=timestamp,
                    level=level,
                    message=str(item.get("message", ""))[:2048],
                    trace_id=item.get("trace_id"),
                    status_code=item.get("status_code"),
                    version=item.get("version"),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if not request.start_time <= entry.timestamp <= request.end_time:
                continue
            if entry.level not in allowed_levels:
                continue
            encoded_size = len(entry.model_dump_json().encode("utf-8"))
            if len(entries) >= request.limit or returned_bytes + encoded_size > 20_480:
                truncated = True
                break
            entries.append(entry)
            returned_bytes += encoded_size
    return LogsResult(
        project_id=request.project_id,
        environment=request.environment,
        service=request.service,
        entries=entries,
        returned_bytes=returned_bytes,
        truncated=truncated,
        source_status="available",
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_service_health(request: ServiceHealthRequest) -> ServiceHealthResult:
    """Fetch the registered service's fixed health endpoint."""

    _assert_scope(request)
    checked_at = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                f"{str(SERVER_SETTINGS.observability_service_url).rstrip('/')}/health"
            )
        status = "healthy" if response.status_code == 200 else "unhealthy"
        detail = f"registered health endpoint returned HTTP {response.status_code}"
    except httpx.HTTPError:
        status = "unknown"
        detail = "registered health endpoint was unreachable"
    return ServiceHealthResult(
        project_id=request.project_id,
        environment=request.environment,
        service=request.service,
        status=status,
        checked_at=checked_at,
        detail=detail,
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_active_alerts(request: ActiveAlertsRequest) -> ActiveAlertsResult:
    """Return active Alertmanager alerts within the registered project boundary."""

    _assert_scope(request)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            payload = await get_bounded_json(
                client,
                f"{str(SERVER_SETTINGS.observability_alertmanager_url).rstrip('/')}/api/v2/alerts",
                max_bytes=262_144,
            )
    except (httpx.HTTPError, BoundedJsonError) as exc:
        raise _retryable("Alertmanager query failed") from exc

    alerts: list[ActiveAlert] = []
    for item in payload:
        labels = item.get("labels", {})
        if (
            labels.get("project_id") != request.project_id
            or labels.get("environment") != request.environment
            or labels.get("service") != request.service
        ):
            continue
        starts_at = item.get("startsAt")
        parsed_start = (
            datetime.fromisoformat(starts_at.replace("Z", "+00:00")) if starts_at else None
        )
        if parsed_start is not None and parsed_start > request.end_time:
            continue
        alerts.append(
            ActiveAlert(
                name=str(labels.get("alertname", "unknown")),
                severity=str(labels.get("severity", "unknown")),
                status=str(item.get("status", {}).get("state", "active")),
                starts_at=parsed_start,
                summary=str(item.get("annotations", {}).get("summary", ""))[:2048],
            )
        )
        if len(alerts) >= request.limit:
            break
    return ActiveAlertsResult(
        project_id=request.project_id,
        environment=request.environment,
        service=request.service,
        alerts=alerts,
        observed_at=datetime.now(timezone.utc),
    )


@mcp.custom_route("/health/ready", methods=["GET"])
async def ready(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "observability-mcp"})


app = BearerAuthMiddleware(
    mcp.streamable_http_app(),
    SERVER_SETTINGS.observability_mcp_secret.get_secret_value(),
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18081)
