"""Outer deterministic Incident graph, including the human approval interrupt."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from oncall.agents.remediation import plan_remediation
from oncall.graph.contracts import IncidentGraphState
from oncall.graph.investigation import build_investigation_graph


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


def _validated(state: State) -> IncidentGraphState:
    return IncidentGraphState.model_validate(state)


def _enter_investigation(state: State) -> State:
    current = _validated(state)
    return {"status": "INVESTIGATING", "investigation_rounds": current.investigation_rounds}


async def _run_investigation(state: State, *, live_mode: bool) -> State:
    subgraph = build_investigation_graph(live_mode=live_mode)
    return await subgraph.ainvoke(state)


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


def _approval_boundary(state: State) -> State:
    """Persisted LangGraph interrupt; only API-provided approval can resume it."""

    current = _validated(state)
    if current.action_plan is None:
        return {
            "status": "NEED_HUMAN",
            "need_human_reason": "No action plan exists at the approval boundary.",
        }
    response = interrupt(
        {
            "kind": "approval_required",
            "incident_id": current.incident_id,
            "action_plan_hash": current.action_plan.plan_hash,
            "summary": current.action_plan.summary,
            "risk_level": current.action_plan.risk_level,
        }
    )
    if not isinstance(response, dict) or response.get("decision") != "APPROVED":
        return {
            "status": "NEED_HUMAN",
            "need_human_reason": "The remediation plan was not approved by an authorized user.",
        }
    return {"approval": response, "status": "EXECUTING"}


def _need_human(state: State) -> State:
    return {
        "status": "NEED_HUMAN",
        "need_human_reason": state.get("need_human_reason") or "Graph requires human review.",
    }


def build_incident_graph(checkpointer: Any | None = None, *, live_mode: bool = False):
    """Build the graph through the persisted approval boundary.

    A checkpointer is required by live callers so that an API approval can safely
    resume exactly the interrupted graph run. Offline contract tests intentionally
    use no checkpointer and only inspect the pre-approval state.
    """

    async def investigation_node(state: State) -> State:
        return await _run_investigation(state, live_mode=live_mode)

    graph = StateGraph(State)
    graph.add_node("enter_investigation", _enter_investigation)
    graph.add_node("investigate", investigation_node)
    graph.add_node("plan", _plan)
    graph.add_node("need_human", _need_human)
    graph.set_entry_point("enter_investigation")
    graph.add_edge("enter_investigation", "investigate")
    graph.add_conditional_edges(
        "investigate",
        _route_after_investigation,
        {"plan": "plan", "need_human": "need_human"},
    )
    if checkpointer is None:
        # Offline contract tests inspect the safe pre-approval state. Real runs
        # always provide PostgreSQL checkpointing and therefore use interrupt().
        graph.add_edge("plan", END)
    else:
        graph.add_node("await_approval", _approval_boundary)
        graph.add_edge("plan", "await_approval")
        graph.add_edge("await_approval", END)
    graph.add_edge("need_human", END)
    if checkpointer is None:
        return graph.compile()
    return graph.compile(checkpointer=checkpointer)
