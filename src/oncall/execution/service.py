"""Execution service for approved recovery plans."""

from __future__ import annotations

import hmac
import time
from hashlib import sha256
from uuid import uuid4

from oncall.config import get_settings
from oncall.models import ActionPlan, Approval


def create_approval_proof(plan: ActionPlan, approval: Approval, nonce: str | None = None) -> dict[str, object]:
    """Create the bounded HMAC proof sent to Recovery MCP instead of user JWTs."""

    issued_at = int(time.time())
    nonce_value = nonce or uuid4().hex
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
            str(issued_at),
        ]
    )
    secret = get_settings().recovery_mcp_secret.get_secret_value().encode("utf-8")
    proof = hmac.new(secret, payload.encode("utf-8"), sha256).hexdigest()
    return {"nonce": nonce_value, "issued_at": issued_at, "approval_proof": proof}


async def execute_approved_plan(incident_id, plan_id):
    """Task 9 placeholder; full execution is driven after approval in later wiring."""

    return {"incident_id": str(incident_id), "plan_id": str(plan_id), "status": "queued"}
