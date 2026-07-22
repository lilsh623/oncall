"""Fixed input and output contracts shared by read-only MCP servers and Gateway."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScopedRequest(StrictModel):
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    environment: str = Field(pattern=r"^[a-z][a-z0-9-]{1,31}$")
    service: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")


class TimeWindowRequest(ScopedRequest):
    start_time: datetime
    end_time: datetime
    limit: int = Field(default=60, ge=1, le=500)

    @field_validator("start_time", "end_time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_window(self) -> "TimeWindowRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if (self.end_time - self.start_time).total_seconds() > 86_400:
            raise ValueError("time window cannot exceed 24 hours")
        return self


class MetricName(StrEnum):
    HTTP_ERROR_RATE = "http_error_rate"
    REQUEST_RATE = "request_rate"


class QueryMetricsRequest(TimeWindowRequest):
    metric: MetricName
    limit: int = Field(default=60, ge=1, le=240)


class MetricPoint(StrictModel):
    timestamp: datetime
    value: float | None


class MetricsResult(StrictModel):
    project_id: str
    environment: str
    service: str
    metric: MetricName
    unit: Literal["ratio", "requests_per_second"]
    points: list[MetricPoint]
    query_summary: str


class LogLevel(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class QueryLogsRequest(TimeWindowRequest):
    limit: int = Field(default=50, ge=1, le=100)
    levels: tuple[LogLevel, ...] = (LogLevel.ERROR, LogLevel.WARNING)

    @field_validator("levels")
    @classmethod
    def require_levels(cls, value: tuple[LogLevel, ...]) -> tuple[LogLevel, ...]:
        if not value:
            raise ValueError("at least one log level is required")
        return value


class LogEntry(StrictModel):
    timestamp: datetime
    level: LogLevel
    message: str = Field(max_length=2048)
    trace_id: str | None = Field(default=None, max_length=128)
    status_code: int | None = Field(default=None, ge=100, le=599)
    version: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
    )


class LogsResult(StrictModel):
    project_id: str
    environment: str
    service: str
    entries: list[LogEntry] = Field(max_length=100)
    returned_bytes: int = Field(ge=0, le=20_480)
    truncated: bool
    source_status: Literal["available", "not_configured"]


class ServiceHealthRequest(TimeWindowRequest):
    limit: int = Field(default=1, ge=1, le=1)


class ServiceHealthResult(StrictModel):
    project_id: str
    environment: str
    service: str
    status: Literal["healthy", "unhealthy", "unknown"]
    checked_at: datetime
    detail: str = Field(max_length=1024)


class ActiveAlertsRequest(TimeWindowRequest):
    limit: int = Field(default=20, ge=1, le=100)


class ActiveAlert(StrictModel):
    name: str
    severity: str
    status: str
    starts_at: datetime | None = None
    summary: str = Field(default="", max_length=2048)


class ActiveAlertsResult(StrictModel):
    project_id: str
    environment: str
    service: str
    alerts: list[ActiveAlert] = Field(max_length=100)
    observed_at: datetime


class CurrentReleaseRequest(ScopedRequest):
    pass


class RecentReleasesRequest(TimeWindowRequest):
    limit: int = Field(default=10, ge=1, le=50)


class ReleaseRecord(StrictModel):
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    released_at: datetime
    released_by: str = Field(max_length=128)
    change_summary: str = Field(max_length=4096)


class CurrentReleaseResult(StrictModel):
    project_id: str
    environment: str
    service: str
    release: ReleaseRecord


class RecentReleasesResult(StrictModel):
    project_id: str
    environment: str
    service: str
    releases: list[ReleaseRecord] = Field(max_length=50)


class ReleaseDiffRequest(ScopedRequest):
    from_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    to_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ReleaseDiffResult(StrictModel):
    project_id: str
    environment: str
    service: str
    from_version: str
    to_version: str
    summary: str = Field(max_length=8192)


class FailureCategory(StrEnum):
    RETRYABLE = "RETRYABLE"
    NEED_HUMAN = "NEED_HUMAN"
    TERMINAL = "TERMINAL"


class ToolAuditContext(StrictModel):
    incident_id: str | None = None
    graph_run_id: str | None = None
    agent_name: str | None = None


class ToolResult(StrictModel):
    tool_call_id: str
    server: Literal["observability", "release"]
    tool_name: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)
    audit: ToolAuditContext
    truncated: bool
    data: dict[str, Any]
