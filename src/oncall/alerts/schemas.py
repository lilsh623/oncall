"""Public contracts for unified alert ingestion."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AlertEnvelope(BaseModel):
    """Source-independent, validated alert fact used by the Alert Hub."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    project_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=64)
    service: str = Field(min_length=1, max_length=128)
    alert_name: str = Field(min_length=1, max_length=256)
    severity: str = Field(min_length=1, max_length=32)
    status: Literal["firing", "resolved"]
    starts_at: datetime
    ends_at: datetime | None = None
    fingerprint: str = Field(min_length=1, max_length=128)
    correlation_key: str = Field(min_length=1, max_length=256)
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)

    @field_validator("starts_at", "ends_at", mode="after")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        """Require an offset and normalize all timestamps to UTC."""

        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("alert timestamps must include a timezone offset")
        return value.astimezone(UTC)


class AlertIngestionCommand(BaseModel):
    """Authenticated project context plus the unmodified vendor payload."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]
    stable_label_names: tuple[str, ...] = ()


class IngestionResult(BaseModel):
    """Persisted result returned before background investigation starts."""

    accepted: bool = True
    incident_ids: list[UUID]
    deduplicated: int = 0


class AlertListItem(BaseModel):
    """A bounded alert summary for the authenticated operations console."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    incident_id: UUID | None = None
    source: str
    project_id: str
    environment: str
    service: str
    alert_name: str
    severity: str
    status: Literal["firing", "resolved"]
    summary: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    last_seen: datetime
    occurrence_count: int


class AlertListResponse(BaseModel):
    """Paginated alert inbox response."""

    items: list[AlertListItem]
    total: int
    limit: int
    offset: int
