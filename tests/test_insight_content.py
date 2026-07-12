from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from content_generator import fallback_content
from crawler import Repository
from market_insight import (
    build_growth_metrics,
    fallback_market_insight,
    load_previous_stars,
)


def make_repository(stars: int = 1200, stars_today: int = 300) -> Repository:
    return Repository(
        rank=1,
        full_name="example/agent-tool",
        url="https://github.com/example/agent-tool",
        stars=stars,
        stars_today=stars_today,
        language="Python",
        description="An AI agent tool",
        readme="Build an agent workflow with Python.",
        topics=["ai", "agent"],
        focus_score=3,
    )


class GrowthMetricTests(unittest.TestCase):
    def test_growth_metric_uses_previous_snapshot_without_crawler_changes(self) -> None:
        repo = make_repository()
        metrics = build_growth_metrics(
            [repo], {repo.full_name: 1000}, elapsed_days=2
        )[repo.full_name]

        self.assertEqual(metrics["historical_stars_per_day"], 100.0)
        self.assertEqual(metrics["daily_growth_rate"], 25.0)
        self.assertEqual(metrics["ai_category"], "AI Agent / 智能体")
        self.assertIn("加速", metrics["growth_trend"])

    def test_load_previous_stars_selects_latest_earlier_report(self) -> None:
        with TemporaryDirectory() as directory:
            report_dir = Path(directory)
            for day, stars in (("2026-07-08", 800), ("2026-07-10", 1000)):
                (report_dir / f"{day}.json").write_text(
                    json.dumps(
                        {"repositories": [{"full_name": "example/agent-tool", "stars": stars}]}
                    ),
                    encoding="utf-8",
                )

            stars, elapsed = load_previous_stars(report_dir, "2026-07-11")

            self.assertEqual(stars["example/agent-tool"], 1000)
            self.assertEqual(elapsed, 1)


class ContentFallbackTests(unittest.TestCase):
    def test_fallback_outputs_requested_json_sections(self) -> None:
        repositories = [make_repository()]
        metrics = build_growth_metrics(repositories)
        insight = fallback_market_insight(repositories, metrics)
        content = fallback_content(
            "2026-07-11",
            repositories,
            {"ai_observation": "Agent 工具热度上升。"},
            insight,
        )

        self.assertEqual(content["date"], "2026-07-11")
        self.assertEqual(len(content["douyin_titles"]), 3)
        self.assertTrue(content["voiceover_30s"])
        self.assertIn("hashtags", content["xiaohongshu_note"])
        self.assertGreaterEqual(len(content["video_topics"]), 3)


if __name__ == "__main__":
    unittest.main()
