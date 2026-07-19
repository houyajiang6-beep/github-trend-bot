from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import daily_report_pipeline
from config import Settings
from creator_delivery import create_creator_ready_zip, load_creator_delivery
from daily_report_pipeline import DailyReportContext, finalize_daily_report


def write_creator_ready(root: Path, report_date: str = "2026-07-18") -> Path:
    creator = root / "outputs" / "creator_ready" / report_date
    creator.mkdir(parents=True)
    candidates = [
        {
            "project_name": "alpha/tool",
            "recommended_title": "首选标题",
            "publish_score": 88.5,
            "why_now": "今天适合发布",
        },
        {
            "project_name": "beta/tool",
            "recommended_title": "第二标题",
            "publish_score": 80,
        },
        {
            "project_name": "gamma/tool",
            "recommended_title": "第三标题",
            "publish_score": 75,
        },
    ]
    (creator / "daily_selection.json").write_text(
        json.dumps(
            {
                "publish_date": report_date,
                "selected_project": "alpha/tool",
                "selected_title": "首选标题",
                "top_candidates": candidates,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (creator / "publish.txt").write_text(
        "标题：\n首选标题\n\n正文：\n这是可直接复制的正文。\n\n标签：\n#AI工具 #学生\n",
        encoding="utf-8",
    )
    (creator / "cover.txt").write_text(
        "封面主标题：\n一个好工具\n\n副标题：\n学生也能用\n\n视觉建议：\n真人出镜\n",
        encoding="utf-8",
    )
    (creator / "prediction.json").write_text(
        json.dumps(
            {
                "project_name": "alpha/tool",
                "publish_date": report_date,
                "prediction": {"publish_score": 88.5},
            }
        ),
        encoding="utf-8",
    )
    (creator / "manifest.json").write_text(
        json.dumps(
            {
                "packages": [
                    {"project": "alpha/tool", "generation_mode": "full_llm"},
                    {"project": "beta/tool", "generation_mode": "rules_fallback"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return creator


def context_for(root: Path, report_date: str = "2026-07-18") -> DailyReportContext:
    cfg = Settings(
        report_dir=root / "reports",
        log_dir=root / "logs",
        creator_output_dir=root / "outputs",
    )
    return DailyReportContext(
        report_date=report_date,
        repositories=[],
        analysis={},
        market_insight={},
        growth_metrics={},
        status={
            "social_content": {"success": False, "fallback": True},
            "report": {"success": False},
            "market_insight": {"success": False, "fallback": True},
            "gmail": {"success": False, "skipped": False},
        },
        cfg=cfg,
    )


class CreatorDeliveryTests(unittest.TestCase):
    def _finalize_and_get_email(
        self, root: Path, creator_status: dict
    ) -> tuple[str, str, dict]:
        context = context_for(root)
        with (
            patch.object(
                daily_report_pipeline,
                "render_report",
                return_value=("原 GitHub 日报", "<html><body>原 GitHub 日报</body></html>"),
            ),
            patch.object(daily_report_pipeline, "send_email") as send_email,
        ):
            finalize_daily_report(
                context,
                {},
                social_success=True,
                social_fallback=False,
                dry_run=False,
                creator_status=creator_status,
            )
        return send_email.call_args.args[1], send_email.call_args.args[2], send_email.call_args.kwargs

    def test_success_email_contains_existing_top_pick_and_attachment(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            creator = write_creator_ready(root)
            status = {
                "status": "success",
                "publish_date": "2026-07-18",
                "content_generation_mode": "full_llm",
                "candidate_count": 3,
                "fallback_projects": [],
                "output": str(creator),
            }
            plain, html_body, kwargs = self._finalize_and_get_email(root, status)

            self.assertIn("原 GitHub 日报", plain)
            self.assertIn("今日小红书首选", plain)
            self.assertIn("这是可直接复制的正文。", plain)
            self.assertIn("生成日期：2026-07-18", plain)
            self.assertIn("full_llm", plain)
            self.assertIn("今日小红书首选", html_body)
            self.assertEqual(len(kwargs["attachments"]), 1)
            self.assertEqual(kwargs["attachments"][0].name, "creator-ready-2026-07-18.zip")

    def test_degraded_email_names_partial_fallback_projects_and_reason(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            creator = write_creator_ready(root)
            status = {
                "status": "degraded",
                "publish_date": "2026-07-18",
                "content_generation_mode": "partial_fallback",
                "candidate_count": 3,
                "fallback_projects": ["beta/tool"],
                "degraded_reason": "beta/tool 的 LLM 内容未通过校验",
                "output": str(creator),
            }
            plain, _, _ = self._finalize_and_get_email(root, status)

        self.assertIn("Creator Pipeline 状态：DEGRADED", plain)
        self.assertIn("Content Generator 模式：partial_fallback", plain)
        self.assertIn("fallback 项目：beta/tool", plain)
        self.assertIn("beta/tool 的 LLM 内容未通过校验", plain)

    def test_failed_creator_still_sends_original_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plain, _, kwargs = self._finalize_and_get_email(
                root,
                {
                    "status": "failed",
                    "publish_date": "2026-07-18",
                    "reason_code": "CREATOR_PIPELINE_FAILED",
                    "output": None,
                },
            )

        self.assertIn("原 GitHub 日报", plain)
        self.assertIn("Creator Pipeline 状态：FAILED", plain)
        self.assertIsNone(kwargs["attachments"])

    def test_missing_daily_selection_is_handled_without_exception(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            creator = root / "outputs" / "creator_ready" / "2026-07-18"
            creator.mkdir(parents=True)
            plain, _, _ = self._finalize_and_get_email(
                root,
                {
                    "status": "success",
                    "publish_date": "2026-07-18",
                    "output": str(creator),
                },
            )

        self.assertIn("MISSING_CREATOR_READY_FILES", plain)
        self.assertIn("今日 Creator Ready 内容不可用", plain)

    def test_zip_excludes_sensitive_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            creator = write_creator_ready(root)
            (creator / ".env").write_text("SECRET=1", encoding="utf-8")
            (creator / "token.json").write_text("secret", encoding="utf-8")
            logs = creator / "logs"
            logs.mkdir()
            (logs / "private.log").write_text("secret", encoding="utf-8")
            archive = create_creator_ready_zip(
                creator, root / "reports", "2026-07-18"
            )
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()

        self.assertTrue(any(name.endswith("publish.txt") for name in names))
        self.assertFalse(any(".env" in name for name in names))
        self.assertFalse(any("token" in name.lower() for name in names))
        self.assertFalse(any("logs/" in name for name in names))

    def test_workflow_uploads_only_required_delivery_paths(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily.yml"
        ).read_text(encoding="utf-8")
        for path in ("logs/", "reports/", "outputs/creator_ready/"):
            self.assertIn(path, workflow)
        self.assertIn("retention-days: 14", workflow)
        self.assertIn("Artifact 已上传", workflow)


if __name__ == "__main__":
    unittest.main()
