import unittest


class AgentEvaluationTest(unittest.TestCase):
    def test_builtin_dataset_covers_all_metric_categories(self):
        from oncall.evaluation.runner import load_dataset

        dataset = load_dataset()
        categories = {case["category"] for case in dataset["cases"]}
        self.assertEqual(categories, {"router", "rag", "tool", "e2e"})

    def test_offline_dataset_is_reproducible(self):
        from oncall.evaluation.runner import _offline_case, load_dataset

        outcomes = [_offline_case(case) for case in load_dataset()["cases"]]
        self.assertTrue(all(outcome.passed for outcome in outcomes))


if __name__ == "__main__":
    unittest.main()
