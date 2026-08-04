"""LangGraph adapter around the OpenAI Agents SDK intelligence loop."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from oncall.agents.openai_loop import run_incident_agent_loop
from oncall.graph.contracts import IncidentGraphState


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


async def _run_loop(state: State, *, live_mode: bool) -> State:
    current = _validated(state)
    return await run_incident_agent_loop(current, live_mode=live_mode)


def build_investigation_graph(*, live_mode: bool = False):
    """Compile one bounded intelligence node.

    The nested OpenAI Agents SDK Runner owns investigation, diagnosis, and plan
    drafting. LangGraph remains responsible only for durable lifecycle control.
    """

    async def agent_loop_node(state: State) -> State:
        return await _run_loop(state, live_mode=live_mode)

    graph = StateGraph(State)
    graph.add_node("openai_agent_loop", agent_loop_node)
    graph.set_entry_point("openai_agent_loop")
    graph.add_edge("openai_agent_loop", END)
    return graph.compile()
