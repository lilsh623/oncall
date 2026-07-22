"""Investigation subgraph orchestration."""

from __future__ import annotations

from typing import TypedDict
from typing import Any

from langgraph.graph import END, StateGraph

from oncall.agents.investigation import run_stub_investigation
from oncall.agents.knowledge import run_knowledge_agent
from oncall.agents.supervisor import decide_next_step
from oncall.graph.contracts import DiagnosisDraft, IncidentGraphState


class State(TypedDict, total=False):
    incident_id: str
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
    verification_failures: int
    need_human_reason: str | None
    supervisor_next_step: str
    supervisor_rationale: str


def _validated(state: State) -> IncidentGraphState:
    fields = IncidentGraphState.model_fields
    return IncidentGraphState.model_validate(
        {key: value for key, value in state.items() if key in fields}
    )


def _supervise(state: State) -> State:
    current = _validated(state)
    decision = decide_next_step(
        evidence_count=len(current.evidence),
        citation_count=len(current.knowledge_citations),
        rounds=current.investigation_rounds,
    )
    return {"supervisor_next_step": decision.next_step, "supervisor_rationale": decision.rationale}


def _route(state: State) -> str:
    return str(state.get("supervisor_next_step", "request_human"))


def _investigate(state: State) -> State:
    current = _validated(state)
    result = run_stub_investigation(current)
    return {
        "evidence": [
            *(item.model_dump(mode="json") for item in current.evidence),
            *(item.model_dump(mode="json") for item in result["evidence"]),
        ],
        "hypotheses": [
            *(item.model_dump(mode="json") for item in current.hypotheses),
            *(item.model_dump(mode="json") for item in result["hypotheses"]),
        ],
        "investigation_rounds": current.investigation_rounds + 1,
        "model_call_count": current.model_call_count + int(result["model_call_count"]),
    }


def _knowledge(state: State) -> State:
    current = _validated(state)
    result = run_knowledge_agent(current)
    return {
        "knowledge_citations": [
            *(item.model_dump(mode="json") for item in current.knowledge_citations),
            *(item.model_dump(mode="json") for item in result["knowledge_citations"]),
        ],
        "model_call_count": current.model_call_count + int(result["model_call_count"]),
    }


def _diagnose(state: State) -> State:
    current = _validated(state)
    diagnosis = DiagnosisDraft(
        root_cause="The latest order-api release likely caused a post-deployment HTTP error regression.",
        confidence=max((item.confidence for item in current.hypotheses), default=0.7),
        summary="Read-only investigation and SOP retrieval support preparing a rollback plan.",
    )
    return {"diagnosis": diagnosis.model_dump(mode="json"), "status": "DIAGNOSED"}


def _need_human(state: State) -> State:
    return {"status": "NEED_HUMAN", "need_human_reason": state.get("supervisor_rationale")}


def build_investigation_graph():
    """Build the bounded Supervisor/Investigation/Knowledge subgraph."""

    graph = StateGraph(State)
    graph.add_node("supervisor", _supervise)
    graph.add_node("call_investigation", _investigate)
    graph.add_node("call_knowledge", _knowledge)
    graph.add_node("submit_diagnosis", _diagnose)
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
