"""Public contracts for reviewed experience memory."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


CandidateStatus = Literal["PENDING_REVIEW", "PUBLISHED", "REJECTED"]


class ExperienceCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    incident_id: UUID
    project_id: str
    environment: str
    service: str
    pattern_fingerprint: str
    content_hash: str
    title: str
    summary: str
    symptoms: list[Any]
    root_cause: str
    action: dict[str, Any]
    verification: list[Any]
    warnings: list[Any]
    source_refs: dict[str, Any]
    confidence: float
    redaction_version: str
    status: CandidateStatus
    duplicate_of_id: UUID | None
    reviewed_by: UUID | None
    review_comment: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExperienceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    candidate_id: UUID
    project_id: str
    environment: str
    service: str
    pattern_fingerprint: str
    content_hash: str
    version: int
    title: str
    content: dict[str, Any]
    source_incident_ids: list[Any]
    published_by: UUID | None
    published_at: datetime
    status: str


class ReviewRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=4096)


class SkillCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    experience_id: UUID
    project_id: str
    environment: str
    service: str
    skill_name: str
    proposed_version: str
    manifest: dict[str, Any]
    instructions: str
    content_hash: str
    source_experience_ids: list[Any]
    status: str
    reviewed_by: UUID | None
    review_comment: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SkillVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    candidate_id: UUID
    project_id: str
    environment: str
    service: str
    skill_name: str
    version: str
    manifest: dict[str, Any]
    instructions: str
    content_hash: str
    source_experience_ids: list[Any]
    status: str
    published_by: UUID | None
    published_at: datetime
