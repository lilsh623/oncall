"""Policy contracts for human-approved remediation plans."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason_code: str
    message: str = Field(max_length=2048)
