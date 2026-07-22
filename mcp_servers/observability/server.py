"""Narrow read-only adapters for the local demo observability stack."""

from __future__ import annotations

import json
import math
import errno
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import stat
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


LOG_DIRECTORY = Path(__file__).resolve().parents[2] / ".runtime" / "demo-logs"
LOG_PATH = LOG_DIRECTORY / "demo-service.log"
LOG_FILENAMES = (
    "demo-service.log.3",
    "demo-service.log.2",
    "demo-service.log.1",
    "demo-service.log",
)
MAX_LOG_FILE_SCAN_BYTES = 1_048_576
MAX_LOG_TOTAL_SCAN_BYTES = MAX_LOG_FILE_SCAN_BYTES * len(LOG_FILENAMES)
MAX_LOG_LINE_BYTES = 65_536
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


class UnsafeLogSource(RuntimeError):
    """Raised when the fixed log directory contains an unsafe file type."""


@dataclass(frozen=True)
class LogFileSnapshot:
    filename: str
    fd: int
    metadata: os.stat_result


def _open_log_directory() -> int | None:
    """Open the fixed directory without following runtime-controlled symlinks."""

    runtime_directory = LOG_DIRECTORY.parent
    for directory in (runtime_directory, LOG_DIRECTORY):
        try:
            metadata = os.lstat(directory)
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeLogSource(f"unsafe log directory: {directory.name}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(LOG_DIRECTORY, flags)
    except OSError as exc:
        raise UnsafeLogSource("cannot safely open fixed demo log directory") from exc
    if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
        os.close(directory_fd)
        raise UnsafeLogSource("fixed demo log path is not a directory")
    return directory_fd


def _open_regular_log(directory_fd: int, filename: str) -> int | None:
    """Open one allowlisted basename relative to the already verified directory."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        file_fd = os.open(filename, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise UnsafeLogSource(f"symbolic log file rejected: {filename}") from exc
        raise UnsafeLogSource(f"cannot safely open log file: {filename}") from exc
    if not stat.S_ISREG(os.fstat(file_fd).st_mode):
        os.close(file_fd)
        raise UnsafeLogSource(f"non-regular log file rejected: {filename}")
    return file_fd


def _rotation_range_status(directory_fd: int) -> tuple[bool, bool]:
    """Detect unsupported rotations without materializing an unbounded directory list."""

    out_of_range = False
    crowded_directory = False
    with os.scandir(directory_fd) as entries:
        for index, entry in enumerate(entries, start=1):
            if index > 256:
                crowded_directory = True
                break
            match = re.fullmatch(r"demo-service\.log\.(\d+)", entry.name)
            if match and int(match.group(1)) >= 4:
                out_of_range = True
    return out_of_range, crowded_directory


def _rotation_inode_snapshot(directory_fd: int) -> dict[str, tuple[int, int]]:
    """Capture allowlisted names without following runtime-controlled links."""

    snapshot: dict[str, tuple[int, int]] = {}
    for filename in LOG_FILENAMES:
        try:
            metadata = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise UnsafeLogSource(f"cannot safely stat log file: {filename}") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafeLogSource(f"non-regular log file rejected: {filename}")
        snapshot[filename] = (metadata.st_dev, metadata.st_ino)
    return snapshot


def _snapshot_log_files(directory_fd: int) -> tuple[list[LogFileSnapshot], bool]:
    """Open all allowlisted log files before reading to bound rotation races."""

    snapshots: list[LogFileSnapshot] = []
    truncated = False
    try:
        before_snapshot = _rotation_inode_snapshot(directory_fd)
        for filename in LOG_FILENAMES:
            file_fd = _open_regular_log(directory_fd, filename)
            if file_fd is None:
                continue
            after_open = os.fstat(file_fd)
            expected_inode = before_snapshot.get(filename)
            if expected_inode is None or expected_inode != (
                after_open.st_dev,
                after_open.st_ino,
            ):
                truncated = True
            snapshots.append(LogFileSnapshot(filename, file_fd, after_open))
        # After every descriptor is open, later rotations cannot invalidate the
        # captured files. A changed name->inode map means rotation happened while
        # taking the snapshot, so results remain bounded but must be marked partial.
        if _rotation_inode_snapshot(directory_fd) != before_snapshot:
            truncated = True
    except Exception:
        for snapshot in snapshots:
            os.close(snapshot.fd)
        raise
    return snapshots, truncated


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
    directory_fd = _open_log_directory()
    if directory_fd is None:
        return LogsResult(
            project_id=request.project_id,
            environment=request.environment,
            service=request.service,
            entries=[],
            returned_bytes=0,
            truncated=False,
            source_status="not_configured",
        )

    matching_entries: list[LogEntry] = []
    earliest_observed: datetime | None = None
    opened_files = 0
    total_scanned_bytes = 0
    truncated = False
    oldest_rotation_present = False
    allowed_levels = set(request.levels)
    try:
        out_of_range_rotation, crowded_directory = _rotation_range_status(directory_fd)
        truncated = out_of_range_rotation or crowded_directory
        log_snapshots, snapshot_truncated = _snapshot_log_files(directory_fd)
        truncated = truncated or snapshot_truncated
        stop_scanning = False
        for snapshot in log_snapshots:
            if stop_scanning:
                truncated = True
                os.close(snapshot.fd)
                continue
            opened_files += 1
            oldest_rotation_present |= snapshot.filename == "demo-service.log.3"
            metadata = snapshot.metadata
            if metadata.st_size > MAX_LOG_FILE_SCAN_BYTES:
                truncated = True
            file_scanned_bytes = 0
            discard_long_line = False
            with os.fdopen(snapshot.fd, "rb", closefd=True) as log_file:
                while True:
                    file_remaining = MAX_LOG_FILE_SCAN_BYTES - file_scanned_bytes
                    total_remaining = MAX_LOG_TOTAL_SCAN_BYTES - total_scanned_bytes
                    if file_remaining <= 0:
                        if log_file.tell() < metadata.st_size:
                            truncated = True
                        break
                    if total_remaining <= 0:
                        if log_file.tell() < metadata.st_size:
                            truncated = True
                        stop_scanning = True
                        break
                    read_limit = min(MAX_LOG_LINE_BYTES, file_remaining, total_remaining)
                    raw_line = log_file.readline(read_limit)
                    if not raw_line:
                        break
                    file_scanned_bytes += len(raw_line)
                    total_scanned_bytes += len(raw_line)

                    complete_line = raw_line.endswith(b"\n") or log_file.tell() == metadata.st_size
                    if discard_long_line:
                        if raw_line.endswith(b"\n"):
                            discard_long_line = False
                        continue
                    if not complete_line:
                        truncated = True
                        discard_long_line = True
                        continue
                    try:
                        item = json.loads(raw_line)
                        timestamp = datetime.fromisoformat(
                            str(item["timestamp"]).replace("Z", "+00:00")
                        )
                        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                            timestamp = timestamp.replace(tzinfo=timezone.utc)
                        else:
                            timestamp = timestamp.astimezone(timezone.utc)
                        entry = LogEntry(
                            timestamp=timestamp,
                            level=str(item.get("level", "INFO")).upper(),
                            message=str(item.get("message", ""))[:2048],
                            trace_id=item.get("trace_id"),
                            status_code=item.get("status_code"),
                            version=item.get("version"),
                        )
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if earliest_observed is None or entry.timestamp < earliest_observed:
                        earliest_observed = entry.timestamp
                    if not request.start_time <= entry.timestamp <= request.end_time:
                        continue
                    if entry.level not in allowed_levels:
                        continue
                    matching_entries.append(entry)
    finally:
        os.close(directory_fd)

    if opened_files == 0:
        return LogsResult(
            project_id=request.project_id,
            environment=request.environment,
            service=request.service,
            entries=[],
            returned_bytes=0,
            truncated=truncated,
            source_status="not_configured",
        )

    matching_entries.sort(key=lambda entry: entry.timestamp)
    selected_reversed: list[LogEntry] = []
    returned_bytes = 0
    for entry in reversed(matching_entries):
        encoded_size = len(entry.model_dump_json().encode("utf-8"))
        if len(selected_reversed) >= request.limit:
            truncated = True
            break
        if returned_bytes + encoded_size > 20_480:
            truncated = True
            continue
        selected_reversed.append(entry)
        returned_bytes += encoded_size
    entries = list(reversed(selected_reversed))
    if len(entries) < len(matching_entries):
        truncated = True
    if oldest_rotation_present and (
        earliest_observed is None or request.start_time < earliest_observed
    ):
        truncated = True
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
