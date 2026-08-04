import unittest


class ConversationRouterTest(unittest.TestCase):
    def test_routes_knowledge_question(self):
        from oncall.conversation.intents import deterministic_route

        decision = deterministic_route("发布后错误率升高应该怎么排查？")
        self.assertEqual((decision.intent, decision.action), ("KNOWLEDGE", "NONE"))

    def test_routes_incident_actions_and_extracts_service(self):
        from oncall.conversation.intents import deterministic_route

        decision = deterministic_route("请排查 order-api 的告警")
        self.assertEqual((decision.intent, decision.action), ("INCIDENT", "INVESTIGATE"))
        self.assertEqual(decision.service, "order-api")

        plan = deterministic_route("为 order-api 生成修复方案")
        self.assertEqual(plan.action, "PLAN")

    def test_approval_language_is_always_blocked(self):
        from oncall.conversation.intents import deterministic_route

        decision = deterministic_route("批准并立即回滚 order-api")
        self.assertEqual((decision.intent, decision.action), ("INCIDENT", "APPROVAL_BLOCKED"))
        self.assertEqual(decision.confidence, 1)

    def test_rejects_inconsistent_intent_action_contract(self):
        from pydantic import ValidationError
        from oncall.conversation.schemas import RouterDecision

        with self.assertRaises(ValidationError):
            RouterDecision(intent="KNOWLEDGE", action="PLAN")


if __name__ == "__main__":
    unittest.main()
