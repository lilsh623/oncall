"""Celery tasks that start or resume deterministic Incident processing."""

import asyncio
from contextlib import asynccontextmanager
from collections.abc import Coroutine
from typing import Any
from uuid import UUID, uuid4

from celery.signals import worker_process_shutdown
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select

from oncall.audit.service import append_audit_event
from oncall.config import get_settings
from oncall.database import async_session, get_engine, get_session_factory
from oncall.graph.contracts import IncidentGraphState
from oncall.graph.incident import build_incident_graph
from oncall.incidents.state import IncidentStatus, ensure_transition
from oncall.jobs.celery_app import celery_app
from oncall.models import ActionPlan, ActionStep, Evidence, Hypothesis, Incident, KnowledgeCitation


_worker_loop: asyncio.AbstractEventLoop | None = None

GRAPH_STATUS_PATH: tuple[IncidentStatus, ...] = (
    IncidentStatus.INVESTIGATING,
    IncidentStatus.DIAGNOSED,
    IncidentStatus.PLANNING,
    IncidentStatus.WAITING_APPROVAL,
)


def _checkpoint_dsn(database_url: str) -> str:
    """Translate SQLAlchemy's asyncpg URL to the psycopg URL LangGraph expects."""

    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def _graph_checkpointer():
    """Open the durable PostgreSQL checkpointer used across approval interrupts."""

    dsn = _checkpoint_dsn(get_settings().database_url)
    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        yield checkpointer


async def _run_live_graph(graph_input: IncidentGraphState) -> dict[str, Any]:
    """Run only the real MCP/RAG/Bailian graph and persist its interrupt state."""

    async with _graph_checkpointer() as checkpointer:
        graph = build_incident_graph(checkpointer=checkpointer, live_mode=True)
        return await graph.ainvoke(
            graph_input.model_dump(mode="json"),
            config={"configurable": {"thread_id": graph_input.graph_run_id}},
        )


async def _persist_graph_findings(
    session: Any,
    incident: Incident,
    graph_result: dict[str, Any],
) -> ActionPlan | None:
    """Persist bounded graph outputs so APIs never need to expose model history."""

    for item in graph_result.get("evidence", []):
        if not isinstance(item, dict):
            continue
        exists = await session.scalar(
            select(Evidence.id).where(
                Evidence.incident_id == incident.id,
                Evidence.source_ref == item.get("source_ref"),
                Evidence.observation == item.get("observation"),
            )
        )
        if exists is None:
            session.add(
                Evidence(
                    incident_id=incident.id,
                    source_type=str(item.get("source_type", "system")),
                    source_ref=str(item.get("source_ref", "unknown")),
                    observation=str(item.get("observation", "")),
                    payload=item.get("payload") if isinstance(item.get("payload"), dict) else {},
                )
            )
    for item in graph_result.get("hypotheses", []):
        if not isinstance(item, dict):
            continue
        exists = await session.scalar(
            select(Hypothesis.id).where(
                Hypothesis.incident_id == incident.id,
                Hypothesis.description == item.get("description"),
            )
        )
        if exists is None:
            session.add(
                Hypothesis(
                    incident_id=incident.id,
                    description=str(item.get("description", "")),
                    confidence=item.get("confidence"),
                    supporting_summary=item.get("supporting_summary"),
                    opposing_summary=item.get("opposing_summary"),
                    next_check=item.get("next_check"),
                )
            )
    for item in graph_result.get("knowledge_citations", []):
        if not isinstance(item, dict):
            continue
        exists = await session.scalar(
            select(KnowledgeCitation.id).where(
                KnowledgeCitation.incident_id == incident.id,
                KnowledgeCitation.document_id == item.get("document_id"),
                KnowledgeCitation.locator == item.get("locator"),
            )
        )
        if exists is None:
            session.add(
                KnowledgeCitation(
                    incident_id=incident.id,
                    document_id=str(item.get("document_id", "unknown")),
                    document_version=item.get("document_version"),
                    section=item.get("section"),
                    file_path=item.get("file_path"),
                    locator=item.get("locator"),
                    excerpt=item.get("excerpt"),
                    score=item.get("score"),
                )
            )

    plan_payload = graph_result.get("action_plan")
    if not isinstance(plan_payload, dict):
        return None
    plan = await session.scalar(
        select(ActionPlan).where(
            ActionPlan.incident_id == incident.id,
            ActionPlan.plan_hash == plan_payload["plan_hash"],
        )
    )
    if plan is not None:
        return plan
    latest_version = await session.scalar(
        select(ActionPlan.version)
        .where(ActionPlan.incident_id == incident.id)
        .order_by(ActionPlan.version.desc())
        .limit(1)
    )
    plan = ActionPlan(
        incident_id=incident.id,
        version=(latest_version or 0) + 1,
        summary=plan_payload["summary"],
        risk_level=plan_payload["risk_level"],
        prerequisites=plan_payload["prerequisites"],
        rollback=plan_payload["rollback"],
        verification_criteria=plan_payload["verification_criteria"],
        plan_hash=plan_payload["plan_hash"],
        status="PENDING_APPROVAL",
    )
    session.add(plan)
    await session.flush()
    session.add(
        ActionStep(
            action_plan_id=plan.id,
            sequence=1,
            name="Rollback the approved release",
            tool_name="rollback_release",
            tool_arguments=plan.rollback,
            risk_level=plan.risk_level,
            expected_result="order-api runs the approved target version",
        )
    )
    return plan


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
                graph_run_id=f"incident:{incident.id}:{uuid4()}",
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

    try:
        graph_result = await _run_live_graph(graph_input)
    except Exception:
        graph_result = {
            "status": IncidentStatus.NEED_HUMAN.value,
            "need_human_reason": "The durable incident graph could not start safely.",
            "investigation_rounds": 0,
            "model_call_count": 0,
        }
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
        plan = await _persist_graph_findings(session, incident, graph_result)
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
                        "action_plan_hash": plan.plan_hash if plan is not None else None,
                        "need_human_reason": graph_result.get("need_human_reason"),
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
