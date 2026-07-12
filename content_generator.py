from __future__ import annotations

import json
from typing import Any

from config import Settings
from crawler import Repository


def fallback_content(
    report_date: str, repositories: list[Repository], analysis: dict[str, Any],
    market_insight: dict[str, list[str]],
) -> dict[str, Any]:
    top = sorted(repositories, key=lambda repo: repo.stars_today, reverse=True)[:3]
    names = "、".join(repo.full_name for repo in top) or "GitHub 热门项目"
    trend = (market_insight.get("technical_trends") or [analysis.get("ai_observation", "AI 工具持续演进")])[0]
    return {
        "date": report_date,
        "douyin_titles": [
            f"GitHub 今日爆火：{names}",
            "开发者都在追什么？30 秒看懂今日 AI 趋势",
            "别只看 Star：GitHub 热榜释放的技术信号",
        ],
        "voiceover_30s": f"今天 GitHub 热榜最值得关注的是 {names}。{trend} 如果你做开发或产品，先看项目解决了什么真实问题，再关注接下来一周的 Star 增速和生态扩展，别被单日热度带偏。",
        "xiaohongshu_note": {
            "title": "GitHub AI 趋势日报｜今天值得收藏的项目",
            "body": f"今日重点：{names}\n\n趋势判断：{trend}\n\n学习建议：先跑通 README 示例，再做一个小型真实场景验证，连续观察一周热度。",
            "hashtags": ["GitHub", "AI工具", "程序员", "开源项目", "技术趋势"],
        },
        "video_topics": [
            {"title": f"实测 {top[0].full_name if top else '今日热门项目'}", "angle": "用真实任务检验项目价值"},
            {"title": "GitHub Star 增速怎么看", "angle": "区分短期爆火和长期成长"},
            {"title": "本周 AI 开源赛道地图", "angle": "按 Agent、MCP、RAG 与基础设施分类"},
        ],
    }


def generate_content(
    report_date: str, repositories: list[Repository], analysis: dict[str, Any],
    market_insight: dict[str, list[str]], cfg: Settings,
) -> dict[str, Any]:
    """Create platform-ready content from the report; callers should fall back on failure."""
    cfg.validate_ai()
    from openai import OpenAI

    source = {
        "date": report_date,
        "repositories": [
            {
                "full_name": repo.full_name, "stars": repo.stars,
                "stars_today": repo.stars_today, "description": repo.description,
            }
            for repo in repositories[:15]
        ],
        "analysis": analysis,
        "market_insight": market_insight,
    }
    prompt = f"""
你是严谨的中文科技内容编辑。根据 GitHub 日报生成可直接二次编辑的社媒内容。
不得编造项目能力、新闻、公司行动和数字；避免夸大承诺。30 秒口播控制在 130 至 180 个汉字。
抖音标题给 3 个；小红书笔记包含 title、body、hashtags；视频选题给 3 至 5 个，每项包含 title 和 angle。
只输出 JSON，字段：date、douyin_titles、voiceover_30s、xiaohongshu_note、video_topics。

日报：{json.dumps(source, ensure_ascii=False)}
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
        max_tokens=3000,
        extra_body={"thinking": {"type": "enabled" if cfg.deepseek_thinking else "disabled"}},
    )
    result = json.loads(response.choices[0].message.content)
    required = {"douyin_titles", "voiceover_30s", "xiaohongshu_note", "video_topics"}
    if not isinstance(result, dict) or not required.issubset(result):
        raise ValueError("内容 JSON 结构无效")
    result["date"] = report_date
    return result
