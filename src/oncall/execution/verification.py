"""Recovery verification helpers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    checks: list[dict[str, object]] = Field(default_factory=list)
    reason: str | None = None


def verify_recovery(criteria: list[str]) -> VerificationResult:
    """Return a deterministic placeholder until live Prometheus wiring runs."""

    return VerificationResult(
        passed=False,
        checks=[{"criterion": criterion, "status": "pending"} for criterion in criteria],
        reason="live verification requires Recovery MCP execution",
    )
