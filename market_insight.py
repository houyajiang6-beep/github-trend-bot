from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from config import Settings
from crawler import Repository


LOGGER = logging.getLogger(__name__)

AI_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AI Agent / 智能体", ("agent", "agentic", "multi-agent", "autonomous")),
    ("MCP / AI 工具集成", ("mcp", "model context protocol")),
    ("AI 编程", ("copilot", "code generation", "coding assistant", "claude-code", "codex")),
    ("大模型 / 模型训练", ("llm", "large language model", "transformer", "foundation model", "training")),
    ("RAG / 知识库", ("rag", "retrieval", "vector database", "embedding", "knowledge base")),
    ("多模态 AI", ("multimodal", "vision", "image generation", "speech", "audio", "video generation")),
    ("机器学习基础设施", ("machine learning", "deep learning", "pytorch", "tensorflow", "inference", "mlops")),
    ("AI 自动化应用", ("artificial intelligence", " ai ", "automation", "workflow")),
)


def classify_ai_field(repo: Repository) -> str:
    text = " ".join(
        [repo.full_name, repo.description, " ".join(repo.topics), repo.readme[:1500]]
    ).lower()
    padded = f" {text} "
    for category, keywords in AI_CATEGORY_KEYWORDS:
        if any(keyword in padded for keyword in keywords):
            return category
    return "非 AI / 通用技术"


def load_previous_stars(
    report_dir: Path, current_date: str
) -> tuple[dict[str, int], int]:
    candidates: list[tuple[str, Path]] = []
    for path in report_dir.glob("????-??-??.json"):
        if path.stem < current_date:
            candidates.append((path.stem, path))
    if not candidates:
        return {}, 0
    previous_date, previous_path = max(candidates, key=lambda item: item[0])
    try:
        payload = json.loads(previous_path.read_text(encoding="utf-8"))
        stars = {
            str(item["full_name"]): int(item["stars"])
            for item in payload.get("repositories", [])
            if isinstance(item, dict) and "full_name" in item and "stars" in item
        }
        elapsed = (date.fromisoformat(current_date) - date.fromisoformat(previous_date)).days
        return stars, max(elapsed, 1)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOGGER.warning("无法读取历史日报 %s：%s", previous_path, exc)
        return {}, 0


def build_growth_metrics(
    repositories: list[Repository], previous_stars: dict[str, int] | None = None,
    elapsed_days: int = 0,
) -> dict[str, dict[str, Any]]:
    previous_stars = previous_stars or {}
    metrics: dict[str, dict[str, Any]] = {}
    for repo in repositories:
        daily_rate = (repo.stars_today / repo.stars * 100) if repo.stars else 0.0
        historical_velocity: float | None = None
        previous = previous_stars.get(repo.full_name)
        if previous is not None and elapsed_days > 0:
            historical_velocity = max(repo.stars - previous, 0) / elapsed_days

        if daily_rate >= 5 or repo.stars_today >= 1000:
            velocity_level = "爆发"
        elif daily_rate >= 1 or repo.stars_today >= 300:
            velocity_level = "高速"
        elif repo.stars_today >= 100:
            velocity_level = "较快"
        else:
            velocity_level = "平稳"

        if historical_velocity is None:
            growth_trend = f"当日{velocity_level}增长，尚无足够历史快照判断持续性"
        elif repo.stars_today > historical_velocity * 1.25:
            growth_trend = "增长加速，今日速度明显高于历史区间"
        elif repo.stars_today < historical_velocity * 0.75:
            growth_trend = "热度回落，今日速度低于历史区间"
        else:
            growth_trend = "增长稳定，今日速度接近历史区间"

        metrics[repo.full_name] = {
            "stars_today": repo.stars_today,
            "daily_growth_rate": round(daily_rate, 2),
            "velocity_level": velocity_level,
            "historical_stars_per_day": (
                round(historical_velocity, 1) if historical_velocity is not None else None
            ),
            "growth_trend": growth_trend,
            "ai_category": classify_ai_field(repo),
        }
    return metrics


def fallback_market_insight(
    repositories: list[Repository], growth_metrics: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    ai_categories = Counter(
        metric["ai_category"]
        for metric in growth_metrics.values()
        if metric["ai_category"] != "非 AI / 通用技术"
    )
    category_text = "、".join(name for name, _ in ai_categories.most_common(3))
    fastest = sorted(repositories, key=lambda repo: repo.stars_today, reverse=True)[:3]
    fastest_text = "、".join(repo.full_name for repo in fastest) or "暂无项目"
    return {
        "technical_trends": [
            f"今日高热项目集中在{category_text or '通用开发工具'}方向。",
            f"Star 增长最快的项目为 {fastest_text}，反映开发者的即时关注重心。",
        ],
        "business_opportunities": [
            "围绕热门开源项目提供托管、企业集成、安全治理和培训服务。",
            "将高热 AI 工具封装为面向具体岗位的可交付工作流。",
        ],
        "affected_companies_industries": [
            "云计算、开发者工具、企业软件与 IT 服务商可能最先受到影响。",
            "知识密集型行业可关注 AI 自动化带来的流程和采购变化。",
        ],
        "long_term_watch": [
            "连续观察 7 至 30 天 Star 增速，区分短期上榜与持续采用。",
            f"重点跟踪 {category_text or '开发者基础设施'} 的生态、商业化与安全治理。",
        ],
    }


def generate_market_insight(
    repositories: list[Repository], analysis: dict[str, Any],
    growth_metrics: dict[str, dict[str, Any]], cfg: Settings,
) -> dict[str, list[str]]:
    """Generate business-oriented insight; callers should fall back on failure."""
    cfg.validate_ai()
    from openai import OpenAI

    compact_repositories = [
        {
            "full_name": repo.full_name,
            "description": repo.description,
            "language": repo.language,
            "topics": repo.topics,
            **growth_metrics[repo.full_name],
        }
        for repo in repositories
    ]
    prompt = f"""
你是开源技术战略与商业分析师。仅依据给定 GitHub 日报数据生成中文市场洞察。
不得编造融资、客户、产品发布或公司行动；涉及未来判断必须使用“可能、值得关注”等审慎措辞。
每个字段输出 2 至 4 条具体结论，并说明结论对应的项目或分类证据。

项目数据：{json.dumps(compact_repositories, ensure_ascii=False)}
日报分析：{json.dumps(analysis, ensure_ascii=False)}

只输出 JSON，对象字段必须为：technical_trends、business_opportunities、affected_companies_industries、long_term_watch；字段值均为字符串数组。
""".strip()
    client = OpenAI(
        api_key=cfg.deepseek_api_key, base_url=cfg.deepseek_base_url,
        timeout=cfg.ai_request_timeout, max_retries=0,
    )
    response = client.chat.completions.create(
        model=cfg.deepseek_model,
        messages=[
            {"role": "system", "content": "只输出有效 JSON，不要输出 Markdown。"},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=2500,
        extra_body={"thinking": {"type": "enabled" if cfg.deepseek_thinking else "disabled"}},
    )
    content = response.choices[0].message.content
    result = json.loads(content)
    required = (
        "technical_trends", "business_opportunities",
        "affected_companies_industries", "long_term_watch",
    )
    if not isinstance(result, dict) or any(not isinstance(result.get(key), list) for key in required):
        raise ValueError("市场洞察 JSON 结构无效")
    return {key: [str(item) for item in result[key] if str(item).strip()][:4] for key in required}
