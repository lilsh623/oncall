import unittest


class ExperienceExtractionTest(unittest.TestCase):
    def test_redacts_secrets_and_personal_network_identifiers_recursively(self):
        from oncall.experience.extraction import redact_value

        result = redact_value(
            {
                "authorization": "Bearer should-never-survive",
                "message": (
                    "contact alice@example.com from 10.24.17.8 using "
                    "Bearer abcdefghijklmnop"
                ),
                "nested": {"api_key": "top-secret"},
            }
        )
        self.assertEqual(result["authorization"], "<REDACTED>")
        self.assertEqual(result["nested"]["api_key"], "<REDACTED>")
        self.assertNotIn("alice@example.com", result["message"])
        self.assertNotIn("10.24.17.8", result["message"])
        self.assertNotIn("abcdefghijklmnop", result["message"])

    def test_pattern_fingerprint_is_stable_and_incident_independent(self):
        from oncall.experience.extraction import pattern_fingerprint

        first = pattern_fingerprint(
            project_id="Payments",
            environment="STAGING",
            service="order-api",
            root_cause="Release  v2 introduced a regression",
            action_type="rollback_release",
        )
        second = pattern_fingerprint(
            project_id=" payments ",
            environment="staging",
            service="ORDER-API",
            root_cause="release v2 introduced a regression",
            action_type="rollback_release",
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_candidate_content_hash_changes_when_reviewed_action_changes(self):
        from oncall.experience.extraction import candidate_document, canonical_hash

        baseline = candidate_document(
            title="verified recovery",
            summary="HighErrorRate",
            symptoms=["5xx above threshold"],
            root_cause="release regression",
            action={"action_type": "rollback_release", "target_version": "v1"},
            verification=[{"name": "health", "conclusion": "PASSED"}],
            warnings=["approval required"],
        )
        changed = {**baseline, "action": {**baseline["action"], "target_version": "v0"}}
        self.assertNotEqual(canonical_hash(baseline), canonical_hash(changed))


if __name__ == "__main__":
    unittest.main()
