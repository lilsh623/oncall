"""Celery tasks that start or resume deterministic Incident processing."""

import asyncio
from collections.abc import Coroutine
from typing import Any
from uuid import UUID

from celery.signals import worker_process_shutdown
from sqlalchemy import select

from oncall.audit.service import append_audit_event
from oncall.database import async_session, get_engine, get_session_factory
from oncall.graph.contracts import IncidentGraphState
from oncall.graph.incident import build_incident_graph
from oncall.incidents.state import IncidentStatus, ensure_transition
from oncall.jobs.celery_app import celery_app
from oncall.models import ActionPlan, Incident


_worker_loop: asyncio.AbstractEventLoop | None = None

GRAPH_STATUS_PATH: tuple[IncidentStatus, ...] = (
    IncidentStatus.INVESTIGATING,
    IncidentStatus.DIAGNOSED,
    IncidentStatus.PLANNING,
    IncidentStatus.WAITING_APPROVAL,
)


def _run_in_worker_loop(
    coroutine: Coroutine[Any, Any, dict[str, str | int]],
) -> dict[str, str | int]:
    """Keep one event loop per Celery child process for the cached async pool."""

    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
    return _worker_loop.run_until_complete(coroutine)


async def _start_incident(incident_id: UUID, checkpoint_version: int) -> dict[str, str | int]:
    graph_input: IncidentGraphState | None = None
    async with async_session() as session:
        result = await session.execute(
            select(Incident).where(Incident.id == incident_id).with_for_update()
        )
        incident = result.scalar_one_or_none()
        if incident is None:
            return {
                "incident_id": str(incident_id),
                "checkpoint_version": checkpoint_version,
                "status": "NOT_FOUND",
            }

        if incident.status == IncidentStatus.RECEIVED:
            target = ensure_transition(incident.status, IncidentStatus.TRIAGING)
            previous = incident.status
            incident.status = target
            await append_audit_event(
                session,
                incident.id,
                "incident.status_changed",
                {
                    "from": previous.value,
                    "to": target.value,
                    "checkpoint_version": checkpoint_version,
                },
                actor="celery:start_incident",
            )
            await session.commit()
            graph_input = IncidentGraphState(
                incident_id=str(incident.id),
                status=target.value,
                project_id=incident.project_id,
                environment=incident.environment,
                service=incident.service,
                alert_summary=incident.summary or incident.title,
                created_at=incident.opened_at,
            )
        if graph_input is None:
            status_value = (
                incident.status.value
                if isinstance(incident.status, IncidentStatus)
                else str(incident.status)
            )
            return {
                "incident_id": str(incident_id),
                "checkpoint_version": checkpoint_version,
                "status": status_value,
            }

    if graph_input is None:
        return {
            "incident_id": str(incident_id),
            "checkpoint_version": checkpoint_version,
            "status": "UNCHANGED",
        }

    graph_result = build_incident_graph(checkpointer=None).invoke(
        graph_input.model_dump(mode="json")
    )
    graph_status = IncidentStatus(str(graph_result["status"]))
    async with async_session() as session:
        result = await session.execute(
            select(Incident).where(Incident.id == incident_id).with_for_update()
        )
        incident = result.scalar_one_or_none()
        if incident is None:
            return {
                "incident_id": str(incident_id),
                "checkpoint_version": checkpoint_version,
                "status": "NOT_FOUND",
            }
        previous = incident.status
        plan_payload = graph_result.get("action_plan")
        if isinstance(plan_payload, dict):
            existing_plan = await session.execute(
                select(ActionPlan).where(
                    ActionPlan.incident_id == incident.id,
                    ActionPlan.plan_hash == plan_payload["plan_hash"],
                )
            )
            if existing_plan.scalar_one_or_none() is None:
                session.add(
                    ActionPlan(
                        incident_id=incident.id,
                        summary=plan_payload["summary"],
                        risk_level=plan_payload["risk_level"],
                        prerequisites=plan_payload["prerequisites"],
                        rollback=plan_payload["rollback"],
                        verification_criteria=plan_payload["verification_criteria"],
                        plan_hash=plan_payload["plan_hash"],
                        status="PENDING_APPROVAL",
                    )
                )
        if previous != graph_status:
            path = GRAPH_STATUS_PATH if graph_status == IncidentStatus.WAITING_APPROVAL else (graph_status,)
            for target_status in path:
                if incident.status == target_status:
                    continue
                step_previous = incident.status
                incident.status = ensure_transition(step_previous, target_status)
                await append_audit_event(
                    session,
                    incident.id,
                    "incident.graph_completed",
                    {
                        "from": step_previous.value,
                        "to": target_status.value,
                        "checkpoint_version": checkpoint_version,
                        "investigation_rounds": graph_result.get("investigation_rounds", 0),
                        "model_call_count": graph_result.get("model_call_count", 0),
                        "action_plan_hash": (graph_result.get("action_plan") or {}).get("plan_hash"),
                    },
                    actor="celery:start_incident",
                )
            await session.commit()
        return {
            "incident_id": str(incident_id),
            "checkpoint_version": checkpoint_version,
            "status": graph_status.value,
        }


@celery_app.task(name="oncall.start_incident")
def start_incident(incident_id: str, checkpoint_version: int) -> dict[str, str | int]:
    """Idempotently advance RECEIVED to TRIAGING; Task 8 will start the Graph."""

    if checkpoint_version < 1:
        raise ValueError("checkpoint_version must be positive")
    return _run_in_worker_loop(_start_incident(UUID(incident_id), checkpoint_version))


@worker_process_shutdown.connect
def close_worker_async_resources(**_: object) -> None:
    """Dispose loop-bound database resources when a Celery child exits."""

    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        return
    if get_engine.cache_info().currsize:
        _worker_loop.run_until_complete(get_engine().dispose())
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    _worker_loop.close()
    _worker_loop = None
