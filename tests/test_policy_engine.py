import unittest
from types import SimpleNamespace

from oncall.graph.contracts import ActionPlanDraft, RollbackReleaseAction


class PolicyEngineTest(unittest.TestCase):
    def test_allows_approved_tencent_tat_service_scope(self):
        from oncall.policy.engine import evaluate_action_plan

        plan = ActionPlanDraft(
            summary="rollback",
            risk_level="medium",
            prerequisites=[],
            rollback=RollbackReleaseAction(
                project_id="payments",
                environment="prod",
                service="checkout",
                current_version="2026.08.5",
                target_version="2026.08.4",
            ),
            verification_criteria=[],
        )
        decision = evaluate_action_plan(plan, incident=None, recovery_config={"provider": "tencent_tat", "action_policy": {"rollback_release": "approval"}})
        self.assertTrue(decision.allowed)

    def test_rejects_missing_tencent_tat_configuration(self):
        from oncall.policy.engine import evaluate_action_plan

        plan = ActionPlanDraft(
            summary="rollback",
            risk_level="medium",
            prerequisites=[],
            rollback=RollbackReleaseAction(
                project_id="payments",
                environment="prod",
                service="checkout",
                current_version="2026.08.5",
                target_version="2026.08.4",
            ),
            verification_criteria=[],
        )
        decision = evaluate_action_plan(plan, incident=None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "RECOVERY_NOT_CONFIGURED")

    def test_catalog_policy_allows_matching_external_service_scope(self):
        from oncall.policy.engine import evaluate_action_plan

        plan = {
            "summary": "rollback",
            "risk_level": "high",
            "prerequisites": ["approval"],
            "rollback": {
                "action_type": "rollback_release",
                "project_id": "payments",
                "environment": "prod",
                "service": "checkout",
                "current_version": "2026.08.5",
                "target_version": "2026.08.4",
            },
            "verification_criteria": ["error rate recovers"],
            "plan_hash": "a" * 64,
        }
        decision = evaluate_action_plan(
            plan,
            SimpleNamespace(project_id="payments", environment="prod", service="checkout"),
            recovery_config={
                "provider": "tencent_tat",
                "action_policy": {"rollback_release": "approval"},
            },
        )
        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
