"""Typed contracts for deterministic Incident graph execution."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictGraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceItem(StrictGraphModel):
    source_type: Literal["mcp", "rag", "skill", "system"]
    source_ref: str = Field(max_length=1024)
    observation: str = Field(max_length=4096)
    payload: dict[str, Any] = Field(default_factory=dict)


class HypothesisDraft(StrictGraphModel):
    description: str = Field(max_length=4096)
    confidence: float = Field(ge=0, le=1)
    supporting_summary: str = Field(max_length=4096)
    opposing_summary: str | None = Field(default=None, max_length=4096)
    next_check: str | None = Field(default=None, max_length=2048)


class KnowledgeCitationDraft(StrictGraphModel):
    document_id: str = Field(max_length=256)
    document_version: str | None = Field(default=None, max_length=128)
    section: str | None = Field(default=None, max_length=512)
    file_path: str | None = Field(default=None, max_length=1024)
    locator: str | None = Field(default=None, max_length=512)
    excerpt: str | None = Field(default=None, max_length=2048)
    score: float | None = Field(default=None, ge=0)


class DiagnosisDraft(StrictGraphModel):
    root_cause: str = Field(max_length=4096)
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(max_length=4096)


class RollbackReleaseAction(StrictGraphModel):
    action_type: Literal["rollback_release"] = "rollback_release"
    project_id: str
    environment: str
    service: str
    current_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    target_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ActionPlanDraft(StrictGraphModel):
    summary: str = Field(max_length=4096)
    risk_level: Literal["low", "medium", "high"]
    prerequisites: list[str] = Field(default_factory=list, max_length=20)
    rollback: RollbackReleaseAction
    verification_criteria: list[str] = Field(default_factory=list, max_length=20)
    plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def finalize_plan(self) -> "ActionPlanDraft":
        if self.rollback.action_type != "rollback_release":
            raise ValueError("V1 action plan only allows rollback_release")
        object.__setattr__(self, "plan_hash", self._stable_hash())
        return self

    def _stable_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"plan_hash"})
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class IncidentGraphState(StrictGraphModel):
    incident_id: str
    graph_run_id: str | None = None
    status: str
    project_id: str
    environment: str
    service: str
    alert_summary: str = Field(max_length=4096)
    created_at: datetime
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=100)
    hypotheses: list[HypothesisDraft] = Field(default_factory=list, max_length=20)
    knowledge_citations: list[KnowledgeCitationDraft] = Field(default_factory=list, max_length=20)
    selected_skill: str | None = None
    diagnosis: DiagnosisDraft | None = None
    action_plan: ActionPlanDraft | None = None
    approval: dict[str, Any] | None = None
    execution_result: dict[str, Any] | None = None
    verification_result: dict[str, Any] | None = None
    investigation_rounds: int = Field(default=0, ge=0, le=4)
    model_call_count: int = Field(default=0, ge=0, le=64)
    no_progress_rounds: int = Field(default=0, ge=0, le=2)
    verification_failures: int = Field(default=0, ge=0, le=1)
    need_human_reason: str | None = None
