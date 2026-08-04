"""Outer deterministic Incident graph, including the human approval interrupt."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from oncall.execution.service import ExecutionResult, execute_approved_plan
from oncall.execution.workflow import (
    RecoveryVerificationOutcome,
    verify_executed_plan,
)
from oncall.graph.contracts import IncidentGraphState
from oncall.graph.investigation import build_investigation_graph
from oncall.incidents.state import IncidentStatus
from oncall.metrics import RECOVERY_EXECUTIONS, RECOVERY_VERIFICATION


ExecutePlan = Callable[[UUID, UUID], Awaitable[ExecutionResult]]
VerifyPlan = Callable[[UUID, UUID, UUID], Awaitable[RecoveryVerificationOutcome]]


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
    if state.get("diagnosis") and state.get("action_plan"):
        return "plan"
    return "need_human"


def _plan(state: State) -> State:
    current = _validated({**state, "status": "PLANNING"})
    if current.diagnosis is None or current.action_plan is None:
        return {
            "status": "NEED_HUMAN",
            "need_human_reason": "The intelligence loop did not produce a complete plan.",
        }
    return {
        "status": "WAITING_APPROVAL",
        # The Agents SDK loop already drafted and validated this plan. This node
        # only crosses the deterministic persistence/approval boundary.
        "action_plan": current.action_plan.model_dump(mode="json"),
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
    if response.get("action_plan_hash") != current.action_plan.plan_hash:
        return {
            "status": "NEED_HUMAN",
            "need_human_reason": "The approval does not match the interrupted action plan.",
        }
    try:
        UUID(str(response["approval_id"]))
        UUID(str(response["action_plan_id"]))
    except (KeyError, TypeError, ValueError):
        return {
            "status": "NEED_HUMAN",
            "need_human_reason": "The approval is missing valid durable identifiers.",
        }
    return {"approval": response, "status": "EXECUTING"}


def _approved_ids(state: IncidentGraphState) -> tuple[UUID, UUID]:
    approval = state.approval or {}
    if (
        state.action_plan is None
        or approval.get("decision") != "APPROVED"
        or approval.get("action_plan_hash") != state.action_plan.plan_hash
    ):
        raise ValueError("approval does not match the graph action plan")
    return UUID(state.incident_id), UUID(str(approval["action_plan_id"]))


def _route_after_approval(state: State) -> str:
    if state.get("status") == IncidentStatus.EXECUTING.value:
        return "execute"
    return "need_human"


async def _execute(
    state: State,
    *,
    execute_plan: ExecutePlan,
) -> State:
    """Execute only the fixed, approved plan through the idempotent service."""

    current = _validated(state)
    try:
        incident_id, plan_id = _approved_ids(current)
        result = await execute_plan(incident_id, plan_id)
    except Exception as exc:
        return {
            "status": IncidentStatus.NEED_HUMAN.value,
            "need_human_reason": (
                "The approved recovery could not execute safely: " + type(exc).__name__
            ),
        }
    RECOVERY_EXECUTIONS.labels(status=result.status).inc()
    payload = result.model_dump(mode="json")
    if result.status != "SUCCEEDED" or result.execution_id is None:
        return {
            "status": IncidentStatus.NEED_HUMAN.value,
            "execution_result": payload,
            "need_human_reason": result.error
            or f"Recovery execution ended as {result.status}.",
        }
    return {
        "status": IncidentStatus.VERIFYING.value,
        "execution_result": payload,
        "need_human_reason": None,
    }


def _route_after_execution(state: State) -> str:
    result = state.get("execution_result")
    if (
        state.get("status") == IncidentStatus.VERIFYING.value
        and isinstance(result, dict)
        and result.get("status") == "SUCCEEDED"
        and result.get("execution_id")
    ):
        return "verify"
    return "need_human"


async def _verify(
    state: State,
    *,
    verify_plan: VerifyPlan,
) -> State:
    """Run and persist read-only recovery verification as a graph node."""

    current = _validated(state)
    try:
        incident_id, plan_id = _approved_ids(current)
        execution_id = UUID(str((current.execution_result or {})["execution_id"]))
        result = await verify_plan(incident_id, plan_id, execution_id)
    except Exception as exc:
        return {
            "status": IncidentStatus.NEED_HUMAN.value,
            "need_human_reason": (
                "Recovery verification could not complete safely: " + type(exc).__name__
            ),
        }
    RECOVERY_VERIFICATION.labels(passed=str(result.passed).lower()).inc()
    payload = result.model_dump(mode="json")
    if result.status != IncidentStatus.RESOLVED.value or not result.passed:
        return {
            "status": IncidentStatus.NEED_HUMAN.value,
            "verification_result": payload,
            "verification_failures": min(1, current.verification_failures + 1),
            "need_human_reason": result.reason
            or "Recovery verification did not pass.",
        }
    return {
        "status": IncidentStatus.RESOLVED.value,
        "verification_result": payload,
        "need_human_reason": None,
    }


def _route_after_verification(state: State) -> str:
    if state.get("status") == IncidentStatus.RESOLVED.value:
        return "resolved"
    return "need_human"


def _resolved(_: State) -> State:
    return {"status": IncidentStatus.RESOLVED.value, "need_human_reason": None}


def _need_human(state: State) -> State:
    return {
        "status": "NEED_HUMAN",
        "need_human_reason": state.get("need_human_reason") or "Graph requires human review.",
    }


def build_incident_graph(
    checkpointer: Any | None = None,
    *,
    live_mode: bool = False,
    execute_plan: ExecutePlan = execute_approved_plan,
    verify_plan: VerifyPlan = verify_executed_plan,
):
    """Build the durable graph from investigation through recovery verification.

    A checkpointer is required by live callers so that an API approval can safely
    resume exactly the interrupted graph run. Offline contract tests intentionally
    use no checkpointer and only inspect the pre-approval state.
    """

    async def investigation_node(state: State) -> State:
        return await _run_investigation(state, live_mode=live_mode)

    async def execution_node(state: State) -> State:
        return await _execute(state, execute_plan=execute_plan)

    async def verification_node(state: State) -> State:
        return await _verify(state, verify_plan=verify_plan)

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
        graph.add_node("execute", execution_node)
        graph.add_node("verify", verification_node)
        graph.add_node("resolved", _resolved)
        graph.add_edge("plan", "await_approval")
        graph.add_conditional_edges(
            "await_approval",
            _route_after_approval,
            {"execute": "execute", "need_human": "need_human"},
        )
        graph.add_conditional_edges(
            "execute",
            _route_after_execution,
            {"verify": "verify", "need_human": "need_human"},
        )
        graph.add_conditional_edges(
            "verify",
            _route_after_verification,
            {"resolved": "resolved", "need_human": "need_human"},
        )
        graph.add_edge("resolved", END)
    graph.add_edge("need_human", END)
    if checkpointer is None:
        return graph.compile()
    return graph.compile(checkpointer=checkpointer)
