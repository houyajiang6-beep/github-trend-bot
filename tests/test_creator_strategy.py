from __future__ import annotations

import unittest

from creator_strategy import CreatorStrategyLayer, apply_strategy, title_specificity_score


def candidate(
    project: str, target: str, pain: str, angle: str, score: float
) -> tuple[dict, dict]:
    post = {
        "project_name": project,
        "title": "这个 AI 工具，普通人值得用吗？｜21岁学生探索",
        "cover_text": "这个 AI 工具\n普通人值得用吗？",
        "target_user": target,
        "pain_point": pain,
        "pages": [angle, "展示真实操作和结果对比"],
        "tags": ["AI工具"],
    }
    return post, {"human_value_score": score, "content_angle": angle}


class CreatorStrategyLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = CreatorStrategyLayer()

    def test_opencut_beats_high_human_value_off_topic_fitness_project(self) -> None:
        opencut, opencut_meta = candidate(
            "OpenCut-app/OpenCut",
            "需要剪辑短视频和vlog的学生与普通创作者",
            "剪映会员成本高，想找免费的开源视频编辑器",
            "录屏展示上传、剪辑、转场和导出的全过程",
            76.8,
        )
        fitness, fitness_meta = candidate(
            "hasaneyldrm/exercises-dataset",
            "健身爱好者和想在家锻炼的学生",
            "不知道不同肌群该练什么动作",
            "展示1324个动作GIF和按肌群浏览",
            82.8,
        )
        opencut_result = self.layer.evaluate(opencut, opencut_meta)
        fitness_result = self.layer.evaluate(fitness, fitness_meta)
        self.assertGreater(
            opencut_result["creator_strategy_score"],
            fitness_result["creator_strategy_score"],
        )
        self.assertEqual(opencut_result["decision"], "priority")
        self.assertEqual(fitness_result["decision"], "do_not_publish")

    def test_finance_candidate_is_blocked_even_when_human_value_is_high(self) -> None:
        post, metadata = candidate(
            "HKUDS/Vibe-Trading",
            "对量化交易感兴趣的学生或投资者",
            "想用AI自动下单并查看回测收益",
            "展示策略收益曲线和自动交易过程",
            90,
        )
        result = self.layer.evaluate(post, metadata)
        self.assertEqual(result["decision"], "do_not_publish")
        self.assertGreaterEqual(result["scores"]["brand_risk"], 70)
        self.assertTrue(result["hard_block_reasons"])

    def test_titles_are_specific_and_remove_account_slogan_template(self) -> None:
        post, metadata = candidate(
            "OpenCut-app/OpenCut",
            "需要剪辑短视频的学生",
            "持续做视频时剪辑成本太高",
            "展示从导入素材到导出视频",
            76.8,
        )
        original = dict(post)
        strategy = self.layer.evaluate(post, metadata)
        transformed = apply_strategy(post, strategy)
        self.assertEqual(post, original)
        self.assertIn("为了做AI账号", transformed["title"])
        self.assertNotIn("这个 AI 工具", transformed["title"])
        self.assertNotIn("21岁学生探索", transformed["title"])
        self.assertEqual(len(transformed["title_candidates"]), 3)
        self.assertGreater(title_specificity_score(transformed["title"]), 75)

    def test_output_exposes_dimensions_and_not_only_a_total_score(self) -> None:
        post, metadata = candidate(
            "mock/private-doc-chat",
            "需要从PDF找答案的大学生",
            "几十页资料很难快速找到重点",
            "上传PDF并用真实问题核对引用",
            84.4,
        )
        result = self.layer.evaluate(post, metadata)
        self.assertEqual(
            set(result["scores"]),
            {
                "account_fit",
                "audience_match",
                "creator_connection",
                "demonstrability",
                "trust_feasibility",
                "title_specificity",
                "brand_risk",
            },
        )
        self.assertIn("100页PDF", result["recommended_title"])
        for field in (
            "account_fit_score",
            "audience_match",
            "brand_risk",
            "reason",
            "why_now",
        ):
            self.assertIn(field, result)

    def test_project_level_source_metadata_blocks_developer_runtime(self) -> None:
        result = self.layer.evaluate_project(
            {
                "project_name": "oven-sh/bun",
                "source_description": "A fast JavaScript runtime, bundler and test runner",
                "source_topics": ["runtime", "javascript"],
                "human_value_score": 80,
                "target_user": "普通创作者",
                "why_people_care": "可能提升效率",
                "content_angle": "展示速度",
            }
        )
        self.assertEqual(result["category"], "developer_tool")
        self.assertEqual(result["decision"], "do_not_publish")


if __name__ == "__main__":
    unittest.main()
