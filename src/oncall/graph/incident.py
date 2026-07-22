"""Outer deterministic Incident graph."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from oncall.agents.remediation import plan_remediation
from oncall.graph.contracts import IncidentGraphState
from oncall.graph.investigation import build_investigation_graph


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


def _validated(state: State) -> IncidentGraphState:
    return IncidentGraphState.model_validate(state)


def _enter_investigation(state: State) -> State:
    current = _validated(state)
    return {"status": "INVESTIGATING", "investigation_rounds": current.investigation_rounds}


def _run_investigation(state: State) -> State:
    subgraph = build_investigation_graph()
    return subgraph.invoke(state)


def _route_after_investigation(state: State) -> str:
    if state.get("status") == "NEED_HUMAN":
        return "need_human"
    if state.get("diagnosis"):
        return "plan"
    return "need_human"


def _plan(state: State) -> State:
    current = _validated({**state, "status": "PLANNING"})
    return {
        "status": "WAITING_APPROVAL",
        "action_plan": plan_remediation(current).model_dump(mode="json"),
    }


def _need_human(state: State) -> State:
    return {"status": "NEED_HUMAN", "need_human_reason": state.get("need_human_reason") or "Graph requires human review."}


def build_incident_graph(checkpointer: Any | None = None):
    """Build the V1 Incident graph up to the human approval interrupt boundary."""

    graph = StateGraph(State)
    graph.add_node("enter_investigation", _enter_investigation)
    graph.add_node("investigate", _run_investigation)
    graph.add_node("plan", _plan)
    graph.add_node("need_human", _need_human)
    graph.set_entry_point("enter_investigation")
    graph.add_edge("enter_investigation", "investigate")
    graph.add_conditional_edges(
        "investigate",
        _route_after_investigation,
        {"plan": "plan", "need_human": "need_human"},
    )
    graph.add_edge("plan", END)
    graph.add_edge("need_human", END)
    if checkpointer is None:
        return graph.compile()
    return graph.compile(checkpointer=checkpointer)
