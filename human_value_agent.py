from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import yaml

from config import BASE_DIR, Settings, settings


LOGGER = logging.getLogger("human-value-agent")

DIMENSION_KEYS = (
    "normal_user_value",
    "usage_threshold",
    "scenario_count",
    "visual_value",
    "viral_potential",
    "business_value",
    "timeliness",
)

LLMEvaluator = Callable[
    [list[dict[str, Any]], dict[str, Any], Settings], list[dict[str, Any]]
]


def load_rules(path: Path) -> dict[str, Any]:
    """Load and validate the Human Value rubric."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"无法读取评分规则 {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("human_value_rules.yaml 顶层必须是对象")

    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSION_KEYS):
        raise ValueError("评分规则必须完整包含 7 个 Human Value 维度")
    try:
        weights = [int(dimensions[key]["weight"]) for key in DIMENSION_KEYS]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("每个评分维度都必须包含整数 weight") from exc
    if sum(weights) != 100:
        raise ValueError(f"评分权重之和必须为 100，当前为 {sum(weights)}")

    recommendation = payload.get("recommendation", {})
    threshold = recommendation.get("threshold")
    if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 100:
        raise ValueError("recommendation.threshold 必须在 0 到 100 之间")
    return payload


def load_projects(path: Path) -> list[dict[str, Any]]:
    """Accept a list, {projects: [...]}, or the bot's {repositories: [...]} report."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取项目文件 {path}: {exc}") from exc

    if isinstance(payload, list):
        projects = payload
    elif isinstance(payload, dict):
        projects = payload.get("projects", payload.get("repositories"))
    else:
        projects = None
    if not isinstance(projects, list) or not projects:
        raise ValueError("输入必须是非空项目数组，或包含 projects/repositories 数组")
    if any(not isinstance(project, dict) for project in projects):
        raise ValueError("每个项目必须是 JSON 对象")
    for index, project in enumerate(projects, start=1):
        if not _project_name(project):
            raise ValueError(f"第 {index} 个项目缺少 full_name/project_name/name")
    return projects


def _project_name(project: dict[str, Any]) -> str:
    return str(
        project.get("project_name")
        or project.get("full_name")
        or project.get("name")
        or ""
    ).strip()


def _project_text(project: dict[str, Any]) -> str:
    topics = project.get("topics", [])
    if not isinstance(topics, list):
        topics = []
    return " ".join(
        [
            _project_name(project),
            str(project.get("description") or ""),
            str(project.get("readme") or ""),
            str(project.get("language") or ""),
            " ".join(str(topic) for topic in topics),
        ]
    ).lower()


def _signal_count(text: str, signals: Any) -> int:
    if not isinstance(signals, list):
        return 0
    return sum(1 for signal in signals if str(signal).lower() in text)


def _clamp_score(value: Any) -> int:
    try:
        number = round(float(value))
    except (TypeError, ValueError):
        number = 1
    return max(1, min(5, number))


def rule_scores(project: dict[str, Any], rules: dict[str, Any]) -> dict[str, int]:
    """Produce a transparent, deterministic baseline before LLM judgment."""
    text = _project_text(project)
    signals = rules.get("rule_signals", {})
    ordinary_count = _signal_count(text, signals.get("ordinary_value"))
    easy_count = _signal_count(text, signals.get("easy_access"))
    barrier_count = _signal_count(text, signals.get("technical_barrier"))
    visual_count = _signal_count(text, signals.get("visual"))
    viral_count = _signal_count(text, signals.get("viral"))
    business_count = _signal_count(text, signals.get("business"))

    normal_user_value = 2
    if ordinary_count >= 1:
        normal_user_value += 1
    if ordinary_count >= 3:
        normal_user_value += 1
    if ordinary_count >= 6:
        normal_user_value += 1
    if barrier_count >= 3 and ordinary_count < 3:
        normal_user_value -= 1

    usage_threshold = 3
    if easy_count >= 1:
        usage_threshold += 1
    if easy_count >= 3:
        usage_threshold += 1
    if barrier_count >= 1:
        usage_threshold -= 1
    if barrier_count >= 3:
        usage_threshold -= 1

    scenario_groups = rules.get("scenario_groups", {})
    matched_groups = 0
    if isinstance(scenario_groups, dict):
        matched_groups = sum(
            1
            for keywords in scenario_groups.values()
            if _signal_count(text, keywords) > 0
        )
    scenario_count = {0: 1, 1: 2, 2: 3, 3: 4}.get(matched_groups, 5)

    visual_value = 2
    if visual_count >= 1:
        visual_value += 1
    if visual_count >= 3:
        visual_value += 1
    if visual_count >= 6:
        visual_value += 1
    if barrier_count >= 3 and visual_count == 0:
        visual_value -= 1

    try:
        stars_today = int(project.get("stars_today") or 0)
    except (TypeError, ValueError):
        stars_today = 0

    viral_potential = 2
    if viral_count >= 2:
        viral_potential += 1
    if stars_today >= 200:
        viral_potential += 1
    if stars_today >= 600 or visual_value >= 5:
        viral_potential += 1
    if normal_user_value <= 2:
        viral_potential -= 1

    business_value = 2
    if business_count >= 1:
        business_value += 1
    if business_count >= 3:
        business_value += 1
    if business_count >= 6:
        business_value += 1
    if normal_user_value <= 2:
        business_value -= 1

    if stars_today >= 600:
        timeliness = 5
    elif stars_today >= 250:
        timeliness = 4
    elif stars_today >= 80:
        timeliness = 3
    elif stars_today >= 20:
        timeliness = 2
    else:
        timeliness = 1

    return {
        "normal_user_value": _clamp_score(normal_user_value),
        "usage_threshold": _clamp_score(usage_threshold),
        "scenario_count": _clamp_score(scenario_count),
        "visual_value": _clamp_score(visual_value),
        "viral_potential": _clamp_score(viral_potential),
        "business_value": _clamp_score(business_value),
        "timeliness": _clamp_score(timeliness),
    }


def _fallback_copy(project: dict[str, Any], scores: dict[str, int]) -> dict[str, str]:
    # Copy fallback should follow the project's primary description. README
    # often lists dozens of secondary examples (for example image/video APIs)
    # that can otherwise mislabel a developer runtime as a creator product.
    text = " ".join(
        [
            _project_name(project),
            str(project.get("description") or ""),
            " ".join(str(item) for item in (project.get("topics") or [])),
        ]
    ).lower()
    name = _project_name(project)
    if any(word in text for word in ("resume", "job", "portfolio", "interview")):
        target = "正在求职、准备作品集的学生和职场新人"
        care = "它可能减少改简历和准备求职材料的重复劳动。"
        angle = "普通人不用懂技术，也能更快准备一份针对目标岗位的求职材料"
    elif any(word in text for word in ("image", "video", "design", "creator")):
        target = "需要做图片、视频或社媒素材的普通创作者"
        care = "它可能把耗时的素材处理变成直观的前后对比。"
        angle = "不会专业软件的人，也能快速做出可直接发布的视觉结果"
    elif any(word in text for word in ("pdf", "document", "notes", "meeting")):
        target = "需要整理资料、课程或会议信息的学生和职场新人"
        care = "它可能减少查资料、整理重点和提炼行动项的时间。"
        angle = "把一堆资料变成可以直接提问和复习的个人助手"
    elif any(word in text for word in ("home", "personal", "privacy")):
        target = "重视隐私和个人效率的普通用户"
        care = "它把抽象技术转化为个人数据控制或生活效率提升。"
        angle = "数据留在自己手里，同时让重复的生活任务自动完成"
    elif scores["normal_user_value"] <= 2:
        target = "开发者或相关技术从业者"
        care = "它对普通用户的直接价值暂不明确。"
        angle = f"为什么 {name} 虽然热门，却不适合普通用户跟风收藏"
    else:
        target = "希望用 AI 提升学习和工作效率的普通用户"
        care = "它可能把一个专业流程简化为普通人可获得的具体结果。"
        angle = "先展示结果，再判断普通用户是否真的值得花时间尝试"
    return {
        "target_user": target,
        "why_people_care": care,
        "content_angle": angle,
    }


def _compact_project(project: dict[str, Any], readme_limit: int) -> dict[str, Any]:
    topics = project.get("topics", [])
    return {
        "project_name": _project_name(project),
        "description": str(project.get("description") or ""),
        "readme_excerpt": str(project.get("readme") or "")[:readme_limit],
        "language": str(project.get("language") or ""),
        "topics": topics if isinstance(topics, list) else [],
        "stars": project.get("stars", 0),
        "stars_today": project.get("stars_today", 0),
        "rank": project.get("rank"),
    }


def evaluate_with_deepseek(
    projects: list[dict[str, Any]], rules: dict[str, Any], cfg: Settings
) -> list[dict[str, Any]]:
    """Ask the LLM for semantic judgment; deterministic rules remain authoritative anchors."""
    cfg.validate_ai()
    from openai import OpenAI

    blend = rules.get("blend", {})
    readme_limit = int(blend.get("readme_max_chars", 2500))
    dimension_prompt = {
        key: {
            "question": rules["dimensions"][key].get("question", ""),
            "anchors": rules["dimensions"][key].get("anchors", {}),
        }
        for key in DIMENSION_KEYS
    }
    prompt = f"""
你是面向小红书普通用户的 AI 产品研究员。请判断 GitHub 项目对非程序员的真实价值，而不是评价代码质量。

目标用户：{rules.get('audience', '')}

评分规则（每项 1-5，usage_threshold 越高代表越容易使用）：
{json.dumps(dimension_prompt, ensure_ascii=False)}

表达要求：
{json.dumps(rules.get('output_language_rules', []), ensure_ascii=False)}

项目：
{json.dumps([_compact_project(project, readme_limit) for project in projects], ensure_ascii=False)}

只输出 JSON 对象：{{"projects": [...]}}。
每项必须包含 project_name、scores、target_user、why_people_care、content_angle。
scores 必须完整包含：{', '.join(DIMENSION_KEYS)}，每项为 1 到 5 的整数。
project_name 必须逐字来自输入。不要输出 human_value_score 或推荐结论，它们由程序按固定规则计算。
""".strip()

    client = OpenAI(
        api_key=cfg.deepseek_api_key,
        base_url=cfg.deepseek_base_url,
        timeout=cfg.ai_request_timeout,
        max_retries=0,
    )
    response = client.chat.completions.create(
        model=cfg.deepseek_model,
        messages=[
            {
                "role": "system",
                "content": "只输出有效 JSON。把技术功能翻译成普通人的问题、收益和场景，不得编造亲测。",
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=6000,
        extra_body={
            "thinking": {"type": "enabled" if cfg.deepseek_thinking else "disabled"}
        },
    )
    content = response.choices[0].message.content
    payload = json.loads(content)
    items = payload.get("projects") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("LLM 输出缺少 projects 数组")
    return [item for item in items if isinstance(item, dict)]


class HumanValueAgent:
    def __init__(
        self,
        rules: dict[str, Any],
        cfg: Settings = settings,
        llm_evaluator: LLMEvaluator = evaluate_with_deepseek,
    ) -> None:
        self.rules = rules
        self.cfg = cfg
        self.llm_evaluator = llm_evaluator

    def evaluate(
        self, projects: list[dict[str, Any]], *, use_llm: bool = True
    ) -> dict[str, Any]:
        blend = self.rules.get("blend", {})
        batch_size = max(1, int(blend.get("batch_size", 10)))
        llm_weight = float(blend.get("llm_weight", 0.7))
        rule_weight = float(blend.get("rule_weight", 0.3))
        if abs((llm_weight + rule_weight) - 1.0) > 0.001:
            raise ValueError("LLM 与规则融合权重之和必须为 1")

        llm_by_name: dict[str, dict[str, Any]] = {}
        failed_batches = 0
        if use_llm:
            for start in range(0, len(projects), batch_size):
                batch = projects[start : start + batch_size]
                try:
                    for item in self.llm_evaluator(batch, self.rules, self.cfg):
                        name = str(item.get("project_name") or "").strip()
                        if name:
                            llm_by_name[name] = item
                except Exception as exc:
                    failed_batches += 1
                    LOGGER.warning(
                        "LLM Human Value 评分失败，当前批次使用规则降级：%s",
                        exc.__class__.__name__,
                    )

        results = [
            self._evaluate_one(project, llm_by_name.get(_project_name(project)), llm_weight, rule_weight)
            for project in projects
        ]
        llm_scored = sum(1 for result in results if result.pop("_llm_scored", False))
        return {
            "generated_at": datetime.now(ZoneInfo(self.cfg.report_timezone)).isoformat(),
            "rules_version": str(self.rules.get("version", "unknown")),
            "mode": (
                "rules_only"
                if not use_llm
                else "llm_and_rules"
                if llm_scored == len(results)
                else "partial_fallback"
                if llm_scored
                else "rules_fallback"
            ),
            "llm_scored_projects": llm_scored,
            "rule_fallback_projects": len(results) - llm_scored,
            "failed_llm_batches": failed_batches,
            "projects": results,
        }

    def _evaluate_one(
        self,
        project: dict[str, Any],
        llm_item: dict[str, Any] | None,
        llm_weight: float,
        rule_weight: float,
    ) -> dict[str, Any]:
        baseline = rule_scores(project, self.rules)
        fallback_copy = _fallback_copy(project, baseline)
        llm_scores = llm_item.get("scores") if isinstance(llm_item, dict) else None
        llm_valid = isinstance(llm_scores, dict) and all(
            key in llm_scores for key in DIMENSION_KEYS
        )
        if llm_valid:
            final_scores = {
                key: _clamp_score(
                    _clamp_score(llm_scores[key]) * llm_weight
                    + baseline[key] * rule_weight
                )
                for key in DIMENSION_KEYS
            }
        else:
            final_scores = baseline

        dimensions = self.rules["dimensions"]
        score = round(
            sum(
                final_scores[key] / 5 * float(dimensions[key]["weight"])
                for key in DIMENSION_KEYS
            ),
            1,
        )
        recommendation = self.rules["recommendation"]
        recommended = (
            score >= float(recommendation["threshold"])
            and final_scores["normal_user_value"]
            >= int(recommendation["minimum_normal_user_value"])
            and final_scores["usage_threshold"]
            >= int(recommendation["minimum_usage_threshold"])
        )

        def copy_field(name: str) -> str:
            if isinstance(llm_item, dict):
                value = llm_item.get(name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return fallback_copy[name]

        return {
            "project_name": _project_name(project),
            "source_description": str(project.get("description") or ""),
            "source_language": str(project.get("language") or ""),
            "source_topics": (
                project.get("topics")
                if isinstance(project.get("topics"), list)
                else []
            ),
            "source_url": str(project.get("url") or ""),
            "human_value_score": score,
            "scores": final_scores,
            "target_user": copy_field("target_user"),
            "why_people_care": copy_field("why_people_care"),
            "content_angle": copy_field("content_angle"),
            "recommended_or_not": recommended,
            "_llm_scored": llm_valid,
        }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 GitHub 项目转换为面向小红书普通用户的内容候选"
    )
    parser.add_argument(
        "input", nargs="?", default="projects.json", help="输入 projects.json"
    )
    parser.add_argument(
        "--output", default="human_value_report.json", help="输出 JSON 文件"
    )
    parser.add_argument(
        "--rules",
        default=str(BASE_DIR / "human_value_rules.yaml"),
        help="评分规则 YAML",
    )
    parser.add_argument(
        "--skip-llm", action="store_true", help="仅运行规则评分，用于离线测试"
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()
    try:
        rules = load_rules(Path(args.rules))
        projects = load_projects(Path(args.input))
        report = HumanValueAgent(rules).evaluate(
            projects, use_llm=not args.skip_llm
        )
        output = Path(args.output)
        write_report(output, report)
        recommended = sum(
            1 for item in report["projects"] if item["recommended_or_not"]
        )
        LOGGER.info(
            "Human Value 报告已保存：%s；项目=%d，推荐=%d，模式=%s",
            output,
            len(report["projects"]),
            recommended,
            report["mode"],
        )
        return 0
    except (OSError, ValueError) as exc:
        LOGGER.error("Human Value Agent 输入或配置错误：%s", exc)
        return 2
    except Exception:
        LOGGER.exception("Human Value Agent 执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
