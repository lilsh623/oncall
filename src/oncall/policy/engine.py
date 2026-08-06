"""Deterministic policy checks for V1 recovery actions."""

from __future__ import annotations

from typing import Any

from oncall.graph.contracts import ActionPlanDraft
from oncall.policy.schemas import PolicyDecision


def evaluate_action_plan(
    plan: ActionPlanDraft | dict[str, Any],
    incident: Any,
    *,
    recovery_config: dict[str, Any] | None = None,
) -> PolicyDecision:
    """Validate a rollback against the registered Tencent Cloud service binding."""

    action_plan = plan if isinstance(plan, ActionPlanDraft) else ActionPlanDraft.model_validate(plan)
    rollback = action_plan.rollback.model_dump(mode="json")
    if recovery_config is None or recovery_config.get("provider") != "tencent_tat":
        return PolicyDecision(
            allowed=False,
            reason_code="RECOVERY_NOT_CONFIGURED",
            message="该服务尚未登记腾讯云 TAT 恢复配置。",
        )
    policy = recovery_config.get("action_policy", {})
    if not isinstance(policy, dict) or policy.get("rollback_release") != "approval":
        return PolicyDecision(
            allowed=False,
            reason_code="RECOVERY_APPROVAL_REQUIRED",
            message="腾讯云 rollback_release 必须配置为人工审批。",
        )
    if incident is not None and any(
        getattr(incident, field, None) != rollback[field]
        for field in ("project_id", "environment", "service")
    ):
        return PolicyDecision(
            allowed=False,
            reason_code="INCIDENT_SCOPE_MISMATCH",
            message="恢复计划与事件服务范围不一致。",
        )
    if not all(
        isinstance(rollback.get(field), str) and rollback[field]
        for field in ("current_version", "target_version")
    ):
        return PolicyDecision(
            allowed=False,
            reason_code="INVALID_RECOVERY_TARGET",
            message="恢复版本不能为空。",
        )
    return PolicyDecision(
        allowed=True,
        reason_code="ALLOWED",
        message="腾讯云 TAT 恢复计划已通过服务范围校验，等待人工审批。",
    )
