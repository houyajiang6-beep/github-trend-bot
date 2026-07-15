from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


class DailyEditorAgent:
    """Deterministically order pre-scored candidates for the current day.

    Human Value owns project value and Creator Strategy owns account fit. This
    class deliberately adds no new semantic score; it only combines those two
    results and applies a recent-topic repetition penalty.
    """

    def rank(
        self,
        strategies: Iterable[Mapping[str, Any]],
        *,
        publish_date: date | str,
        previous_categories: Iterable[str] = (),
        rubric: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        rubric = dict(rubric or {})
        weights = dict(rubric.get("weights") or {})
        strategy_weight = float(weights.get("creator_strategy", 0.7))
        human_weight = float(weights.get("human_value", 0.3))
        repetition_penalty_value = float(rubric.get("repetition_penalty", 20.0))
        if abs(strategy_weight + human_weight - 1.0) > 0.001:
            raise ValueError("Daily Editor 权重之和必须为 1")
        previous = list(previous_categories)
        items: list[dict[str, Any]] = []
        for raw in strategies:
            strategy = dict(raw)
            scores = dict(strategy.get("scores") or {})
            category = str(strategy.get("category") or "general_utility")
            risk = float(scores.get("brand_risk") or 0)
            repetition_penalty = (
                repetition_penalty_value
                if previous and category == previous[-1]
                else 0.0
            )
            strategy_score = float(strategy.get("creator_strategy_score") or 0)
            human = float(strategy.get("human_value_score") or 0)
            publish_score = _clamp(
                strategy_score * strategy_weight
                + human * human_weight
                - repetition_penalty
            )
            blocked = strategy.get("decision") == "do_not_publish"
            if blocked:
                publish_score = min(publish_score, 39.0)
            item = {
                "project_name": strategy.get("project_name"),
                "category": category,
                "publish_score": publish_score,
                "status": "blocked" if blocked else "candidate",
                "recommended_title": strategy.get("recommended_title"),
                "title_candidates": strategy.get("title_candidates", []),
                "why_now": strategy.get("why_now"),
                "risk": risk,
                "hard_block_reasons": strategy.get("hard_block_reasons", []),
                "dimensions": {
                    "human_value": human,
                    "creator_strategy": strategy_score,
                    "account_fit": float(scores.get("account_fit") or 0),
                    "repetition_penalty": repetition_penalty,
                    "brand_risk": risk,
                },
            }
            items.append(item)

        items.sort(
            key=lambda item: (
                item["status"] != "blocked",
                item["publish_score"],
                item["dimensions"]["account_fit"],
            ),
            reverse=True,
        )
        publishable = [item for item in items if item["status"] != "blocked"]
        top = publishable[:3]
        for index, item in enumerate(top, start=1):
            item["rank"] = index
        selected = top[0] if top else None

        counterexample = None
        if selected:
            others = [item for item in items if item is not selected]
            if others:
                anchor = max(others, key=lambda item: item["dimensions"]["human_value"])
                counterexample = {
                    "project_name": anchor["project_name"],
                    "human_value_score": anchor["dimensions"]["human_value"],
                    "publish_score": anchor["publish_score"],
                    "reason": (
                        "工具本身的 Human Value 不低，但账号适配、风险或受众门槛使它不应排在今日首选前。"
                    ),
                }
        return {
            "publish_date": publish_date.isoformat() if isinstance(publish_date, date) else str(publish_date),
            "selected_project": selected["project_name"] if selected else None,
            "selected_title": selected["recommended_title"] if selected else None,
            "rubric_version": str(rubric.get("version") or "daily-v1"),
            "top_candidates": top,
            "counterexample_anchor": counterexample,
            "all_candidates": items,
        }


def render_daily_selection(selection: Mapping[str, Any]) -> str:
    lines = [
        f"# {selection['publish_date']} Daily Editor 发布决策",
        "",
        "## 今日首选",
        "",
    ]
    top = list(selection.get("top_candidates") or [])
    if not top:
        lines.extend(["今天没有达到发布门槛的候选。", ""])
    else:
        selected = top[0]
        lines.extend(
            [
                f"**{selected['project_name']}**",
                "",
                f"推荐标题：{selected['recommended_title']}",
                f"Publish Score：{selected['publish_score']}",
                f"为什么现在发：{selected['why_now']}",
                "发布前动作：先完成真实操作或录屏；没有证据时使用‘准备测试’，不要写成‘已经实测’。",
                "",
                "## Top 3 多维排序",
                "",
            ]
        )
        for item in top:
            dimensions = item["dimensions"]
            lines.extend(
                [
                    f"### Rank {item['rank']}｜{item['project_name']}",
                    "",
                    f"- 推荐标题：{item['recommended_title']}",
                    f"- Publish Score：{item['publish_score']}",
                    f"- Human Value：{dimensions['human_value']}",
                    f"- Creator Strategy：{dimensions['creator_strategy']}",
                    f"- 账号适配：{dimensions['account_fit']}",
                    f"- 重复主题扣分：{dimensions['repetition_penalty']}",
                    f"- 品牌风险：{dimensions['brand_risk']}（越低越好）",
                    f"- 判断：{item['why_now']}",
                    "",
                ]
            )
    anchor = selection.get("counterexample_anchor")
    if anchor:
        lines.extend(
            [
                "## 反例锚点",
                "",
                f"{anchor['project_name']} 的 Human Value 是 {anchor['human_value_score']}，"
                f"但 Publish Score 只有 {anchor['publish_score']}。{anchor['reason']}",
                "",
            ]
        )
    blocked = [item for item in selection.get("all_candidates", []) if item["status"] == "blocked"]
    if blocked:
        lines.extend(["## 今日不发", ""])
        for item in blocked:
            reasons = "；".join(item.get("hard_block_reasons") or ["不符合当前账号策略"])
            lines.append(f"- {item['project_name']}：{reasons}。")
        lines.append("")
    return "\n".join(lines)


def write_daily_selection(
    output_dir: Path, selection: Mapping[str, Any], *, markdown_name: str = "daily_selection.md"
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / markdown_name
    json_path = output_dir / "daily_selection.json"
    markdown_path.write_text(render_daily_selection(selection), encoding="utf-8")
    json_path.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"markdown": markdown_path, "json": json_path}
