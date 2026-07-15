from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from config import BASE_DIR, Settings
from human_value_agent import (
    DIMENSION_KEYS,
    HumanValueAgent,
    load_projects,
    load_rules,
    rule_scores,
    write_report,
)


class HumanValueAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_rules(BASE_DIR / "human_value_rules.yaml")
        cls.projects = load_projects(BASE_DIR / "mock_projects.json")
        cls.settings = Settings(report_timezone="Asia/Shanghai")

    def test_rules_have_seven_dimensions_and_weight_100(self) -> None:
        self.assertEqual(set(self.rules["dimensions"]), set(DIMENSION_KEYS))
        self.assertEqual(
            sum(item["weight"] for item in self.rules["dimensions"].values()),
            100,
        )

    def test_mock_input_contains_ten_different_projects(self) -> None:
        self.assertEqual(len(self.projects), 10)
        names = {project["full_name"] for project in self.projects}
        self.assertEqual(len(names), 10)

    def test_rule_baseline_separates_consumer_app_from_cpp_library(self) -> None:
        consumer = rule_scores(self.projects[0], self.rules)
        cpp_library = rule_scores(self.projects[6], self.rules)

        self.assertGreater(
            consumer["normal_user_value"], cpp_library["normal_user_value"]
        )
        self.assertGreater(
            consumer["usage_threshold"], cpp_library["usage_threshold"]
        )
        self.assertGreater(consumer["visual_value"], cpp_library["visual_value"])

    def test_rules_only_report_has_required_output_contract(self) -> None:
        report = HumanValueAgent(self.rules, self.settings).evaluate(
            self.projects, use_llm=False
        )

        self.assertEqual(report["mode"], "rules_only")
        self.assertEqual(len(report["projects"]), 10)
        required = {
            "project_name",
            "source_description",
            "source_language",
            "source_topics",
            "source_url",
            "human_value_score",
            "scores",
            "target_user",
            "why_people_care",
            "content_angle",
            "recommended_or_not",
        }
        for item in report["projects"]:
            self.assertEqual(set(item), required)
            self.assertEqual(set(item["scores"]), set(DIMENSION_KEYS))
            self.assertTrue(
                all(1 <= score <= 5 for score in item["scores"].values())
            )
            self.assertTrue(0 <= item["human_value_score"] <= 100)

    def test_llm_scores_are_blended_with_rules(self) -> None:
        def fake_llm(projects, rules, settings):
            return [
                {
                    "project_name": project["full_name"],
                    "scores": {key: 5 for key in DIMENSION_KEYS},
                    "target_user": "普通学生",
                    "why_people_care": "能节省时间",
                    "content_angle": "展示使用前后对比",
                }
                for project in projects
            ]

        report = HumanValueAgent(
            self.rules, self.settings, llm_evaluator=fake_llm
        ).evaluate(self.projects[:2])

        self.assertEqual(report["mode"], "llm_and_rules")
        self.assertEqual(report["llm_scored_projects"], 2)
        self.assertEqual(report["projects"][0]["target_user"], "普通学生")
        self.assertGreaterEqual(report["projects"][0]["human_value_score"], 80)

    def test_llm_failure_falls_back_to_rules(self) -> None:
        def failed_llm(projects, rules, settings):
            raise TimeoutError("test timeout")

        report = HumanValueAgent(
            self.rules, self.settings, llm_evaluator=failed_llm
        ).evaluate(self.projects[:1])

        self.assertEqual(report["mode"], "rules_fallback")
        self.assertEqual(report["failed_llm_batches"], 1)
        self.assertEqual(report["rule_fallback_projects"], 1)

    def test_report_is_written_as_valid_json(self) -> None:
        report = HumanValueAgent(self.rules, self.settings).evaluate(
            self.projects[:1], use_llm=False
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "human_value_report.json"
            write_report(output, report)
            loaded = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(loaded["projects"][0]["project_name"], "mock/private-doc-chat")


if __name__ == "__main__":
    unittest.main()
