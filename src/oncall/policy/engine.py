"""Deterministic policy checks for V1 recovery actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from oncall.graph.contracts import ActionPlanDraft
from oncall.policy.schemas import PolicyDecision


POLICY_PATH = Path(__file__).resolve().parents[3] / "project-packs" / "demo-shop" / "policies.yaml"
DEFAULT_ALLOWED_ROLLBACK = {
    "project_id": "demo-shop",
    "environment": "staging",
    "service": "order-api",
    "current_version": "v2",
    "target_version": "v1",
}


def _load_allowed_rollback() -> dict[str, str]:
    if not POLICY_PATH.exists():
        return DEFAULT_ALLOWED_ROLLBACK
    with POLICY_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    rollback = payload.get("allowed_rollback")
    if not isinstance(rollback, dict):
        return DEFAULT_ALLOWED_ROLLBACK
    return {**DEFAULT_ALLOWED_ROLLBACK, **{key: str(value) for key, value in rollback.items()}}


def evaluate_action_plan(plan: ActionPlanDraft | dict[str, Any], incident: Any) -> PolicyDecision:
    """Allow only the fixed demo-shop staging order-api v2 -> v1 rollback."""

    action_plan = plan if isinstance(plan, ActionPlanDraft) else ActionPlanDraft.model_validate(plan)
    rollback = action_plan.rollback.model_dump(mode="json")
    allowed = _load_allowed_rollback()
    expected = {"action_type": "rollback_release", **allowed}
    if rollback != expected:
        return PolicyDecision(
            allowed=False,
            reason_code="ROLLBACK_NOT_ALLOWLISTED",
            message="Policy allows only demo-shop/staging/order-api rollback from v2 to v1.",
        )
    if incident is not None:
        mismatches = [
            field
            for field in ("project_id", "environment", "service")
            if getattr(incident, field, None) != allowed[field]
        ]
        if mismatches:
            return PolicyDecision(
                allowed=False,
                reason_code="INCIDENT_SCOPE_MISMATCH",
                message="Incident scope does not match the approved demo recovery policy.",
            )
    return PolicyDecision(
        allowed=True,
        reason_code="ALLOWED",
        message="Policy allows this fixed demo rollback after human approval.",
    )
