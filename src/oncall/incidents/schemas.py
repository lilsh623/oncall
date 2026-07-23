"""Public, bounded Incident query contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IncidentListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str
    project_id: str
    environment: str
    service: str
    status: str
    severity: str | None = None
    opened_at: datetime
    updated_at: datetime


class IncidentListResponse(BaseModel):
    items: list[IncidentListItem]
    limit: int
    offset: int


class AuditEventSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    event_type: str
    actor: str
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class IncidentDetail(BaseModel):
    """Aggregated details without model transcripts or raw log entries."""

    model_config = ConfigDict(extra="forbid")

    overview: dict[str, Any]
    alerts: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    knowledge_citations: list[dict[str, Any]]
    action_plans: list[dict[str, Any]]
    approvals: list[dict[str, Any]]
    executions: list[dict[str, Any]]
    verification: list[dict[str, Any]]
    audit_events: list[AuditEventSummary]
