import time
import unittest
from types import SimpleNamespace
from uuid import uuid4


class RecoveryApprovalProofTest(unittest.TestCase):
    def _request(self, *, nonce: str = "n" * 16, issued_at: int | None = None):
        from mcp_servers.recovery.server import RollbackReleaseRequest
        from oncall.execution.service import create_approval_proof

        plan = SimpleNamespace(
            rollback={
                "project_id": "payments",
                "environment": "prod",
                "service": "checkout",
                "current_version": "2026.08.5",
                "target_version": "2026.08.4",
            },
            plan_hash="a" * 64,
        )
        approval = SimpleNamespace(id=uuid4())
        secret = "unit-test-independent-approval-secret"
        proof = create_approval_proof(
            plan,
            approval,
            nonce=nonce,
            issued_at=issued_at,
            secret=secret,
        )
        request = RollbackReleaseRequest(
            **plan.rollback,
            action_plan_hash=plan.plan_hash,
            approval_id=approval.id,
            **proof,
        )
        return request, secret

    def test_valid_proof_is_accepted_once(self):
        from mcp_servers.recovery.server import verify_approval_proof

        request, secret = self._request()
        claimed: set[str] = set()

        def claim(nonce: str) -> bool:
            if nonce in claimed:
                return False
            claimed.add(nonce)
            return True

        verify_approval_proof(request, secret=secret, claim_nonce=claim)
        with self.assertRaisesRegex(ValueError, "replayed"):
            verify_approval_proof(request, secret=secret, claim_nonce=claim)

    def test_expired_proof_is_rejected_before_nonce_claim(self):
        from mcp_servers.recovery.server import verify_approval_proof

        request, secret = self._request(issued_at=int(time.time()) - 301)
        calls: list[str] = []
        with self.assertRaisesRegex(ValueError, "expired"):
            verify_approval_proof(
                request,
                secret=secret,
                claim_nonce=lambda nonce: calls.append(nonce) is None,
            )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
