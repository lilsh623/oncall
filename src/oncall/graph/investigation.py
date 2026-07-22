"""Bounded Supervisor/Investigation/Knowledge subgraph."""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from oncall.agents.investigation import run_demo_investigation, run_investigation_agent
from oncall.agents.knowledge import run_knowledge_agent
from oncall.agents.supervisor import decide_next_step, draft_diagnosis
from oncall.graph.contracts import EvidenceItem, IncidentGraphState


class State(TypedDict, total=False):
    incident_id: str
    graph_run_id: str | None
    status: str
    project_id: str
    environment: str
    service: str
    alert_summary: str
    created_at: Any
    evidence: list[Any]
    hypotheses: list[Any]
    knowledge_citations: list[Any]
    selected_skill: str | None
    diagnosis: Any
    action_plan: Any
    approval: dict[str, Any] | None
    execution_result: dict[str, Any] | None
    verification_result: dict[str, Any] | None
    investigation_rounds: int
    model_call_count: int
    no_progress_rounds: int
    verification_failures: int
    need_human_reason: str | None
    supervisor_next_step: str
    supervisor_rationale: str


def _validated(state: State) -> IncidentGraphState:
    fields = IncidentGraphState.model_fields
    return IncidentGraphState.model_validate(
        {key: value for key, value in state.items() if key in fields}
    )


def _usable_evidence(items: list[EvidenceItem]) -> int:
    """Count only successful structured observations, never safe error envelopes."""

    return sum(
        1
        for item in items
        if isinstance(item.payload, dict)
        and (
            isinstance(item.payload.get("data"), dict)
            or {
                "current_version",
                "previous_healthy_version",
            }.issubset(item.payload)
        )
    )


async def _supervise(state: State, *, live_mode: bool) -> State:
    current = _validated(state)
    decision, calls = await decide_next_step(
        current,
        live_mode=live_mode,
        evidence_count=_usable_evidence(current.evidence),
        citation_count=len(current.knowledge_citations),
    )
    return {
        "supervisor_next_step": decision.next_step,
        "supervisor_rationale": decision.rationale,
        "model_call_count": current.model_call_count + calls,
    }


def _route(state: State) -> str:
    return str(state.get("supervisor_next_step", "request_human"))


async def _investigate(state: State, *, live_mode: bool) -> State:
    current = _validated(state)
    try:
        result = (
            await run_investigation_agent(current)
            if live_mode
            else run_demo_investigation(current)
        )
    except Exception:
        # A failure to collect evidence is intentionally not turned into fabricated
        # observations or hypotheses. The bounded no-progress guard will escalate.
        result = {"evidence": [], "hypotheses": [], "model_call_count": 0}

    collected = list(result["evidence"])
    usable_count = _usable_evidence(collected)
    return {
        "evidence": [
            *(item.model_dump(mode="json") for item in current.evidence),
            *(item.model_dump(mode="json") for item in collected),
        ],
        "hypotheses": [
            *(item.model_dump(mode="json") for item in current.hypotheses),
            *(
                item.model_dump(mode="json")
                for item in result["hypotheses"]
                if usable_count > 0
            ),
        ],
        "investigation_rounds": current.investigation_rounds + 1,
        "no_progress_rounds": 0 if usable_count else current.no_progress_rounds + 1,
        "model_call_count": current.model_call_count + int(result["model_call_count"]),
    }


async def _knowledge(state: State, *, live_mode: bool) -> State:
    current = _validated(state)
    try:
        result = await asyncio.to_thread(
            run_knowledge_agent,
            current,
            allow_offline_fallback=not live_mode,
        )
    except Exception:
        result = {"knowledge_citations": [], "model_call_count": 0}
    citations = list(result["knowledge_citations"])
    return {
        "knowledge_citations": [
            *(item.model_dump(mode="json") for item in current.knowledge_citations),
            *(item.model_dump(mode="json") for item in citations),
        ],
        "no_progress_rounds": 0 if citations else current.no_progress_rounds + 1,
        "model_call_count": current.model_call_count + int(result["model_call_count"]),
    }


async def _diagnose(state: State, *, live_mode: bool) -> State:
    current = _validated(state)
    diagnosis, calls, reason = await draft_diagnosis(current, live_mode=live_mode)
    if diagnosis is None:
        return {
            "status": "NEED_HUMAN",
            "need_human_reason": reason or "The graph could not produce a safe diagnosis.",
            "model_call_count": current.model_call_count + calls,
        }
    return {
        "diagnosis": diagnosis.model_dump(mode="json"),
        "status": "DIAGNOSED",
        "model_call_count": current.model_call_count + calls,
    }


def _need_human(state: State) -> State:
    return {
        "status": "NEED_HUMAN",
        "need_human_reason": state.get("need_human_reason")
        or state.get("supervisor_rationale")
        or "Graph requires human review.",
    }


def build_investigation_graph(*, live_mode: bool = False):
    """Build a bounded subgraph.

    ``live_mode`` is deliberately opt-in: contract tests can use deterministic
    fixtures, while production callers must opt in to MCP/RAG/Bailian access.
    """

    async def supervisor_node(state: State) -> State:
        return await _supervise(state, live_mode=live_mode)

    async def investigation_node(state: State) -> State:
        return await _investigate(state, live_mode=live_mode)

    async def knowledge_node(state: State) -> State:
        return await _knowledge(state, live_mode=live_mode)

    async def diagnosis_node(state: State) -> State:
        return await _diagnose(state, live_mode=live_mode)

    graph = StateGraph(State)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("call_investigation", investigation_node)
    graph.add_node("call_knowledge", knowledge_node)
    graph.add_node("submit_diagnosis", diagnosis_node)
    graph.add_node("request_human", _need_human)
    graph.set_entry_point("supervisor")
    graph.add_conditional_edges(
        "supervisor",
        _route,
        {
            "call_investigation": "call_investigation",
            "call_knowledge": "call_knowledge",
            "submit_diagnosis": "submit_diagnosis",
            "request_human": "request_human",
        },
    )
    graph.add_edge("call_investigation", "supervisor")
    graph.add_edge("call_knowledge", "supervisor")
    graph.add_edge("submit_diagnosis", END)
    graph.add_edge("request_human", END)
    return graph.compile()
