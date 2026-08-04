"""Public Agent evaluation contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EvaluationRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["offline", "online"] = "offline"


class EvaluationMetrics(BaseModel):
    task_success_rate: float
    tool_call_success_rate: float
    rag_hit_rate: float
    end_to_end_resolution_rate: float
    evaluated_cases: int
    passed_cases: int


class EvaluationCaseView(BaseModel):
    id: UUID
    case_id: str
    category: str
    passed: bool
    input: dict[str, Any]
    expected: dict[str, Any]
    actual: dict[str, Any]
    scores: dict[str, Any]
    duration_ms: int
    error: str | None


class EvaluationRunView(BaseModel):
    id: UUID
    mode: str
    dataset_name: str
    dataset_version: str
    status: str
    case_count: int
    passed_count: int
    metrics: EvaluationMetrics
    started_at: datetime
    completed_at: datetime | None
    error: str | None
    created_at: datetime


class EvaluationRunDetail(EvaluationRunView):
    cases: list[EvaluationCaseView]
