"""Remediation Planner that only drafts rollback_release actions for V1."""

from __future__ import annotations

from oncall.graph.contracts import ActionPlanDraft, IncidentGraphState, RollbackReleaseAction


def plan_remediation(state: IncidentGraphState) -> ActionPlanDraft:
    """Create a human-approved rollback plan; never executes recovery."""

    current_version = "current"
    target_version = "previous-stable"
    for evidence in state.evidence:
        data = evidence.payload.get("data") if isinstance(evidence.payload, dict) else None
        payload = data if isinstance(data, dict) else evidence.payload
        release = payload.get("release") if isinstance(payload, dict) else None
        if isinstance(release, dict) and release.get("version"):
            current_version = str(release["version"])
        if isinstance(payload, dict) and payload.get("current_version"):
            current_version = str(payload["current_version"])
        if isinstance(payload, dict) and payload.get("previous_healthy_version"):
            target_version = str(payload["previous_healthy_version"])
    if current_version == target_version:
        target_version = "previous-stable"
    return ActionPlanDraft(
        summary=f"Rollback {state.service} from {current_version} to {target_version} after human approval.",
        risk_level="medium",
        prerequisites=[
            "On-call engineer approves the exact plan hash.",
            "Current release still matches the diagnosed faulty version.",
        ],
        rollback=RollbackReleaseAction(
            project_id=state.project_id,
            environment=state.environment,
            service=state.service,
            current_version=current_version,
            target_version=target_version,
        ),
        verification_criteria=[
            "Tencent Cloud TAT reports a successful invocation.",
            "Tencent Cloud CLS contains no new ERROR logs after the recovery window.",
        ],
    )
