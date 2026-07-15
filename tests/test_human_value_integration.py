from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

import daily_report_pipeline
import main
import runner
from config import Settings
from content_generator import ContentGeneratorMVP
from crawler import Repository


def make_repository() -> Repository:
    return Repository(
        rank=1,
        full_name="example/consumer-ai",
        url="https://github.com/example/consumer-ai",
        stars=5000,
        stars_today=300,
        language="TypeScript",
        description="Browser AI tool for students to summarize PDF documents",
        readme=(
            "A hosted web app with drag and drop PDF support, visual citations, "
            "and no command line required."
        ),
        topics=["ai", "study", "pdf", "web-app"],
        focus_score=3,
    )


class DailyPipelineMainIntegrationTests(unittest.TestCase):
    def test_creator_failure_does_not_block_daily_email(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = Settings(
                enable_daily_content_pipeline=True,
                report_dir=root / "reports",
                log_dir=root / "logs",
                creator_output_dir=root / "outputs",
            )
            with (
                patch.object(main, "settings", cfg),
                patch.object(
                    daily_report_pipeline.GitHubTrendingCrawler,
                    "collect",
                    return_value=[make_repository()],
                ),
                patch.object(
                    runner,
                    "run_daily_content_pipeline",
                    side_effect=RuntimeError("simulated pipeline failure"),
                ),
                patch.object(daily_report_pipeline, "send_email") as send_email,
            ):
                main.run(dry_run=False, skip_ai=True)

            execution_status = json.loads(
                (cfg.log_dir / "execution_status.json").read_text(encoding="utf-8")
            )

        send_email.assert_called_once()
        self.assertEqual(execution_status["creator_pipeline"]["status"], "failed")
        self.assertIsNone(
            execution_status["creator_pipeline"]["selected_project"]
        )
        self.assertEqual(execution_status["daily_report"]["status"], "success")
        self.assertEqual(execution_status["overall_status"], "degraded")

    def test_offline_end_to_end_generates_creator_ready_entrypoints(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = Settings(
                enable_daily_content_pipeline=True,
                report_dir=root / "reports",
                log_dir=root / "logs",
                creator_output_dir=root / "outputs",
            )
            original_generate = ContentGeneratorMVP.generate
            with (
                patch.object(main, "settings", cfg),
                patch.object(
                    daily_report_pipeline.GitHubTrendingCrawler,
                    "collect",
                    return_value=[make_repository()],
                ),
                patch.object(
                    ContentGeneratorMVP,
                    "generate",
                    autospec=True,
                    side_effect=original_generate,
                ) as creator_generator,
                patch.object(daily_report_pipeline, "generate_content") as legacy_generator,
            ):
                result = main.run(dry_run=True, skip_ai=True)

            report_date = datetime.now(
                ZoneInfo(cfg.report_timezone)
            ).date().isoformat()
            creator_dir = cfg.creator_output_dir / "creator_ready" / report_date
            execution_status = json.loads(
                (cfg.log_dir / "execution_status.json").read_text(encoding="utf-8")
            )
            required_files = {
                "daily_selection.md",
                "daily_selection.json",
                "publish.txt",
                "cover.txt",
                "prediction.json",
            }

            self.assertEqual(result["creator_pipeline"]["status"], "success")
            self.assertTrue(result["creator_pipeline"]["selected_project"])
            self.assertTrue(required_files.issubset({p.name for p in creator_dir.iterdir()}))
            self.assertTrue((cfg.report_dir / f"{report_date}.html").is_file())
            self.assertTrue((cfg.report_dir / "content" / f"{report_date}.json").is_file())
            self.assertEqual(execution_status["overall_status"], "success")
            self.assertFalse(execution_status["daily_report"]["gmail_sent"])
            creator_generator.assert_called_once()
            legacy_generator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
