import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


class RecoveryProofTest(unittest.TestCase):
    def test_valid_proof_is_accepted_once_and_replay_is_rejected(self):
        from mcp_servers.recovery import server
        from oncall.execution.service import create_approval_proof

        plan = SimpleNamespace(
            rollback={
                "project_id": "demo-shop",
                "environment": "staging",
                "service": "order-api",
                "current_version": "v2",
                "target_version": "v1",
            },
            plan_hash="a" * 64,
        )
        approval = SimpleNamespace(id=uuid4())
        secret = "unit-test-independent-approval-secret"
        proof = create_approval_proof(plan, approval, nonce="n" * 16, secret=secret)
        request = server.RollbackReleaseRequest(
            **plan.rollback,
            action_plan_hash=plan.plan_hash,
            approval_id=approval.id,
            **proof,
        )
        original_path = server.NONCE_DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            server.NONCE_DB_PATH = Path(directory) / "nonces.sqlite3"
            try:
                server.verify_approval_proof(request, secret=secret)
                with self.assertRaises(ValueError):
                    server.verify_approval_proof(request, secret=secret)
            finally:
                server.NONCE_DB_PATH = original_path

    def test_expired_proof_is_rejected_before_nonce_claim(self):
        from mcp_servers.recovery import server

        request = server.RollbackReleaseRequest(
            project_id="demo-shop",
            environment="staging",
            service="order-api",
            current_version="v2",
            target_version="v1",
            action_plan_hash="b" * 64,
            approval_id=uuid4(),
            nonce="e" * 16,
            issued_at=int(time.time()) - 301,
            approval_proof="c" * 64,
        )
        with self.assertRaises(ValueError):
            server.verify_approval_proof(request, secret="unit-test-independent-approval-secret")


if __name__ == "__main__":
    unittest.main()
