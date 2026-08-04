import asyncio
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
        result = asyncio.run(graph.ainvoke(state.model_dump(mode="json")))
        self.assertEqual(result["status"], "WAITING_APPROVAL")
        self.assertTrue(result["evidence"])
        self.assertTrue(result["knowledge_citations"])
        self.assertEqual(result["action_plan"]["rollback"]["action_type"], "rollback_release")
        self.assertEqual(result["execution_result"], None)

    def test_durable_graph_resumes_through_execution_and_verification(self):
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.types import Command

        from oncall.execution.service import ExecutionResult
        from oncall.execution.workflow import RecoveryVerificationOutcome
        from oncall.graph.contracts import IncidentGraphState
        from oncall.graph.incident import build_incident_graph

        incident_id = uuid4()
        plan_id = uuid4()
        approval_id = uuid4()
        execution_id = uuid4()
        calls = []

        async def execute_plan(received_incident_id, received_plan_id):
            calls.append(("execute", received_incident_id, received_plan_id))
            return ExecutionResult(
                execution_id=execution_id,
                status="SUCCEEDED",
                response={"status": "succeeded"},
            )

        async def verify_plan(
            received_incident_id, received_plan_id, received_execution_id
        ):
            calls.append(
                (
                    "verify",
                    received_incident_id,
                    received_plan_id,
                    received_execution_id,
                )
            )
            return RecoveryVerificationOutcome(
                status="RESOLVED",
                passed=True,
                checks=[{"name": "service_health", "status": "passed"}],
            )

        async def scenario():
            graph = build_incident_graph(
                checkpointer=InMemorySaver(),
                execute_plan=execute_plan,
                verify_plan=verify_plan,
            )
            config = {"configurable": {"thread_id": f"incident:{incident_id}"}}
            state = IncidentGraphState(
                incident_id=str(incident_id),
                graph_run_id=f"incident:{incident_id}:attempt:1",
                status="TRIAGING",
                project_id="demo-shop",
                environment="staging",
                service="order-api",
                alert_summary="HighErrorRate after release v2",
                created_at=datetime.now(timezone.utc),
            )
            interrupted = await graph.ainvoke(
                state.model_dump(mode="json"), config=config
            )
            self.assertEqual(interrupted["status"], "WAITING_APPROVAL")
            result = await graph.ainvoke(
                Command(
                    resume={
                        "decision": "APPROVED",
                        "approval_id": str(approval_id),
                        "action_plan_id": str(plan_id),
                        "action_plan_hash": interrupted["action_plan"]["plan_hash"],
                    }
                ),
                config=config,
            )
            return result

        result = asyncio.run(scenario())
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["execution_result"]["status"], "SUCCEEDED")
        self.assertTrue(result["verification_result"]["passed"])
        self.assertEqual(
            calls,
            [
                ("execute", incident_id, plan_id),
                ("verify", incident_id, plan_id, execution_id),
            ],
        )

    def test_durable_graph_rejects_approval_for_a_different_plan(self):
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.types import Command

        from oncall.graph.contracts import IncidentGraphState
        from oncall.graph.incident import build_incident_graph

        incident_id = uuid4()
        execute_called = False

        async def execute_plan(*_):
            nonlocal execute_called
            execute_called = True
            raise AssertionError("mismatched approval must not execute")

        async def scenario():
            graph = build_incident_graph(
                checkpointer=InMemorySaver(), execute_plan=execute_plan
            )
            config = {"configurable": {"thread_id": f"incident:{incident_id}"}}
            state = IncidentGraphState(
                incident_id=str(incident_id),
                graph_run_id=f"incident:{incident_id}:attempt:1",
                status="TRIAGING",
                project_id="demo-shop",
                environment="staging",
                service="order-api",
                alert_summary="HighErrorRate after release v2",
                created_at=datetime.now(timezone.utc),
            )
            await graph.ainvoke(state.model_dump(mode="json"), config=config)
            return await graph.ainvoke(
                Command(
                    resume={
                        "decision": "APPROVED",
                        "approval_id": str(uuid4()),
                        "action_plan_id": str(uuid4()),
                        "action_plan_hash": "f" * 64,
                    }
                ),
                config=config,
            )

        result = asyncio.run(scenario())
        self.assertEqual(result["status"], "NEED_HUMAN")
        self.assertIn("does not match", result["need_human_reason"])
        self.assertFalse(execute_called)


if __name__ == "__main__":
    unittest.main()
