from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from config import BASE_DIR, Settings
from daily_content_pipeline import run_daily_content_pipeline
from human_value_agent import load_projects


class DailyContentPipelineTests(unittest.TestCase):
    def test_single_entry_scores_before_generating_only_top_three(self) -> None:
        projects = load_projects(BASE_DIR / "mock_projects.json")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_daily_content_pipeline(
                projects,
                publish_date=date(2026, 7, 20),
                cfg=Settings(report_timezone="Asia/Shanghai"),
                use_llm=False,
                top_n=3,
                output_root=root,
            )
            run_dir = Path(result["run_directory"])
            package = result["content_package"]
            selection = json.loads(
                (run_dir / "daily_selection.json").read_text(encoding="utf-8")
            )
            prediction = json.loads(
                Path(result["prediction"]).read_text(encoding="utf-8")
            )
            creator_dir = Path(result["creator_ready_directory"])
            creator_entrypoints_exist = all(
                (creator_dir / filename).is_file()
                for filename in (
                    "daily_selection.md",
                    "daily_selection.json",
                    "publish.txt",
                    "cover.txt",
                    "prediction.json",
                )
            )

        self.assertLessEqual(package["selected_projects"], 3)
        self.assertEqual(
            package["selected_projects"], len(selection["top_candidates"])
        )
        self.assertEqual(prediction["project_name"], result["selected_project"])
        self.assertTrue(prediction["prediction_locked"])
        self.assertIsNone(prediction["performance"])
        self.assertTrue(creator_entrypoints_exist)

    def test_top_n_rejects_values_other_than_one_or_three(self) -> None:
        with self.assertRaisesRegex(ValueError, "1 或 3"):
            run_daily_content_pipeline(
                [], publish_date="2026-07-20", use_llm=False, top_n=2
            )


if __name__ == "__main__":
    unittest.main()
