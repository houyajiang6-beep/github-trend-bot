from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import daily_report_pipeline
import main
from config import Settings
from crawler import Repository


def make_repository() -> Repository:
    return Repository(
        rank=1,
        full_name="example/project",
        url="https://github.com/example/project",
        stars=1000,
        stars_today=100,
        language="Python",
        description="Test repository",
        readme="Test README",
        topics=["ai"],
        focus_score=1,
    )


class MainFallbackTests(unittest.TestCase):
    def test_missing_deepseek_key_still_writes_fallback_report(self) -> None:
        with TemporaryDirectory() as directory:
            report_dir = Path(directory)
            settings = Settings(
                deepseek_api_key="",
                report_dir=report_dir,
                log_dir=report_dir / "logs",
                creator_output_dir=report_dir / "outputs",
                enable_daily_content_pipeline=False,
            )
            with (
                patch.object(main, "settings", settings),
                patch.object(
                    daily_report_pipeline.GitHubTrendingCrawler,
                    "collect",
                    return_value=[make_repository()],
                ),
            ):
                main.run(dry_run=True, skip_ai=False)

            reports = list(report_dir.glob("*.json"))
            self.assertEqual(len(reports), 1)
            data = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertEqual(data["analysis"]["top10"][0]["full_name"], "example/project")
            self.assertIn("DeepSeek API 本次不可用", data["analysis"]["ai_observation"])
            self.assertEqual(
                data["analysis"]["top10"][0]["ai_category"],
                "AI 自动化应用",
            )
            self.assertIn("daily_growth_rate", data["growth_metrics"]["example/project"])
            self.assertIn("technical_trends", data["market_insight"])
            content_path = report_dir / "content" / f"{data['date']}.json"
            content = json.loads(content_path.read_text(encoding="utf-8"))
            self.assertIn("douyin_titles", content)
            self.assertIn("voiceover_30s", content)
            self.assertIn("xiaohongshu_note", content)
            self.assertIn("video_topics", content)
            market_path = report_dir / "market_insight" / f"{data['date']}.json"
            market = json.loads(market_path.read_text(encoding="utf-8"))
            self.assertIn("business_opportunities", market)
            html_report = next(report_dir.glob("*.html")).read_text(encoding="utf-8")
            self.assertIn("今日 AI 领域观察", html_report)
            self.assertIn("市场与商业洞察", html_report)
            self.assertIn("内容创作建议", html_report)
            status = json.loads(
                (settings.log_dir / "actions-status.json").read_text(encoding="utf-8")
            )
            self.assertFalse(status["deepseek"]["success"])
            self.assertTrue(status["deepseek"]["fallback"])
            self.assertTrue(status["report"]["success"])
            self.assertTrue(status["market_insight"]["generated"])
            self.assertTrue(status["social_content"]["generated"])

    def test_gmail_body_contains_all_v11_sections(self) -> None:
        with TemporaryDirectory() as directory:
            report_dir = Path(directory)
            settings = Settings(
                report_dir=report_dir,
                log_dir=report_dir / "logs",
                creator_output_dir=report_dir / "outputs",
                enable_daily_content_pipeline=False,
            )
            with (
                patch.object(main, "settings", settings),
                patch.object(
                    daily_report_pipeline.GitHubTrendingCrawler,
                    "collect",
                    return_value=[make_repository()],
                ),
                patch.object(daily_report_pipeline, "send_email") as send_email,
            ):
                main.run(dry_run=False, skip_ai=True)

            send_email.assert_called_once()
            plain_text = send_email.call_args.args[1]
            html_body = send_email.call_args.args[2]
            for section in ("今日AI领域观察", "市场与商业洞察", "内容创作建议"):
                self.assertIn(section, plain_text)
            for section in ("今日 AI 领域观察", "市场与商业洞察", "内容创作建议"):
                self.assertIn(section, html_body)


if __name__ == "__main__":
    unittest.main()
