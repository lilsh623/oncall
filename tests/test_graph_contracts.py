import unittest
from datetime import datetime, timezone
from uuid import uuid4


class GraphContractsTest(unittest.TestCase):
    def test_supervisor_rejects_recovery_tool_decision(self):
        from oncall.agents.supervisor import SupervisorDecision

        with self.assertRaises(ValueError):
            SupervisorDecision(next_step="rollback_release", rationale="unsafe")

    def test_action_plan_hash_is_stable_and_allows_only_rollback(self):
        from oncall.graph.contracts import ActionPlanDraft, RollbackReleaseAction

        action = RollbackReleaseAction(
            project_id="demo-shop",
            environment="staging",
            service="order-api",
            current_version="v2",
            target_version="v1",
        )
        plan = ActionPlanDraft(
            summary="Rollback order-api from v2 to v1",
            risk_level="medium",
            prerequisites=["human approval"],
            rollback=action,
            verification_criteria=["HTTP error rate recovers"],
        )
        self.assertEqual(plan.plan_hash, ActionPlanDraft.model_validate(plan.model_dump()).plan_hash)

        with self.assertRaises(ValueError):
            ActionPlanDraft(
                summary="Restart service",
                risk_level="high",
                prerequisites=[],
                rollback={"action_type": "restart_service"},
                verification_criteria=[],
            )

    def test_incident_graph_runs_to_waiting_approval_without_recovery(self):
        from oncall.graph.contracts import IncidentGraphState
        from oncall.graph.incident import build_incident_graph

        graph = build_incident_graph(checkpointer=None)
        state = IncidentGraphState(
            incident_id=str(uuid4()),
            status="TRIAGING",
            project_id="demo-shop",
            environment="staging",
            service="order-api",
            alert_summary="HighErrorRate after release v2",
            created_at=datetime.now(timezone.utc),
        )
        result = graph.invoke(state.model_dump(mode="json"))
        self.assertEqual(result["status"], "WAITING_APPROVAL")
        self.assertTrue(result["evidence"])
        self.assertTrue(result["knowledge_citations"])
        self.assertEqual(result["action_plan"]["rollback"]["action_type"], "rollback_release")
        self.assertEqual(result["execution_result"], None)


if __name__ == "__main__":
    unittest.main()
