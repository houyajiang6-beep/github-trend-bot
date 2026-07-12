from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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
            settings = Settings(deepseek_api_key="", report_dir=report_dir)
            with (
                patch.object(main, "settings", settings),
                patch.object(
                    main.GitHubTrendingCrawler,
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


if __name__ == "__main__":
    unittest.main()
