from __future__ import annotations

import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from config import BASE_DIR, Settings
from content_generator import (
    POST_FIELDS,
    STORY_FIELDS,
    VIDEO_FIELDS,
    ContentGeneratorMVP,
    load_human_value_report,
    write_content_package,
)


class ContentGeneratorMVPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = load_human_value_report(
            BASE_DIR / "mock_human_value_report.json"
        )
        cls.settings = Settings(report_timezone="Asia/Shanghai")

    def test_mock_report_contains_ten_projects(self) -> None:
        self.assertEqual(len(self.report["projects"]), 10)
        self.assertEqual(
            sum(
                1
                for project in self.report["projects"]
                if project["recommended_or_not"]
            ),
            5,
        )

    def test_templates_generate_three_output_contracts(self) -> None:
        package = ContentGeneratorMVP(self.settings).generate(
            self.report, use_llm=False
        )

        self.assertEqual(package["mode"], "templates_only")
        self.assertEqual(package["selected_projects"], 5)
        self.assertEqual(len(package["xiaohongshu_posts"]), 5)
        self.assertEqual(len(package["video_scripts"]), 5)
        self.assertEqual(len(package["content_metadata"]), 5)

        for post in package["xiaohongshu_posts"]:
            self.assertTrue(set(POST_FIELDS).issubset(post))
            self.assertTrue(5 <= len(post["pages"]) <= 8)
            self.assertTrue(3 <= len(post["tags"]) <= 5)
        for video in package["video_scripts"]:
            self.assertTrue(set(VIDEO_FIELDS).issubset(video))
        for metadata in package["content_metadata"]:
            self.assertEqual(
                set(metadata),
                {
                    "project_name",
                    "human_value_score",
                    "content_types",
                    "generated_time",
                },
            )
            datetime.fromisoformat(metadata["generated_time"])

    def test_generated_copy_has_persona_and_avoids_developer_jargon(self) -> None:
        package = ContentGeneratorMVP(self.settings).generate(
            self.report, use_llm=False
        )
        banned = re.compile(
            r"\b(GitHub|CLI|SDK|API|RAG|MCP|framework|Docker|Kubernetes)\b",
            re.IGNORECASE,
        )
        inflated = ("神器", "零门槛", "秒杀", "100%", "万能", "一定能", "实测")

        for post in package["xiaohongshu_posts"]:
            text = " ".join(
                [
                    post["title"],
                    post["cover_text"],
                    post["target_user"],
                    post["pain_point"],
                    *post["pages"],
                    post["personal_hook"],
                    *post["story_arc"],
                    *post["experiment_plan"],
                    post["reflection"],
                ]
            )
            self.assertTrue("21岁" in text or "21 岁" in text)
            self.assertIsNone(banned.search(text))
            self.assertFalse(any(word in text for word in inflated))

        for video in package["video_scripts"]:
            text = " ".join(str(video[key]) for key in VIDEO_FIELDS)
            self.assertTrue("21岁" in text or "21 岁" in text)
            self.assertIsNone(banned.search(text))
            self.assertFalse(any(word in text for word in inflated))

    def test_llm_output_is_cleaned_and_normalized(self) -> None:
        def fake_llm(projects, settings):
            return [
                {
                    "project_name": project["project_name"],
                    "xiaohongshu": {
                        "title": "GitHub API 神器 100% 有效",
                        "cover_text": "普通人也能用",
                        "target_user": project["target_user"],
                        "pain_point": project["why_people_care"],
                        "pages": ["第一页", "第二页", "第三页", "第四页", "第五页"],
                        "tags": ["#AI工具", "#学生成长", "#真实体验"],
                    },
                    "video": {
                        "hook": "这个 CLI 工具太强了",
                        "problem": "整理资料很慢",
                        "solution": "通过 API 解决",
                        "demo": "展示真实操作",
                        "ending": "不一定适合所有人",
                        "cta": "先收藏再判断",
                    },
                }
                for project in projects
            ]

        package = ContentGeneratorMVP(
            self.settings, llm_evaluator=fake_llm
        ).generate(self.report)

        self.assertEqual(package["mode"], "llm_and_templates")
        post = package["xiaohongshu_posts"][0]
        video = package["video_scripts"][0]
        self.assertNotIn("GitHub", post["title"])
        self.assertNotIn("API", post["title"])
        self.assertNotIn("神器", post["title"])
        self.assertNotIn("100%", post["title"])
        self.assertNotIn("CLI", video["hook"])
        self.assertTrue("21岁" in video["hook"] or "21 岁" in video["hook"])
        self.assertEqual(post["tags"], ["AI工具", "学生成长", "真实体验"])
        self.assertTrue(set(STORY_FIELDS).issubset(post))

    def test_same_project_before_and_after_story_layer_comparison(self) -> None:
        project = next(
            item for item in self.report["projects"] if item["recommended_or_not"]
        )
        before = {
            "project_name": project["project_name"],
            "title": "21岁学生探索AI工具",
            "target_user": project["target_user"],
            "pain_point": project["why_people_care"],
            "pages": [
                "介绍工具是什么",
                "说明适合谁",
                "列出使用场景",
                "展示操作",
                "总结工具价值",
            ],
        }
        after = ContentGeneratorMVP(self.settings).generate(
            {**self.report, "projects": [project]}, use_llm=False
        )["xiaohongshu_posts"][0]

        self.assertTrue(set(STORY_FIELDS).isdisjoint(before))
        self.assertTrue(set(STORY_FIELDS).issubset(after))
        self.assertEqual(before["project_name"], after["project_name"])
        self.assertEqual(before["target_user"], after["target_user"])
        comparison_text = " ".join(
            [
                after["personal_hook"],
                *after["story_arc"],
                *after["experiment_plan"],
                after["reflection"],
                *after["pages"],
            ]
        )
        for story_element in (
            "用户真实场景",
            "使用前的问题",
            "为什么我会测试",
            "我的实验过程",
            "我的个人判断",
        ):
            self.assertIn(story_element, comparison_text)
        self.assertNotEqual(before["pages"], after["pages"])

    def test_llm_failure_falls_back_without_losing_content(self) -> None:
        def failed_llm(projects, settings):
            raise TimeoutError("test timeout")

        package = ContentGeneratorMVP(
            self.settings, llm_evaluator=failed_llm
        ).generate(self.report)

        self.assertEqual(package["mode"], "templates_fallback")
        self.assertTrue(package["llm_failed"])
        self.assertEqual(len(package["xiaohongshu_posts"]), 5)
        self.assertEqual(len(package["video_scripts"]), 5)

    def test_unverified_llm_claim_is_rejected_for_safe_template(self) -> None:
        def unsafe_llm(projects, settings):
            return [
                {
                    "project_name": project["project_name"],
                    "xiaohongshu": {
                        "title": "我已经实测过这个工具",
                        "cover_text": "效率提升100%",
                        "target_user": project["target_user"],
                        "pain_point": project["why_people_care"],
                        "pages": ["我用了它", "第二页", "第三页", "第四页", "第五页"],
                        "tags": ["AI工具", "学生成长", "工具观察"],
                    },
                    "video": {
                        "hook": "我测试了这个工具",
                        "problem": "问题",
                        "solution": "方案",
                        "demo": "我把资料放进去",
                        "ending": "面试机会变多了",
                        "cta": "关注我",
                    },
                }
                for project in projects
            ]

        package = ContentGeneratorMVP(
            self.settings, llm_evaluator=unsafe_llm
        ).generate(self.report)

        self.assertEqual(package["mode"], "templates_fallback")
        self.assertEqual(package["rejected_llm_projects"], 5)
        self.assertTrue(
            all("实测" not in post["title"] for post in package["xiaohongshu_posts"])
        )

    def test_unsafe_claim_inside_story_layer_is_also_rejected(self) -> None:
        def unsafe_story_llm(projects, settings):
            return [
                {
                    "project_name": project["project_name"],
                    "xiaohongshu": {
                        "title": "这个工具适合普通用户吗",
                        "cover_text": "先看真实场景",
                        "target_user": project["target_user"],
                        "pain_point": project["why_people_care"],
                        "pages": ["第一页", "第二页", "第三页", "第四页", "第五页"],
                        "tags": ["AI工具", "学生成长", "工具观察"],
                        "personal_hook": "我已经测试过，效果一定很好",
                        "story_arc": ["用户场景", "使用前问题", "测试原因"],
                        "experiment_plan": ["准备任务", "执行操作", "核对结果"],
                        "reflection": "推荐所有人使用",
                    },
                    "video": {
                        "hook": "21岁学生观察一个新工具",
                        "problem": "问题",
                        "solution": "方案",
                        "demo": "拍摄时展示操作",
                        "ending": "先判断适用场景",
                        "cta": "先收藏",
                    },
                }
                for project in projects
            ]

        package = ContentGeneratorMVP(
            self.settings, llm_evaluator=unsafe_story_llm
        ).generate(self.report)

        self.assertEqual(package["mode"], "templates_fallback")
        self.assertEqual(package["rejected_llm_projects"], 5)
        self.assertTrue(
            all("已经测试" not in post["personal_hook"] for post in package["xiaohongshu_posts"])
        )

    def test_only_recommended_projects_are_generated(self) -> None:
        package = ContentGeneratorMVP(self.settings).generate(
            self.report, use_llm=False
        )
        names = {
            item["project_name"] for item in package["xiaohongshu_posts"]
        }
        rejected_names = {
            item["project_name"]
            for item in self.report["projects"]
            if not item["recommended_or_not"]
        }
        self.assertTrue(names.isdisjoint(rejected_names))

    def test_output_files_are_valid_json(self) -> None:
        package = ContentGeneratorMVP(self.settings).generate(
            self.report, use_llm=False
        )
        with TemporaryDirectory() as directory:
            paths = write_content_package(Path(directory), package)
            loaded = {
                key: json.loads(path.read_text(encoding="utf-8"))
                for key, path in paths.items()
            }

        self.assertEqual(len(loaded["xiaohongshu_posts"]), 5)
        self.assertEqual(loaded["xiaohongshu_post"], loaded["xiaohongshu_posts"])
        self.assertEqual(len(loaded["video_scripts"]), 5)
        self.assertEqual(len(loaded["content_metadata"]), 5)


if __name__ == "__main__":
    unittest.main()
