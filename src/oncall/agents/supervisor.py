"""Controlled Supervisor decisions for the investigation subgraph."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from oncall.agents.model_factory import create_chat_model
from oncall.graph.contracts import DiagnosisDraft, IncidentGraphState


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


def _deterministic_decision(
    *,
    evidence_count: int,
    citation_count: int,
    rounds: int,
    no_progress_rounds: int,
    model_call_count: int,
) -> SupervisorDecision:
    """Choose only bounded read/inference steps; recovery tools are intentionally absent."""

    if model_call_count >= 12:
        return SupervisorDecision(
            next_step="request_human",
            rationale="Model-call budget is exhausted; a human must review the incident.",
        )
    if no_progress_rounds >= 2:
        return SupervisorDecision(
            next_step="request_human",
            rationale="Two bounded evidence or knowledge attempts made no progress.",
        )
    if rounds >= 4 and (evidence_count == 0 or citation_count == 0):
        return SupervisorDecision(
            next_step="request_human",
            rationale="Investigation budget exhausted before enough evidence was collected.",
        )
    if evidence_count == 0:
        return SupervisorDecision(
            next_step="call_investigation",
            rationale="Collect scoped read-only evidence from Tencent CLS logs.",
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


def _compact_context(state: IncidentGraphState) -> str:
    """Bound untrusted MCP/RAG data before it is sent to the Supervisor model."""

    evidence = [
        {
            "source": item.source_ref,
            "observation": item.observation[:512],
        }
        for item in state.evidence[-10:]
    ]
    citations = [
        {
            "document_id": item.document_id,
            "section": item.section,
            "excerpt": (item.excerpt or "")[:512],
        }
        for item in state.knowledge_citations[-5:]
    ]
    return json.dumps(
        {
            "incident": {
                "id": state.incident_id,
                "project_id": state.project_id,
                "environment": state.environment,
                "service": state.service,
                "alert_summary": state.alert_summary[:1024],
            },
            "budget": {
                "investigation_rounds": state.investigation_rounds,
                "model_call_count": state.model_call_count,
                "no_progress_rounds": state.no_progress_rounds,
            },
            "evidence": evidence,
            "knowledge_citations": citations,
        },
        ensure_ascii=False,
    )


def _guard_decision(
    decision: SupervisorDecision,
    *,
    evidence_count: int,
    citation_count: int,
    rounds: int,
    no_progress_rounds: int,
    model_call_count: int,
) -> SupervisorDecision:
    """Keep semantic model choices inside deterministic safety and budget gates."""

    baseline = _deterministic_decision(
        evidence_count=evidence_count,
        citation_count=citation_count,
        rounds=rounds,
        no_progress_rounds=no_progress_rounds,
        model_call_count=model_call_count,
    )
    if baseline.next_step == "request_human":
        return baseline
    if decision.next_step == "submit_diagnosis" and (evidence_count == 0 or citation_count == 0):
        return baseline
    if (
        decision.next_step in {"call_investigation", "call_knowledge"}
        and evidence_count > 0
        and citation_count > 0
    ):
        # Read-only evidence and an approved SOP citation are already in hand.
        # Honor the deterministic baseline (submit_diagnosis) rather than letting
        # the model spend the remaining rounds re-investigating until the budget
        # is exhausted and the incident falls to NEED_HUMAN.
        return baseline
    if decision.next_step == "call_investigation" and rounds >= 4:
        return SupervisorDecision(
            next_step="request_human",
            rationale="Investigation-round budget is exhausted.",
        )
    return decision


async def decide_next_step(
    state: IncidentGraphState,
    *,
    live_mode: bool,
    evidence_count: int,
    citation_count: int,
) -> tuple[SupervisorDecision, int]:
    """Choose a constrained next step; live mode uses Bailian structured output."""

    deterministic = _deterministic_decision(
        evidence_count=evidence_count,
        citation_count=citation_count,
        rounds=state.investigation_rounds,
        no_progress_rounds=state.no_progress_rounds,
        model_call_count=state.model_call_count,
    )
    if not live_mode or deterministic.next_step == "request_human":
        return deterministic, 0

    try:
        model = create_chat_model("supervisor").with_structured_output(SupervisorDecision)
        response = await model.ainvoke(
            [
                (
                    "system",
                    "You are a constrained OnCall investigation supervisor. "
                    "Choose exactly one allowed next_step: call_investigation, "
                    "call_knowledge, submit_diagnosis, or request_human. "
                    "You cannot execute remediation or request write tools. "
                    "Do not diagnose without both read-only evidence and an approved SOP citation.",
                ),
                ("human", _compact_context(state)),
            ]
        )
        decision = SupervisorDecision.model_validate(response)
    except Exception:
        return (
            SupervisorDecision(
                next_step="request_human",
                rationale="Supervisor model was unavailable or returned an invalid structured decision.",
            ),
            1,
        )
    return (
        _guard_decision(
            decision,
            evidence_count=evidence_count,
            citation_count=citation_count,
            rounds=state.investigation_rounds,
            no_progress_rounds=state.no_progress_rounds,
            model_call_count=state.model_call_count + 1,
        ),
        1,
    )


async def draft_diagnosis(
    state: IncidentGraphState,
    *,
    live_mode: bool,
) -> tuple[DiagnosisDraft | None, int, str | None]:
    """Turn bounded evidence into a structured diagnosis, never an execution command."""

    if not state.evidence or not state.knowledge_citations:
        return None, 0, "Diagnosis requires read-only evidence and an approved SOP citation."
    if not live_mode:
        return (
            DiagnosisDraft(
                root_cause="The latest Tencent Cloud service release likely caused a post-deployment error regression.",
                confidence=max((item.confidence for item in state.hypotheses), default=0.7),
                summary="Read-only investigation and SOP retrieval support preparing a rollback plan.",
            ),
            0,
            None,
        )
    if state.model_call_count >= 12:
        return None, 0, "Model-call budget is exhausted before diagnosis."
    try:
        model = create_chat_model("supervisor").with_structured_output(DiagnosisDraft)
        response = await model.ainvoke(
            [
                (
                    "system",
                    "Draft a concise root-cause diagnosis from the supplied read-only evidence and SOP citations. "
                    "Do not propose actions, tools, shell commands, or recovery execution.",
                ),
                ("human", _compact_context(state)),
            ]
        )
        return DiagnosisDraft.model_validate(response), 1, None
    except Exception:
        return None, 1, "Diagnosis model was unavailable or returned invalid structured output."
