from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from creator_strategy import CreatorStrategyLayer
from daily_editor_agent import DailyEditorAgent, render_daily_selection, write_daily_selection


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


class DailyEditorAgentTests(unittest.TestCase):
    def _strategies(self) -> list[dict]:
        layer = CreatorStrategyLayer()
        inputs = [
            candidate(
                "OpenCut-app/OpenCut",
                "需要剪辑短视频、vlog的学生和普通创作者",
                "持续做视频时剪辑成本太高",
                "录屏展示上传、剪辑、转场和导出的全过程",
                76.8,
            ),
            candidate(
                "hasaneyldrm/exercises-dataset",
                "健身爱好者和想在家锻炼的学生",
                "不知道不同肌群该练什么动作",
                "展示1324个动作GIF和按肌群浏览",
                82.8,
            ),
            candidate(
                "moeru-ai/airi",
                "想要AI虚拟伴侣和游戏陪玩的年轻人",
                "想要会语音聊天的AI美少女",
                "展示Live2D、语音和游戏互动，同时说明自托管门槛",
                80.8,
            ),
            candidate(
                "HKUDS/Vibe-Trading",
                "对量化交易感兴趣的学生",
                "想让AI自动下单",
                "展示收益曲线和自动交易过程",
                88,
            ),
        ]
        return [layer.evaluate(post, metadata) for post, metadata in inputs]

    def test_selects_opencut_instead_of_highest_human_value(self) -> None:
        result = DailyEditorAgent().rank(
            self._strategies(), publish_date=date(2026, 7, 14)
        )
        self.assertEqual(result["selected_project"], "OpenCut-app/OpenCut")
        self.assertEqual(result["top_candidates"][0]["rank"], 1)
        self.assertEqual(
            result["counterexample_anchor"]["project_name"], "HKUDS/Vibe-Trading"
        )

    def test_ranking_exposes_dimensions_and_blocked_reasons(self) -> None:
        result = DailyEditorAgent().rank(
            self._strategies(), publish_date="2026-07-14"
        )
        selected = result["top_candidates"][0]
        for dimension in (
            "human_value",
            "creator_strategy",
            "account_fit",
            "repetition_penalty",
            "brand_risk",
        ):
            self.assertIn(dimension, selected["dimensions"])
        blocked = [item for item in result["all_candidates"] if item["status"] == "blocked"]
        self.assertGreaterEqual(len(blocked), 2)
        self.assertTrue(all(item["hard_block_reasons"] for item in blocked))

    def test_daily_editor_only_combines_upstream_scores(self) -> None:
        result = DailyEditorAgent().rank(
            self._strategies(),
            publish_date="2026-07-14",
            rubric={
                "version": "test-v1",
                "weights": {"creator_strategy": 0.7, "human_value": 0.3},
                "repetition_penalty": 20,
            },
        )
        selected = result["top_candidates"][0]
        dimensions = selected["dimensions"]
        expected = round(
            dimensions["creator_strategy"] * 0.7
            + dimensions["human_value"] * 0.3,
            1,
        )
        self.assertEqual(selected["publish_score"], expected)
        self.assertEqual(result["rubric_version"], "test-v1")

    def test_markdown_has_one_clear_pick_and_counterexample_anchor(self) -> None:
        result = DailyEditorAgent().rank(
            self._strategies(), publish_date="2026-07-14"
        )
        text = render_daily_selection(result)
        self.assertIn("## 今日首选", text)
        self.assertIn("OpenCut-app/OpenCut", text)
        self.assertIn("## 反例锚点", text)
        self.assertIn("## 今日不发", text)
        self.assertIn("账号适配", text)

    def test_writes_json_and_markdown(self) -> None:
        result = DailyEditorAgent().rank(
            self._strategies(), publish_date="2026-07-14"
        )
        with TemporaryDirectory() as temporary:
            paths = write_daily_selection(Path(temporary), result)
            self.assertTrue(paths["markdown"].exists())
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_project"], "OpenCut-app/OpenCut")


if __name__ == "__main__":
    unittest.main()
