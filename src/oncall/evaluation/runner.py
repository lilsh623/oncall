"""Offline fixture replay and optional online Agent/tool evaluation."""

from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.conversation.intents import route_message
from oncall.evaluation.schemas import EvaluationMetrics
from oncall.mcp_gateway.client import McpGateway
from oncall.models import EvaluationCaseResult, EvaluationRun, Incident, User
from oncall.rag.schemas import KnowledgeQuery
from oncall.rag.service import search_knowledge


DATASET_PATH = Path(__file__).with_name("datasets") / "oncall_v1.json"


@dataclass
class CaseOutcome:
    actual: dict[str, Any]
    passed: bool
    scores: dict[str, Any]
    error: str | None = None


def load_dataset() -> dict[str, Any]:
    payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload.get("cases"), list):
        raise ValueError("evaluation dataset must contain cases")
    return payload


def _matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


async def _online_case(
    session: AsyncSession, case: dict[str, Any], gateway: McpGateway
) -> CaseOutcome:
    category = str(case["category"])
    case_input = dict(case.get("input") or {})
    expected = dict(case.get("expected") or {})
    if category == "router":
        decision = await route_message(str(case_input["message"]), live_mode=True)
        actual = {
            "intent": decision.intent,
            "action": decision.action,
            "service": decision.service,
        }
        expected_subset = {key: value for key, value in expected.items() if key in actual}
        return CaseOutcome(actual, _matches(actual, expected_subset), {"exact_match": int(_matches(actual, expected_subset))})
    if category == "rag":
        query = KnowledgeQuery.model_validate(case_input)
        hits = await asyncio.to_thread(search_knowledge, query)
        actual_ids = list(dict.fromkeys(item.document_id for item in hits))
        expected_ids = [str(item) for item in expected.get("document_ids", [])]
        overlap = sorted(set(actual_ids) & set(expected_ids))
        hit_rate = len(overlap) / len(expected_ids) if expected_ids else 1.0
        return CaseOutcome(
            {"document_ids": actual_ids},
            bool(overlap) if expected_ids else True,
            {"hit_rate": hit_rate, "matched_document_ids": overlap},
        )
    if category == "tool":
        result = await gateway.call_read_tool(
            str(case_input["tool_name"]),
            dict(case_input["arguments"]),
            agent_name="evaluation-runner",
        )
        actual = {"success": True, "tool_name": result.tool_name, "server": result.server}
        return CaseOutcome(actual, bool(expected.get("success")), {"success": 1.0})
    if category == "e2e":
        statement = select(Incident).order_by(Incident.opened_at.desc())
        if case_input.get("service"):
            statement = statement.where(Incident.service == case_input["service"])
        incident = await session.scalar(statement.limit(1))
        actual = {
            "resolved": bool(incident and incident.status.value == "RESOLVED"),
            "incident_id": str(incident.id) if incident else None,
            "status": incident.status.value if incident else None,
        }
        return CaseOutcome(actual, actual["resolved"] == expected.get("resolved"), {"resolved": float(actual["resolved"])})
    raise ValueError(f"unsupported evaluation category: {category}")


def _offline_case(case: dict[str, Any]) -> CaseOutcome:
    category = str(case["category"])
    expected = dict(case.get("expected") or {})
    actual = dict(case.get("offline_actual") or {})
    if category == "rag":
        expected_ids = set(str(item) for item in expected.get("document_ids", []))
        actual_ids = set(str(item) for item in actual.get("document_ids", []))
        overlap = expected_ids & actual_ids
        score = len(overlap) / len(expected_ids) if expected_ids else 1.0
        return CaseOutcome(actual, bool(overlap) if expected_ids else True, {"hit_rate": score})
    passed = _matches(actual, expected)
    score_name = "resolved" if category == "e2e" else "success" if category == "tool" else "exact_match"
    return CaseOutcome(actual, passed, {score_name: float(passed)})


def _aggregate(results: list[EvaluationCaseResult]) -> EvaluationMetrics:
    total = len(results)
    passed = sum(int(row.passed) for row in results)

    def rate(category: str, score: str | None = None) -> float:
        rows = [row for row in results if row.category == category]
        if not rows:
            return 0.0
        if score:
            return sum(float(row.scores.get(score, 0)) for row in rows) / len(rows)
        return sum(int(row.passed) for row in rows) / len(rows)

    return EvaluationMetrics(
        task_success_rate=passed / total if total else 0.0,
        tool_call_success_rate=rate("tool", "success"),
        rag_hit_rate=rate("rag", "hit_rate"),
        end_to_end_resolution_rate=rate("e2e", "resolved"),
        evaluated_cases=total,
        passed_cases=passed,
    )


def _empty_metrics() -> dict[str, Any]:
    return EvaluationMetrics(
        task_success_rate=0,
        tool_call_success_rate=0,
        rag_hit_rate=0,
        end_to_end_resolution_rate=0,
        evaluated_cases=0,
        passed_cases=0,
    ).model_dump(mode="json")


async def create_evaluation_run_record(
    session: AsyncSession, *, mode: str, actor: User
) -> EvaluationRun:
    dataset = load_dataset()
    run = EvaluationRun(
        mode=mode,
        dataset_name=str(dataset["name"]),
        dataset_version=str(dataset["version"]),
        status="PENDING",
        created_by=actor.id,
        case_count=len(dataset["cases"]),
        passed_count=0,
        metrics=_empty_metrics(),
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return run


async def execute_evaluation_run(session: AsyncSession, run: EvaluationRun) -> EvaluationRun:
    dataset = load_dataset()
    run.status = "RUNNING"
    await session.flush()
    gateway = McpGateway()
    results: list[EvaluationCaseResult] = []
    for case in dataset["cases"]:
        started = perf_counter()
        try:
            outcome = (
                await _online_case(session, case, gateway)
                if mode == "online"
                else _offline_case(case)
            )
        except Exception as exc:
            outcome = CaseOutcome({}, False, {}, type(exc).__name__)
        result = EvaluationCaseResult(
            run_id=run.id,
            case_id=str(case["id"]),
            category=str(case["category"]),
            passed=outcome.passed,
            input=dict(case.get("input") or {}),
            expected=dict(case.get("expected") or {}),
            actual=outcome.actual,
            scores=outcome.scores,
            duration_ms=max(0, round((perf_counter() - started) * 1000)),
            error=outcome.error,
        )
        session.add(result)
        results.append(result)
    metrics = _aggregate(results)
    run.passed_count = metrics.passed_cases
    run.metrics = metrics.model_dump(mode="json")
    run.status = "COMPLETED"
    run.completed_at = datetime.now(UTC)
    await session.flush()
    return run


async def run_evaluation(
    session: AsyncSession, *, mode: str, actor: User
) -> EvaluationRun:
    """Convenience wrapper used by tests and direct CLI-style callers."""

    run = await create_evaluation_run_record(session, mode=mode, actor=actor)
    return await execute_evaluation_run(session, run)
