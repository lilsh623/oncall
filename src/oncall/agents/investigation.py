"""Investigation Agent constrained to read-only MCP observations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from oncall.graph.contracts import EvidenceItem, HypothesisDraft, IncidentGraphState
from oncall.mcp_gateway.client import McpGateway, McpGatewayError
from oncall.mcp_gateway.schemas import LogLevel, MetricName


async def run_investigation_agent(
    state: IncidentGraphState,
    *,
    gateway: McpGateway | None = None,
) -> dict[str, Any]:
    """Collect bounded read-only evidence and propose a hypothesis."""

    client = gateway or McpGateway()
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=30)
    base = {
        "project_id": state.project_id,
        "environment": state.environment,
        "service": state.service,
    }
    calls = [
        ("get_current_release", base),
        ("query_metrics", {**base, "start_time": start_time, "end_time": end_time, "metric": MetricName.HTTP_ERROR_RATE, "limit": 30}),
        ("query_service_logs", {**base, "start_time": start_time, "end_time": end_time, "levels": (LogLevel.ERROR, LogLevel.WARNING), "limit": 20}),
        ("get_service_health", {**base, "start_time": start_time, "end_time": end_time, "limit": 1}),
        ("get_active_alerts", {**base, "start_time": start_time, "end_time": end_time, "limit": 20}),
    ]
    evidence: list[EvidenceItem] = []
    for tool_name, arguments in calls:
        try:
            result = await client.call_read_tool(
                tool_name,
                arguments,
                incident_id=state.incident_id,
                agent_name="investigation",
            )
            evidence.append(
                EvidenceItem(
                    source_type="mcp",
                    source_ref=tool_name,
                    observation=f"{tool_name} returned structured read-only data",
                    payload={"truncated": result.truncated, "data": result.data},
                )
            )
        except McpGatewayError as exc:
            evidence.append(
                EvidenceItem(
                    source_type="mcp",
                    source_ref=tool_name,
                    observation=f"{tool_name} failed with safe category {exc.category.value}",
                    payload={"code": exc.code, "category": exc.category.value},
                )
            )
    if not evidence:
        evidence.append(
            EvidenceItem(
                source_type="system",
                source_ref="investigation_agent",
                observation="No read-only evidence could be collected.",
            )
        )
    hypothesis = HypothesisDraft(
        description="HTTP errors likely correlate with the latest order-api release.",
        confidence=0.7,
        supporting_summary="V1 demo scenario focuses on post-deployment HighErrorRate regressions.",
        next_check="Confirm SOP guidance and rollback target before remediation planning.",
    )
    return {"evidence": evidence, "hypotheses": [hypothesis], "model_call_count": 1}


def run_stub_investigation(state: IncidentGraphState) -> dict[str, Any]:
    """Offline deterministic fallback for contract tests and no-network smoke runs."""

    evidence = EvidenceItem(
        source_type="mcp",
        source_ref="offline_demo_observability",
        observation="HighErrorRate appeared after the latest v2 release of order-api.",
        payload={"current_version": "v2", "previous_healthy_version": "v1"},
    )
    hypothesis = HypothesisDraft(
        description="Latest release v2 introduced an order-api regression.",
        confidence=0.86,
        supporting_summary="Alert summary and demo release evidence point to v2.",
        next_check="Use SOP to validate rollback criteria.",
    )
    return {"evidence": [evidence], "hypotheses": [hypothesis], "model_call_count": 1}
