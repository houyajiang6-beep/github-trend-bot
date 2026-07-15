from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from content_publishing_package import (
    CREATOR_READY_FILES,
    PACKAGE_FILES,
    build_creator_ready_packages,
    build_publishing_packages,
    load_content_outputs,
    publishing_main,
)


class ContentPublishingPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_dir = self.root / "content"
        self.output_root = self.root / "publishing"
        self.input_dir.mkdir()
        self.project = "mock/student-tool"
        self._write_inputs()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_inputs(self) -> None:
        posts = [
            {
                "project_name": self.project,
                "title": "资料太多找不到重点？21岁学生试着这样整理…",
                "cover_text": "别再逐页翻资料了",
                "target_user": "需要整理学习资料的学生",
                "pain_point": "复习时资料很多，很难快速找到重点",
                "pages": [
                    "封面：资料太多怎么办",
                    "复习时经常找不到上次看过的内容",
                    "先选择一份不含隐私的学习资料",
                    "打开工具并上传测试文件",
                    "用一个真实问题查看回答和原文位置",
                    "对照原文，检查结果是否准确",
                    "它适合辅助查找，但重要内容仍需自己核对",
                    "收藏这份方法，按自己的资料再试一次",
                ],
                "tags": ["学习方法", "学生党", "AI工具"],
                "personal_hook": "我今年21岁，复习时也经常在一堆资料里找不到重点。",
                "story_arc": [
                    "用户真实场景：考试前需要整理多份资料。",
                    "使用前的问题：逐页查找很费时间。",
                    "为什么我会测试：我想确认它是否真的适合学生独立使用。",
                ],
                "experiment_plan": [
                    "准备一份不含隐私的资料。",
                    "记录手动查找需要的步骤。",
                    "执行一次操作并保留失败截图。",
                    "回到原文核对结果。",
                ],
                "reflection": "我的个人判断：结果能核对、学生能独立完成，我才会推荐。",
            }
        ]
        videos = [
            {
                "project_name": self.project,
                "hook": "复习资料太多，你也经常找不到重点吗？",
                "problem": "逐页查找很费时间，还容易漏掉重要内容。",
                "solution": "可以先用工具辅助定位，再回到原文核对。",
                "demo": "录屏展示上传测试资料、提问和定位原文。",
                "ending": "它适合辅助查找，但不能替代自己的判断。",
                "cta": "先收藏，等有真实资料时再试一次。",
            }
        ]
        metadata = [
            {
                "project_name": self.project,
                "human_value_score": 82.5,
                "content_type": "xiaohongshu_post",
                "generated_time": "2026-07-12T20:00:00+08:00",
            },
            {
                "project_name": self.project,
                "human_value_score": 82.5,
                "content_type": "video_script",
                "generated_time": "2026-07-12T20:00:00+08:00",
            },
        ]
        for filename, data in (
            ("xiaohongshu_posts.json", posts),
            ("video_scripts.json", videos),
            ("content_metadata.json", metadata),
        ):
            (self.input_dir / filename).write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )

    def _build(self) -> tuple[dict, Path]:
        result = build_publishing_packages(
            self.input_dir,
            self.output_root,
            publish_date=date(2026, 7, 12),
        )
        return result, Path(result["output_directory"]) / "mock-student-tool"

    def test_loads_three_content_generator_outputs(self) -> None:
        loaded = load_content_outputs(self.input_dir)
        self.assertEqual(set(loaded), {"posts", "videos", "metadata"})
        self.assertEqual(len(loaded["posts"]), 1)

    def test_builds_one_directory_with_all_required_files(self) -> None:
        result, package_dir = self._build()
        self.assertEqual(result["package_count"], 1)
        self.assertEqual(
            {path.name for path in package_dir.iterdir()}, set(PACKAGE_FILES)
        )
        self.assertTrue((package_dir.parent / "manifest.json").exists())

    def test_xiaohongshu_post_has_three_titles_and_exactly_seven_pages(self) -> None:
        _, package_dir = self._build()
        text = (package_dir / "xiaohongshu_post.md").read_text(encoding="utf-8")
        title_block = text.split("## 推荐标题", 1)[0]
        self.assertEqual(
            sum(f"{index}. " in title_block for index in range(1, 4)), 3
        )
        self.assertEqual(text.count("### 第"), 7)
        self.assertIn("## 封面文字", text)
        self.assertIn("## 标签", text)

    def test_image_plan_describes_every_page_and_screenshot_need(self) -> None:
        _, package_dir = self._build()
        text = (package_dir / "image_plan.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("## 第"), 7)
        self.assertEqual(text.count("- 页面目的："), 7)
        self.assertEqual(text.count("- 建议图片："), 7)
        self.assertEqual(text.count("- 是否需要截图："), 7)
        self.assertIn("- 是否需要截图：是", text)

    def test_video_script_contains_hook_storyboard_voiceover_and_visuals(self) -> None:
        _, package_dir = self._build()
        text = (package_dir / "video_script.md").read_text(encoding="utf-8")
        for section in ("## Hook", "## 分镜", "## 旁白全文", "## 画面建议"):
            self.assertIn(section, text)
        self.assertEqual(text.count("### 镜头"), 6)

    def test_metadata_contract_and_checklist(self) -> None:
        _, package_dir = self._build()
        metadata = json.loads(
            (package_dir / "metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(metadata),
            {"project", "human_value_score", "content_angle", "generated_time"},
        )
        self.assertEqual(metadata["project"], self.project)
        self.assertEqual(metadata["human_value_score"], 82.5)
        datetime.fromisoformat(metadata["generated_time"])

        checklist = (package_dir / "publish_checklist.md").read_text(
            encoding="utf-8"
        )
        for phrase in ("是否删除", "真实测试", "普通用户", "21岁学生探索AI"):
            self.assertIn(phrase, checklist)

    def test_mismatched_projects_fail_without_partial_output(self) -> None:
        (self.input_dir / "video_scripts.json").write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "项目不一致"):
            build_publishing_packages(
                self.input_dir,
                self.output_root,
                publish_date=date(2026, 7, 12),
            )
        self.assertFalse((self.output_root / "2026-07-12").exists())

    def test_existing_date_directory_is_not_overwritten(self) -> None:
        existing = self.output_root / "2026-07-12"
        existing.mkdir(parents=True)
        marker = existing / "manual-note.md"
        marker.write_text("人工修改", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "避免覆盖人工修改"):
            build_publishing_packages(
                self.input_dir,
                self.output_root,
                publish_date=date(2026, 7, 12),
            )
        self.assertEqual(marker.read_text(encoding="utf-8"), "人工修改")

    def test_creator_ready_directory_and_all_output_contracts(self) -> None:
        result = build_creator_ready_packages(
            self.input_dir,
            self.root / "creator_ready",
            publish_date=date(2026, 7, 12),
        )
        package_dir = Path(result["output_directory"]) / "mock-student-tool"
        self.assertEqual(result["package_count"], 1)
        self.assertEqual(
            {path.name for path in package_dir.iterdir()},
            set(CREATOR_READY_FILES),
        )
        self.assertTrue((package_dir.parent / "manifest.json").exists())
        self.assertTrue((package_dir.parent / "daily_selection.md").exists())
        self.assertTrue((package_dir.parent / "daily_selection.json").exists())

        publish = (package_dir / "publish.txt").read_text(encoding="utf-8")
        self.assertIn("标题：\n", publish)
        self.assertIn("正文：\n", publish)
        self.assertIn("标签：\n", publish)
        self.assertIn("我今年21岁", publish)
        self.assertIn("我的实验过程", publish)
        self.assertIn("#学习方法", publish)
        self.assertNotIn("##", publish)
        self.assertNotIn("…", publish.split("\n", 3)[1])
        self.assertNotIn("这个 AI 工具，普通人值得用吗", publish)
        self.assertNotIn("21岁学生探索：", publish.split("\n", 3)[1])

        cover = (package_dir / "cover.txt").read_text(encoding="utf-8")
        for field in ("封面主标题：", "副标题：", "视觉建议："):
            self.assertIn(field, cover)

        plan = (package_dir / "image_generation_plan.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(plan.count("## 第"), 7)
        for field in ("页面目的：", "文字：", "画面描述：", "图片类型："):
            self.assertEqual(plan.count(field), 7)

        review = (package_dir / "creator_review.md").read_text(encoding="utf-8")
        for section in ("## 真实性", "## 个人IP感", "## 收藏价值", "## 是否像广告"):
            self.assertIn(section, review)

        performance = json.loads(
            (package_dir / "performance_tracking.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(performance),
            {
                "project",
                "published_time",
                "views",
                "likes",
                "favorites",
                "comments",
                "followers_gained",
            },
        )
        self.assertTrue(
            all(value is None for key, value in performance.items() if key != "project")
        )

        strategy = json.loads(
            (package_dir / "creator_strategy.json").read_text(encoding="utf-8")
        )
        self.assertIn("creator_strategy_score", strategy)
        self.assertIn("account_fit", strategy["scores"])

        selection = json.loads(
            (package_dir.parent / "daily_selection.json").read_text(encoding="utf-8")
        )
        self.assertEqual(selection["selected_project"], self.project)
        self.assertIn("counterexample_anchor", selection)

    def test_cli_generates_legacy_and_creator_ready_directories(self) -> None:
        legacy_root = self.root / "cli-publishing"
        creator_root = self.root / "cli-creator-ready"
        code = publishing_main(
            [
                "--input-dir",
                str(self.input_dir),
                "--output-root",
                str(legacy_root),
                "--creator-ready-root",
                str(creator_root),
                "--date",
                "2026-07-13",
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue((legacy_root / "2026-07-13" / "manifest.json").exists())
        self.assertTrue((creator_root / "2026-07-13" / "manifest.json").exists())

    def test_cli_returns_nonzero_for_missing_inputs(self) -> None:
        code = publishing_main(
            [
                "--input-dir",
                str(self.root / "missing"),
                "--output-root",
                str(self.output_root),
                "--creator-ready-root",
                str(self.root / "creator-ready"),
            ]
        )
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
