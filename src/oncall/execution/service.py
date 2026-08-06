"""Idempotent executor for an already approved, fixed recovery plan."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from oncall.config import get_settings
from oncall.database import async_session
from oncall.incidents.state import IncidentStatus, ensure_transition
from oncall.models import ActionPlan, ActionStep, Approval, CloudProject, CloudService, Execution, Incident
from oncall.policy.engine import evaluate_action_plan


class RecoveryRequest(BaseModel):
    """The complete fixed-schema request accepted by Recovery MCP."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    environment: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,127}$")
    current_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    target_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    action_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_id: UUID
    nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    issued_at: int
    approval_proof: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: UUID | None
    status: str
    response: dict[str, Any] | None = None
    error: str | None = None


class RecoveryMcpError(RuntimeError):
    """A bounded, non-sensitive failure from the separate Recovery MCP."""


def _mcp_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/mcp"):
        path = f"{path}/mcp" if path else "/mcp"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _ready_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/health/ready", "", ""))


async def _ensure_recovery_ready(url: str, secret: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                _ready_endpoint(url), headers={"Authorization": f"Bearer {secret}"}
            )
        if response.status_code != 200:
            raise RecoveryMcpError("Recovery MCP is not ready")
    except (httpx.HTTPError, RecoveryMcpError) as exc:
        raise RecoveryMcpError("Recovery MCP is not ready") from exc


def _structured_content(result: Any) -> dict[str, Any]:
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    combined = "".join(
        str(getattr(item, "text", "")) for item in result.content
    )[:8192]
    try:
        payload = json.loads(combined)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RecoveryMcpError("Recovery MCP returned no bounded structured result") from exc
    if not isinstance(payload, dict):
        raise RecoveryMcpError("Recovery MCP returned an invalid result")
    return payload


async def _call_recovery_mcp(
    request: RecoveryRequest,
    *,
    tool_name: str = "rollback_release",
    tencent_tat: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    url = settings.recovery_mcp_url
    service_secret = settings.recovery_mcp_secret.get_secret_value()
    await _ensure_recovery_ready(url, service_secret)
    try:
        async with asyncio.timeout(70):
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {service_secret}"}, timeout=65.0
            ) as http_client:
                async with streamable_http_client(
                    _mcp_endpoint(url), http_client=http_client
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as client:
                        await client.initialize()
                        arguments: dict[str, Any] = {
                            "request": request.model_dump(mode="json")
                        }
                        if tencent_tat is not None:
                            arguments["tencent_tat"] = tencent_tat
                        result = await client.call_tool(tool_name, arguments)
    except TimeoutError as exc:
        raise TimeoutError("Recovery MCP call timed out") from exc
    except Exception as exc:
        raise RecoveryMcpError("Recovery MCP transport failed") from exc
    if result.isError:
        raise RecoveryMcpError("Recovery MCP rejected the approved rollback request")
    payload = _structured_content(result)
    if payload.get("status") not in {"succeeded", "failed"}:
        raise RecoveryMcpError("Recovery MCP returned an invalid execution status")
    return payload


def create_approval_proof(
    plan: ActionPlan,
    approval: Approval,
    *,
    nonce: str | None = None,
    issued_at: int | None = None,
    secret: str | None = None,
) -> dict[str, object]:
    """Sign immutable approval facts before crossing the Recovery MCP boundary."""

    nonce_value = nonce or uuid4().hex
    issued_value = issued_at if issued_at is not None else int(time.time())
    rollback = plan.rollback
    payload = ":".join(
        [
            str(rollback["project_id"]),
            str(rollback["environment"]),
            str(rollback["service"]),
            str(rollback["current_version"]),
            str(rollback["target_version"]),
            plan.plan_hash,
            str(approval.id),
            nonce_value,
            str(issued_value),
        ]
    )
    key = (
        secret
        if secret is not None
        else get_settings().recovery_approval_secret.get_secret_value()
    ).encode("utf-8")
    proof = hmac.new(key, payload.encode("utf-8"), sha256).hexdigest()
    return {"nonce": nonce_value, "issued_at": issued_value, "approval_proof": proof}


def _idempotency_key(plan: ActionPlan, approval: Approval) -> str:
    return f"rollback:{plan.id}:{plan.plan_hash}:{approval.id}"


async def _existing_result(session: Any, key: str) -> ExecutionResult | None:
    execution = await session.scalar(select(Execution).where(Execution.idempotency_key == key))
    if execution is None:
        return None
    return ExecutionResult(
        execution_id=execution.id,
        status=execution.status,
        response=execution.response,
        error=execution.error,
    )


async def execute_approved_plan(incident_id: UUID, plan_id: UUID) -> ExecutionResult:
    """Execute exactly once after a matching approved plan and policy check.

    A duplicate job returns the saved execution record. A timeout becomes UNKNOWN
    after a read-only version check; it is deliberately not retried automatically.
    """

    recovery_provider = "tencent_tat"
    recovery_tool_name = "rollback_release"
    tencent_tat: dict[str, Any] | None = None
    async with async_session() as session:
        incident = await session.scalar(
            select(Incident).where(Incident.id == incident_id).with_for_update()
        )
        plan = await session.scalar(
            select(ActionPlan)
            .where(ActionPlan.id == plan_id, ActionPlan.incident_id == incident_id)
            .with_for_update()
        )
        if incident is None or plan is None:
            return ExecutionResult(execution_id=None, status="NOT_FOUND", error="incident or plan not found")
        approval = await session.scalar(
            select(Approval)
            .where(
                Approval.action_plan_id == plan.id,
                Approval.action_plan_hash == plan.plan_hash,
                Approval.decision == "APPROVED",
            )
            .order_by(Approval.decided_at.desc())
            .limit(1)
        )
        if approval is None or plan.status != "APPROVED":
            return ExecutionResult(
                execution_id=None, status="NOT_APPROVED", error="matching approved plan is required"
            )
        service_config: dict[str, Any] | None = None
        project = await session.scalar(
            select(CloudProject).where(CloudProject.project_id == incident.project_id)
        )
        if project is not None:
            binding = await session.scalar(
                select(CloudService).where(
                    CloudService.cloud_project_id == project.id,
                    CloudService.environment == incident.environment,
                    CloudService.service == incident.service,
                    CloudService.status == "active",
                )
            )
            if binding is not None:
                service_config = binding.recovery_config
                recovery_provider = str(service_config.get("provider", recovery_provider))
                recovery_tool_name = str(service_config.get("tool_name", recovery_tool_name))
                configured_tat = service_config.get("tencent_tat")
                if isinstance(configured_tat, dict):
                    tencent_tat = configured_tat
        decision = evaluate_action_plan(
            {
                "summary": plan.summary,
                "risk_level": plan.risk_level,
                "prerequisites": plan.prerequisites,
                "rollback": plan.rollback,
                "verification_criteria": plan.verification_criteria,
                "plan_hash": plan.plan_hash,
            },
            incident,
            recovery_config=service_config,
        )
        if not decision.allowed:
            return ExecutionResult(execution_id=None, status="DENIED", error=decision.reason_code)
        if recovery_provider == "tencent_tat" and tencent_tat is None:
            return ExecutionResult(
                execution_id=None,
                status="DENIED",
                error="Tencent TAT recovery configuration is required",
            )

        key = _idempotency_key(plan, approval)
        step = await session.scalar(
            select(ActionStep)
            .where(ActionStep.action_plan_id == plan.id, ActionStep.tool_name == "rollback_release")
            .order_by(ActionStep.sequence.asc())
            .limit(1)
        )
        execution_id = uuid4()
        inserted = await session.scalar(
            postgres_insert(Execution)
            .values(
                id=execution_id,
                action_plan_id=plan.id,
                action_step_id=step.id if step is not None else None,
                idempotency_key=key,
                status="RUNNING",
                tool_name="rollback_release",
                request={"approval_id": str(approval.id), "plan_hash": plan.plan_hash},
                started_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Execution.id)
        )
        if inserted is None:
            return (await _existing_result(session, key)) or ExecutionResult(
                execution_id=None, status="UNKNOWN", error="execution insert conflicted"
            )
        if incident.status == IncidentStatus.WAITING_APPROVAL:
            incident.status = ensure_transition(incident.status, IncidentStatus.EXECUTING)
        await session.commit()

    rollback_request = {
        key: plan.rollback[key]
        for key in ("project_id", "environment", "service", "current_version", "target_version")
    }
    request = RecoveryRequest(
        **rollback_request,
        action_plan_hash=plan.plan_hash,
        approval_id=approval.id,
        **create_approval_proof(plan, approval),
    )
    try:
        response = await _call_recovery_mcp(
            request,
            tool_name=recovery_tool_name,
            tencent_tat=tencent_tat,
        )
        execution_status = "SUCCEEDED" if response["status"] == "succeeded" else "FAILED"
        error = None if execution_status == "SUCCEEDED" else "Recovery MCP reported failure"
    except TimeoutError:
        response = {"tencent_tat": "timeout"}
        execution_status = "UNKNOWN"
        error = "Tencent Cloud TAT timed out; automatic retry is prohibited"
    except (RecoveryMcpError, ValidationError):
        response = None
        execution_status = "FAILED"
        error = "Tencent Cloud TAT MCP rejected or could not execute the configured action"

    async with async_session() as session:
        execution = await session.get(Execution, execution_id, with_for_update=True)
        if execution is None:  # pragma: no cover - the insert above is durable
            return ExecutionResult(execution_id=None, status="UNKNOWN", error="execution disappeared")
        execution.status = execution_status
        execution.response = response
        execution.error = error
        execution.completed_at = datetime.now(timezone.utc)
        await session.commit()
        return ExecutionResult(
            execution_id=execution.id,
            status=execution.status,
            response=execution.response,
            error=execution.error,
        )
