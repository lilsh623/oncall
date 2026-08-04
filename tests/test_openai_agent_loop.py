import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4


def _state():
    from oncall.graph.contracts import IncidentGraphState

    return IncidentGraphState(
        incident_id=str(uuid4()),
        graph_run_id="incident:test:attempt:1",
        status="INVESTIGATING",
        project_id="demo-shop",
        environment="staging",
        service="order-api",
        alert_summary="HighErrorRate after release v2",
        created_at=datetime.now(timezone.utc),
    )


def _config():
    return SimpleNamespace(
        agent_loop_max_turns=8,
        agent_specialist_max_turns=4,
        agent_max_tool_calls=16,
        openai_agents_tracing_enabled=False,
    )


class FakeRunner:
    def __init__(self, *, wrong_scope: bool = False):
        self.wrong_scope = wrong_scope

    async def run(
        self,
        starting_agent,
        input,
        *,
        context,
        max_turns,
        run_config,
    ):
        from oncall.agents.openai_loop import IncidentIntelligenceResult
        from oncall.graph.contracts import (
            ActionPlanDraft,
            DiagnosisDraft,
            EvidenceItem,
            HypothesisDraft,
            KnowledgeCitationDraft,
            RollbackReleaseAction,
        )

        context.evidence.append(
            EvidenceItem(
                source_type="mcp",
                source_ref="get_current_release:call-1",
                observation="Validated current release",
                payload={
                    "data": {
                        "release": {"version": "v2"},
                        "previous_healthy_version": "v1",
                    }
                },
            )
        )
        context.citations.append(
            KnowledgeCitationDraft(
                document_id="rollback-sop",
                document_version="1.0.0",
                section="Rollback criteria",
                file_path="knowledge/rollback.md",
                locator="knowledge/rollback.md",
                excerpt="Rollback after a release-correlated regression.",
                score=0.9,
            )
        )
        diagnosis = DiagnosisDraft(
            root_cause="Release v2 caused the HTTP error regression.",
            confidence=0.9,
            summary="Release and error timing correlate.",
        )
        hypothesis = HypothesisDraft(
            description=diagnosis.root_cause,
            confidence=diagnosis.confidence,
            supporting_summary=diagnosis.summary,
        )
        plan = ActionPlanDraft(
            summary="Rollback order-api from v2 to v1 after approval.",
            risk_level="medium",
            prerequisites=["Human approval of the exact plan hash."],
            rollback=RollbackReleaseAction(
                project_id="other-project" if self.wrong_scope else "demo-shop",
                environment="staging",
                service="order-api",
                current_version="v2",
                target_version="v1",
            ),
            verification_criteria=["HTTP error rate recovers."],
        )
        output = IncidentIntelligenceResult(
            status="PLAN_READY",
            diagnosis=diagnosis,
            action_plan=plan,
            hypotheses=[hypothesis],
            reason="Evidence and approved SOP support the plan.",
        )
        return SimpleNamespace(
            final_output=output,
            context_wrapper=SimpleNamespace(usage=SimpleNamespace(requests=7)),
        )


class OpenAIAgentLoopTest(unittest.TestCase):
    def test_builds_supervisor_with_three_isolated_specialist_tools(self):
        from oncall.agents.openai_loop import build_incident_agents

        supervisor, investigator, diagnostician, planner = build_incident_agents(
            "test-model", specialist_max_turns=4
        )
        self.assertEqual(supervisor.name, "Incident Supervisor")
        self.assertEqual(
            [tool.name for tool in supervisor.tools],
            ["investigate_incident", "diagnose_incident", "plan_remediation"],
        )
        self.assertNotIn("rollback_release", [tool.name for tool in investigator.tools])
        self.assertEqual(diagnostician.name, "Incident Diagnostician")
        self.assertEqual(planner.name, "Remediation Planner")

    def test_read_tool_cannot_override_incident_scope(self):
        from agents import RunContextWrapper

        from oncall.agents.openai_loop import IncidentAgentContext, _read_mcp

        class Gateway:
            def __init__(self):
                self.arguments = None

            async def call_read_tool(self, name, arguments, **kwargs):
                self.arguments = arguments
                return SimpleNamespace(
                    tool_call_id="call-1",
                    truncated=False,
                    data={"release": {"version": "v2"}},
                )

        gateway = Gateway()
        context = IncidentAgentContext(
            state=_state(),
            gateway=gateway,
            allowed_read_tools=frozenset({"get_current_release"}),
            max_tool_calls=1,
        )
        result = asyncio.run(
            _read_mcp(
                RunContextWrapper(context=context),
                "get_current_release",
                {
                    "project_id": "attacker-project",
                    "environment": "production",
                    "service": "other-service",
                },
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(gateway.arguments["project_id"], "demo-shop")
        self.assertEqual(gateway.arguments["environment"], "staging")
        self.assertEqual(gateway.arguments["service"], "order-api")

    def test_returns_plan_to_langgraph_without_approval_or_execution(self):
        from oncall.agents.openai_loop import run_incident_agent_loop

        result = asyncio.run(
            run_incident_agent_loop(
                _state(),
                live_mode=True,
                settings=_config(),
                runner=FakeRunner(),
                model="test-model",
                selected_skill=None,
            )
        )
        self.assertEqual(result["status"], "PLANNED")
        self.assertEqual(result["model_call_count"], 7)
        self.assertEqual(result["action_plan"]["rollback"]["target_version"], "v1")
        self.assertRegex(result["action_plan"]["plan_hash"], r"^[a-f0-9]{64}$")
        self.assertNotIn("approval", result)
        self.assertNotIn("execution_result", result)

    def test_rejects_model_plan_that_changes_incident_scope(self):
        from oncall.agents.openai_loop import run_incident_agent_loop

        result = asyncio.run(
            run_incident_agent_loop(
                _state(),
                live_mode=True,
                settings=_config(),
                runner=FakeRunner(wrong_scope=True),
                model="test-model",
                selected_skill=None,
            )
        )
        self.assertEqual(result["status"], "NEED_HUMAN")
        self.assertIn("scope", result["need_human_reason"])


if __name__ == "__main__":
    unittest.main()
