from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from config import Settings, settings
from crawler import Repository


LOGGER = logging.getLogger("content-generator")

BASE_POST_FIELDS = (
    "title",
    "cover_text",
    "target_user",
    "pain_point",
    "pages",
    "tags",
)
STORY_FIELDS = (
    "personal_hook",
    "story_arc",
    "experiment_plan",
    "reflection",
)
POST_FIELDS = BASE_POST_FIELDS + STORY_FIELDS
VIDEO_FIELDS = ("hook", "problem", "solution", "demo", "ending", "cta")

ContentLLMEvaluator = Callable[
    [list[dict[str, Any]], Settings], list[dict[str, Any]]
]


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


def load_human_value_report(path: Path) -> dict[str, Any]:
    """Load the Human Value output without depending on its implementation."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 Human Value 报告 {path}: {exc}") from exc
    projects = payload.get("projects") if isinstance(payload, dict) else None
    if not isinstance(projects, list) or not projects:
        raise ValueError("Human Value 报告必须包含非空 projects 数组")
    required = {
        "project_name",
        "human_value_score",
        "target_user",
        "why_people_care",
        "content_angle",
        "recommended_or_not",
    }
    for index, project in enumerate(projects, start=1):
        if not isinstance(project, dict) or not required.issubset(project):
            raise ValueError(f"第 {index} 个 Human Value 项目字段不完整")
    return payload


def _clean_text(value: Any) -> str:
    """Remove developer jargon and inflated claims from user-facing copy."""
    text = str(value or "").strip()
    replacements = (
        (r"\bGitHub\b", "开源社区"),
        (r"\bCLI\b", "需要输入命令的工具"),
        (r"\bSDK\b", "开发组件"),
        (r"\bAPI\b", "连接能力"),
        (r"\bRAG\b", "资料问答"),
        (r"\bMCP\b", "AI 工具连接方式"),
        (r"\bframework\b", "工具"),
        (r"\brepository\b", "项目"),
        (r"\bDocker\b", "复杂安装环境"),
        (r"\bKubernetes\b", "企业级运行环境"),
        (r"\d+\s*倍", "明显"),
        (r"\d+\s*秒", "较短时间"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    for inflated, replacement in (
        ("神器", "工具"),
        ("零门槛", "上手相对简单"),
        ("秒杀", "更省事"),
        ("100%", "尽量"),
        ("彻底", "尽量"),
        ("万能", "多用途"),
        ("一定能", "可能会"),
    ):
        text = text.replace(inflated, replacement)
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, length: int) -> str:
    cleaned = _clean_text(text)
    return cleaned if len(cleaned) <= length else cleaned[: length - 1].rstrip() + "…"


def _tags_for(project: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(project.get(key) or "")
        for key in ("target_user", "why_people_care", "content_angle")
    )
    tags = ["AI工具", "21岁探索AI"]
    if any(word in text for word in ("简历", "求职", "作品集", "面试")):
        tags.extend(["求职成长", "学生必看"])
    elif any(word in text for word in ("图片", "封面", "创作", "照片")):
        tags.extend(["内容创作", "效率工具"])
    elif any(word in text for word in ("资料", "PDF", "上课", "笔记", "会议")):
        tags.extend(["学习效率", "职场效率"])
    else:
        tags.extend(["效率提升", "工具观察"])
    return tags[:5]


def build_story_layer(project: dict[str, Any]) -> dict[str, Any]:
    """Create a first-person test story without inventing completed experience."""
    target = _clean_text(project["target_user"])
    pain = _clean_text(project["why_people_care"])
    angle = _clean_text(project["content_angle"])
    return {
        "personal_hook": (
            f"我今年 21 岁，也在学习怎么把 AI 用进真实生活。看到“{pain}”这个问题，"
            "我想先替和我一样的普通用户确认：它到底值不值得花时间尝试。"
        ),
        "story_arc": [
            f"用户真实场景：{target}遇到相关任务时，常常需要自己反复处理。",
            f"使用前的问题：{pain}",
            f"为什么我会测试：我想用“{angle}”这个具体场景，判断它是在解决问题，还是只看起来新鲜。",
        ],
        "experiment_plan": [
            "准备一个不含隐私、结果可以人工核对的真实任务。",
            "记录不用工具时需要完成的步骤，作为使用前基线。",
            f"按照“{angle}”完成一次操作，保留关键过程、结果和失败截图。",
            "对照原始材料检查结果，并记录上手门槛、出错情况和适用限制。",
        ],
        "reflection": (
            "我的个人判断：只有真实任务能跑通、结果可以核对，而且普通用户能独立完成，"
            "我才会推荐；实验完成前不把计划写成已经发生的体验。"
        ),
    }


def _fallback_candidate(project: dict[str, Any]) -> dict[str, Any]:
    target = _clean_text(project["target_user"])
    pain = _clean_text(project["why_people_care"])
    angle = _clean_text(project["content_angle"])
    title_angle = _truncate(angle, 18)
    story = build_story_layer(project)
    post = {
        "project_name": str(project["project_name"]),
        "title": f"21岁学生探索｜{title_angle}",
        "cover_text": "这个 AI 工具\n普通人值得用吗？",
        "target_user": target,
        "pain_point": pain,
        "pages": [
            story["personal_hook"],
            story["story_arc"][0],
            story["story_arc"][1],
            story["story_arc"][2],
            "我的实验过程会这样记录：" + "；".join(story["experiment_plan"][:2]),
            "结果页不只放成功画面，也会保留失败截图、使用门槛和不适用场景。",
            story["reflection"],
        ],
        "tags": _tags_for(project),
        **story,
    }
    video = {
        "project_name": str(project["project_name"]),
        "hook": f"我今年 21 岁，最近在替普通人筛 AI 工具。这个方向值得注意：{title_angle}",
        "problem": pain,
        "solution": f"它提供的思路是：{angle}",
        "demo": "按实验计划展示真实任务、使用前基线、关键操作和核对过程；如果没有跑通，就说明卡在哪里。",
        "ending": story["reflection"],
        "cta": "先收藏这份判断，关注我继续看一个 21 岁学生替你筛选 AI 工具。",
    }
    return {"project_name": str(project["project_name"]), "post": post, "video": video}


def evaluate_content_with_deepseek(
    projects: list[dict[str, Any]], cfg: Settings
) -> list[dict[str, Any]]:
    cfg.validate_ai()
    from openai import OpenAI

    source = [
        {
            "project_name": item["project_name"],
            "human_value_score": item["human_value_score"],
            "target_user": item["target_user"],
            "why_people_care": item["why_people_care"],
            "content_angle": item["content_angle"],
        }
        for item in projects
    ]
    prompt = f"""
你是“Jack探索AI”的内容编辑。创作者是一个 21 岁学生，替普通用户筛选 AI 工具。
请把下面的 Human Value 候选转换为小红书图文和 60 秒短视频脚本。

硬性要求：
1. 面向不会编程的普通用户，先讲问题、场景和结果。
2. 不使用 GitHub、CLI、SDK、API、RAG、MCP、框架、部署等程序员术语。
3. 每条内容都自然包含“21 岁学生探索 AI”的第一人称视角。
4. 不使用“神器、万能、零门槛、100%、一定、彻底”等夸张承诺。
5. 输入没有提供实测证据，禁止写“实测、我用过、我测试过、我已经、效果提升”等完成态经历。
6. 不编造速度、准确率、耗时、用户反馈或项目能力；demo 只能写拍摄时应该展示什么。
7. 小红书 pages 为 5 到 8 个字符串，按封面后逐页阅读顺序组织。
8. tags 为 3 到 5 个不带 # 的短标签。
9. 增加 Story Layer：必须有用户真实场景、使用前问题、为什么我要测试、实验计划和个人判断。
10. “我的实验过程”只能写准备如何测试和记录，不能把尚未发生的实验写成完成态。

只输出 JSON：
{{
  "projects": [
    {{
      "project_name": "必须逐字来自输入",
      "xiaohongshu": {{
        "title": "",
        "cover_text": "",
        "target_user": "",
        "pain_point": "",
        "pages": [""],
        "tags": [""],
        "personal_hook": "第一人称说明我为什么关注这个真实问题，不虚构使用经历",
        "story_arc": ["用户真实场景", "使用前的问题", "为什么我会测试"],
        "experiment_plan": ["准备什么", "怎么操作", "记录什么", "如何核对"],
        "reflection": "条件式的个人判断，并明确当前是否已有真实证据"
      }},
      "video": {{
        "hook": "",
        "problem": "",
        "solution": "",
        "demo": "",
        "ending": "",
        "cta": ""
      }}
    }}
  ]
}}

输入：{json.dumps(source, ensure_ascii=False)}
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
            {"role": "system", "content": "只输出有效 JSON，不要输出 Markdown。"},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=8000,
        extra_body={
            "thinking": {"type": "enabled" if cfg.deepseek_thinking else "disabled"}
        },
    )
    payload = json.loads(response.choices[0].message.content)
    items = payload.get("projects") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("Content Generator LLM 输出缺少 projects 数组")
    return [item for item in items if isinstance(item, dict)]


def _normalize_llm_candidate(
    project: dict[str, Any], item: dict[str, Any] | None
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    post_source = item.get("xiaohongshu")
    video_source = item.get("video")
    if not isinstance(post_source, dict) or not isinstance(video_source, dict):
        return None
    if not set(BASE_POST_FIELDS).issubset(post_source) or not set(VIDEO_FIELDS).issubset(
        video_source
    ):
        return None
    pages = post_source.get("pages")
    tags = post_source.get("tags")
    if not isinstance(pages, list) or not 5 <= len(pages) <= 8:
        return None
    if not isinstance(tags, list) or not 3 <= len(tags) <= 5:
        return None

    default_story = build_story_layer(project)
    story_arc = post_source.get("story_arc")
    experiment_plan = post_source.get("experiment_plan")
    normalized_story = {
        "personal_hook": _clean_text(
            post_source.get("personal_hook") or default_story["personal_hook"]
        ),
        "story_arc": (
            [_clean_text(value) for value in story_arc]
            if isinstance(story_arc, list) and len(story_arc) >= 3
            else default_story["story_arc"]
        ),
        "experiment_plan": (
            [_clean_text(value) for value in experiment_plan]
            if isinstance(experiment_plan, list) and 3 <= len(experiment_plan) <= 6
            else default_story["experiment_plan"]
        ),
        "reflection": _clean_text(
            post_source.get("reflection") or default_story["reflection"]
        ),
    }
    post = {
        "project_name": str(project["project_name"]),
        **{
            key: _clean_text(post_source[key])
            for key in BASE_POST_FIELDS
            if key not in {"pages", "tags"}
        },
        "pages": [_clean_text(page) for page in pages],
        "tags": [_clean_text(tag).lstrip("#") for tag in tags],
        **normalized_story,
    }
    video = {
        "project_name": str(project["project_name"]),
        **{key: _clean_text(video_source[key]) for key in VIDEO_FIELDS},
    }
    combined_post = " ".join(
        [post["title"], post["cover_text"], *post["pages"]]
    )
    if "21岁" not in combined_post and "21 岁" not in combined_post:
        post["pages"][0] = "我今年 21 岁，正在探索普通人真正用得上的 AI。" + post["pages"][0]
    if "21岁" not in video["hook"] and "21 岁" not in video["hook"]:
        video["hook"] = "我今年 21 岁，正在替普通人筛 AI 工具。" + video["hook"]
    user_facing_text = " ".join(
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
            *(video[key] for key in VIDEO_FIELDS),
        ]
    )
    unverified_patterns = (
        r"实测",
        r"我(?:用|把|放|上传|测试|测了|已经|平时|只测|投了|有一份)",
        r"\d+(?:\.\d+)?\s*(?:%|倍|秒|分钟|小时|天)",
        r"面试机会变多",
        r"反馈都不错",
        r"省下(?:不少|很多)",
    )
    if any(re.search(pattern, user_facing_text) for pattern in unverified_patterns):
        return None
    return {"project_name": str(project["project_name"]), "post": post, "video": video}


class ContentGeneratorMVP:
    def __init__(
        self,
        cfg: Settings = settings,
        llm_evaluator: ContentLLMEvaluator = evaluate_content_with_deepseek,
    ) -> None:
        self.cfg = cfg
        self.llm_evaluator = llm_evaluator

    def generate(
        self, report: dict[str, Any], *, use_llm: bool = True
    ) -> dict[str, Any]:
        candidates = [
            item
            for item in report["projects"]
            if item.get("recommended_or_not") is True
        ]
        if not candidates:
            raise ValueError("Human Value 报告中没有 recommended 项目")

        llm_by_name: dict[str, dict[str, Any]] = {}
        llm_failed = False
        if use_llm:
            try:
                # The unified pipeline passes at most Top 3. Generate them in
                # one request instead of spending one LLM call per two items.
                for item in self.llm_evaluator(candidates, self.cfg):
                    name = str(item.get("project_name") or "").strip()
                    if name:
                        llm_by_name[name] = item
            except Exception as exc:
                llm_failed = True
                LOGGER.warning(
                    "Content Generator LLM 调用失败，全部使用安全模板降级：error=%s",
                    exc.__class__.__name__,
                )

        generated: list[dict[str, Any]] = []
        llm_count = 0
        rejected_llm_count = 0
        for project in candidates:
            llm_item = llm_by_name.get(str(project["project_name"]))
            normalized = _normalize_llm_candidate(
                project, llm_item
            )
            if normalized is None:
                if llm_item is not None:
                    rejected_llm_count += 1
                generated.append(_fallback_candidate(project))
            else:
                generated.append(normalized)
                llm_count += 1

        generated_time = datetime.now(
            ZoneInfo(self.cfg.report_timezone)
        ).isoformat()
        posts = [item["post"] for item in generated]
        videos = [item["video"] for item in generated]
        metadata = [
            {
                "project_name": str(project["project_name"]),
                "human_value_score": float(project["human_value_score"]),
                "content_types": ["xiaohongshu_post", "video_script"],
                "generated_time": generated_time,
            }
            for project in candidates
        ]
        mode = (
            "templates_only"
            if not use_llm
            else "llm_and_templates"
            if llm_count == len(candidates)
            else "partial_fallback"
            if llm_count
            else "templates_fallback"
        )
        return {
            "mode": mode,
            "llm_failed": llm_failed,
            "selected_projects": len(candidates),
            "llm_generated_projects": llm_count,
            "rejected_llm_projects": rejected_llm_count,
            "xiaohongshu_posts": posts,
            "video_scripts": videos,
            "content_metadata": metadata,
        }


def _atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def write_content_package(output_dir: Path, package: dict[str, Any]) -> dict[str, Path]:
    paths = {
        "xiaohongshu_posts": output_dir / "xiaohongshu_posts.json",
        "xiaohongshu_post": output_dir / "xiaohongshu_post.json",
        "video_scripts": output_dir / "video_scripts.json",
        "content_metadata": output_dir / "content_metadata.json",
    }
    payload_keys = {
        "xiaohongshu_posts": "xiaohongshu_posts",
        "xiaohongshu_post": "xiaohongshu_posts",
        "video_scripts": "video_scripts",
        "content_metadata": "content_metadata",
    }
    for key, path in paths.items():
        _atomic_json_write(path, package[payload_keys[key]])
    return paths


def parse_content_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 Human Value 报告转换为小红书图文和短视频脚本"
    )
    parser.add_argument(
        "input", nargs="?", default="human_value_report.json", help="Human Value JSON"
    )
    parser.add_argument(
        "--output-dir", default="outputs/content", help="内容输出目录"
    )
    parser.add_argument(
        "--skip-llm", action="store_true", help="使用安全模板离线生成"
    )
    return parser.parse_args()


def content_main() -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_content_args()
    try:
        report = load_human_value_report(Path(args.input))
        package = ContentGeneratorMVP().generate(
            report, use_llm=not args.skip_llm
        )
        paths = write_content_package(Path(args.output_dir), package)
        LOGGER.info(
            "Content Generator 完成：projects=%d mode=%s output=%s",
            package["selected_projects"],
            package["mode"],
            Path(args.output_dir),
        )
        for name, path in paths.items():
            LOGGER.info("已保存 %s：%s", name, path)
        return 0
    except (OSError, ValueError) as exc:
        LOGGER.error("Content Generator 输入或配置错误：%s", exc)
        return 2
    except Exception:
        LOGGER.exception("Content Generator 执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(content_main())
