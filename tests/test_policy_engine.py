import unittest

from oncall.graph.contracts import ActionPlanDraft, RollbackReleaseAction


class PolicyEngineTest(unittest.TestCase):
    def test_allows_only_demo_shop_staging_order_api_v2_to_v1(self):
        from oncall.policy.engine import evaluate_action_plan

        plan = ActionPlanDraft(
            summary="rollback",
            risk_level="medium",
            prerequisites=[],
            rollback=RollbackReleaseAction(
                project_id="demo-shop",
                environment="staging",
                service="order-api",
                current_version="v2",
                target_version="v1",
            ),
            verification_criteria=[],
        )
        decision = evaluate_action_plan(plan, incident=None)
        self.assertTrue(decision.allowed)

    def test_rejects_wrong_target_version(self):
        from oncall.policy.engine import evaluate_action_plan

        plan = ActionPlanDraft(
            summary="rollback",
            risk_level="medium",
            prerequisites=[],
            rollback=RollbackReleaseAction(
                project_id="demo-shop",
                environment="staging",
                service="order-api",
                current_version="v2",
                target_version="v3",
            ),
            verification_criteria=[],
        )
        decision = evaluate_action_plan(plan, incident=None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "ROLLBACK_NOT_ALLOWLISTED")


if __name__ == "__main__":
    unittest.main()
