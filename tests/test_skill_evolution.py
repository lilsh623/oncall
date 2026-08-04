import unittest


class SkillEvolutionTest(unittest.TestCase):
    def test_generated_skill_is_schema_valid_and_read_only(self):
        from oncall.models import Experience
        from oncall.skills.evolution import build_skill_material
        from oncall.skills.schemas import SkillManifest

        experience = Experience(
            project_id="demo-shop",
            environment="staging",
            service="order-api",
            candidate_id="00000000-0000-0000-0000-000000000001",
            pattern_fingerprint="a" * 64,
            content_hash="b" * 64,
            version=1,
            title="Verified release regression",
            content={
                "root_cause": "Release v2 introduced a regression.",
                "symptoms": ["HighErrorRate after release"],
                "action": {"action_type": "rollback_release"},
            },
            source_incident_ids=["00000000-0000-0000-0000-000000000002"],
            status="PUBLISHED",
        )
        manifest, instructions, content_hash = build_skill_material(
            experience,
            alert_names=["HighErrorRate"],
            version="1.0.0",
        )
        validated = SkillManifest.model_validate(manifest)
        self.assertIn("query_metrics", validated.allowed_tools)
        self.assertNotIn("rollback_release", validated.allowed_tools)
        self.assertEqual(
            {"rollback", "restart", "scale", "arbitrary_write"},
            set(validated.forbidden_actions),
        )
        self.assertIn("never execute or authorize remediation", instructions)
        self.assertEqual(len(content_hash), 64)

    def test_patch_versions_are_monotonic(self):
        from oncall.skills.evolution import next_patch_version

        self.assertEqual(next_patch_version(None), "1.0.0")
        self.assertEqual(next_patch_version("1.0.0"), "1.0.1")
        self.assertEqual(next_patch_version("2.3.9"), "2.3.10")


if __name__ == "__main__":
    unittest.main()
