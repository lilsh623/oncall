"""Controlled Supervisor decisions for the investigation subgraph."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SupervisorStep = Literal[
    "call_investigation",
    "call_knowledge",
    "submit_diagnosis",
    "request_human",
]


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_step: SupervisorStep
    rationale: str = Field(max_length=2048)


def decide_next_step(*, evidence_count: int, citation_count: int, rounds: int) -> SupervisorDecision:
    """Choose only bounded read/inference steps; recovery tools are intentionally absent."""

    if rounds >= 4 and (evidence_count == 0 or citation_count == 0):
        return SupervisorDecision(
            next_step="request_human",
            rationale="Investigation budget exhausted before enough evidence was collected.",
        )
    if evidence_count == 0:
        return SupervisorDecision(
            next_step="call_investigation",
            rationale="Collect read-only metrics, logs, health, alerts, and release evidence.",
        )
    if citation_count == 0:
        return SupervisorDecision(
            next_step="call_knowledge",
            rationale="Retrieve approved SOP references before diagnosis.",
        )
    return SupervisorDecision(
        next_step="submit_diagnosis",
        rationale="Evidence and knowledge are sufficient for V1 post-deployment regression.",
    )
