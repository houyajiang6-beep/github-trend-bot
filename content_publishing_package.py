from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from creator_strategy import CreatorStrategyLayer, apply_strategy
from daily_editor_agent import DailyEditorAgent, write_daily_selection


PACKAGE_FILES = (
    "xiaohongshu_post.md",
    "image_plan.md",
    "video_script.md",
    "metadata.json",
    "creator_strategy.json",
    "publish_checklist.md",
)
CREATOR_READY_FILES = (
    "publish.txt",
    "cover.txt",
    "image_generation_plan.md",
    "creator_review.md",
    "creator_strategy.json",
    "performance_tracking.json",
)


def _load_json_list(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"缺少 {label} 文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 不是有效 JSON：{path}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{label} 顶层必须是数组：{path}")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError(f"{label} 数组成员必须是对象：{path}")
    return data


def load_content_outputs(input_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Load and validate the three Content Generator output files."""
    return {
        "posts": _load_json_list(
            input_dir / "xiaohongshu_posts.json", "小红书内容"
        ),
        "videos": _load_json_list(
            input_dir / "video_scripts.json", "视频脚本"
        ),
        "metadata": _load_json_list(
            input_dir / "content_metadata.json", "内容元数据"
        ),
    }


def _index_by_project(
    items: list[dict[str, Any]], label: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        project = str(item.get("project_name", "")).strip()
        if not project:
            raise ValueError(f"{label} 存在缺少 project_name 的记录")
        if project in result:
            raise ValueError(f"{label} 存在重复项目：{project}")
        result[project] = item
    return result


def _metadata_by_project(
    items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        project = str(item.get("project_name", "")).strip()
        if not project:
            raise ValueError("内容元数据存在缺少 project_name 的记录")
        current = result.setdefault(project, {})
        if "human_value_score" in item:
            current["human_value_score"] = item["human_value_score"]
        if "generated_time" in item:
            current["source_generated_time"] = item["generated_time"]
    return result


def _project_slug(project: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", project).strip("-.")
    if slug:
        return slug[:100]
    return "project"


def _clean_heading_text(value: Any, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def _shorten(text: str, limit: int = 36) -> str:
    text = _clean_heading_text(text, "这个工具适合普通用户吗")
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _title_candidates(post: dict[str, Any]) -> list[str]:
    strategy_titles = post.get("title_candidates")
    if isinstance(strategy_titles, list):
        candidates = [
            _shorten(title, 40) for title in strategy_titles if str(title).strip()
        ]
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) >= 3:
            return candidates[:3]
    original = _shorten(post.get("title", ""), 40)
    pain_point = _shorten(post.get("pain_point", ""), 28)
    base = re.sub(r"^(21\s*岁学生[^：:]*[：:]?)", "", original).strip()
    raw = [
        original,
        f"{pain_point}，可以怎么解决？",
        f"21岁学生探索：{base or original}",
    ]
    candidates: list[str] = []
    for candidate in raw:
        candidate = _shorten(candidate, 40)
        if candidate not in candidates:
            candidates.append(candidate)
    while len(candidates) < 3:
        candidates.append(f"{original}｜候选{len(candidates) + 1}")
    return candidates[:3]


def _seven_pages(raw_pages: Any, post: dict[str, Any]) -> list[str]:
    pages = [
        _clean_heading_text(page, "待补充页面文案")
        for page in (raw_pages if isinstance(raw_pages, list) else [])
        if str(page).strip()
    ]
    fallbacks = [
        f"封面：{_clean_heading_text(post.get('cover_text'), post.get('title', '内容主题'))}",
        f"这篇内容写给：{_clean_heading_text(post.get('target_user'), '有相关需求的普通用户')}",
        f"真实痛点：{_clean_heading_text(post.get('pain_point'), '先确认自己的真实需求')}",
        "工具思路：说明它能做什么，不使用难懂的技术词。",
        "操作展示：用一个真实任务展示关键步骤，并保留必要截图。",
        "适用场景与限制：说明适合谁、在哪些情况下可能不好用。",
        "结尾：邀请读者先收藏，再根据自己的场景判断是否值得尝试。",
    ]
    for fallback in fallbacks:
        if len(pages) >= 7:
            break
        pages.append(fallback)
    if len(pages) > 7:
        pages = pages[:6] + ["；".join(pages[6:])]
    return pages


def _page_purpose(index: int) -> str:
    purposes = (
        "抓住注意力并说明主题",
        "让目标用户产生代入感",
        "讲清真实问题",
        "解释工具或方法",
        "展示关键操作与结果",
        "补充使用场景和限制",
        "总结价值并引导互动",
    )
    return purposes[index - 1]


def _image_advice(page: str, index: int) -> tuple[str, bool]:
    needs_screenshot = bool(
        re.search(r"截图|屏幕|操作|演示|上传|打开|结果|对比|前后", page)
    )
    if needs_screenshot:
        return "真实操作截图；遮挡隐私信息，并标注关键步骤", True
    suggestions = (
        "人物与问题场景的封面图，文字保持醒目",
        "目标用户日常场景图",
        "痛点场景或问题清单",
        "简洁流程图或工具界面示意图",
        "操作前后对比图",
        "适用/不适用场景对照图",
        "账号人设照片或简洁总结卡片",
    )
    return suggestions[index - 1], False


def _content_angle(post: dict[str, Any]) -> str:
    pain = _clean_heading_text(post.get("pain_point"), "普通用户的真实问题")
    return f"围绕“{pain}”，用7页说明使用场景、操作思路、实际价值和限制"


def render_xiaohongshu_post(post: dict[str, Any]) -> str:
    titles = _title_candidates(post)
    pages = _seven_pages(post.get("pages"), post)
    tags = post.get("tags") if isinstance(post.get("tags"), list) else []
    tags_text = " ".join(
        f"#{str(tag).strip().lstrip('#')}" for tag in tags if str(tag).strip()
    )
    lines = ["# 小红书发布稿", "", "## 标题候选", ""]
    lines.extend(f"{index}. {title}" for index, title in enumerate(titles, 1))
    lines.extend(
        [
            "",
            "## 推荐标题",
            "",
            titles[0],
            "",
            "## 封面文字",
            "",
            _clean_heading_text(post.get("cover_text"), titles[0]),
            "",
            "## 目标用户",
            "",
            _clean_heading_text(post.get("target_user"), "有相关需求的普通用户"),
            "",
            "## 痛点",
            "",
            _clean_heading_text(post.get("pain_point"), "待确认真实痛点"),
            "",
            "## 正文（7页）",
            "",
        ]
    )
    for index, page in enumerate(pages, 1):
        lines.extend([f"### 第{index}页", "", page, ""])
    lines.extend(["## 标签", "", tags_text or "#AI工具 #普通人用AI", ""])
    return "\n".join(lines)


def render_image_plan(post: dict[str, Any]) -> str:
    pages = _seven_pages(post.get("pages"), post)
    lines = ["# 小红书图文制作计划", ""]
    for index, page in enumerate(pages, 1):
        advice, needs_screenshot = _image_advice(page, index)
        lines.extend(
            [
                f"## 第{index}页",
                "",
                f"- 页面目的：{_page_purpose(index)}",
                f"- 文案：{page}",
                f"- 建议图片：{advice}",
                f"- 是否需要截图：{'是' if needs_screenshot else '否'}",
                "",
            ]
        )
    return "\n".join(lines)


def render_video_script(video: dict[str, Any]) -> str:
    segments = [
        ("Hook", "0-3秒", video.get("hook"), "人物出镜或痛点大字，第一秒出现核心问题"),
        ("问题", "3-12秒", video.get("problem"), "真实生活场景，展示问题发生的时刻"),
        ("解决思路", "12-22秒", video.get("solution"), "工具首页或流程示意，不展示复杂技术信息"),
        ("操作演示", "22-42秒", video.get("demo"), "录屏展示关键步骤，敏感信息打码"),
        ("价值与限制", "42-53秒", video.get("ending"), "结果对比，并在画面上标出适用条件和限制"),
        ("行动引导", "53-60秒", video.get("cta"), "人物出镜，配收藏或关注提示"),
    ]
    hook = _clean_heading_text(video.get("hook"), "先提出目标用户正在经历的问题。")
    lines = ["# 60秒视频脚本", "", "## Hook", "", hook, "", "## 分镜", ""]
    for index, (name, duration, voiceover, visual) in enumerate(segments, 1):
        lines.extend(
            [
                f"### 镜头{index}｜{name}（{duration}）",
                "",
                f"- 旁白：{_clean_heading_text(voiceover, '待补充真实内容')} ",
                f"- 画面建议：{visual}",
                "",
            ]
        )
    lines.extend(["## 旁白全文", ""])
    lines.extend(
        f"{index}. {_clean_heading_text(segment[2], '待补充真实内容')}"
        for index, segment in enumerate(segments, 1)
    )
    lines.extend(["", "## 画面建议", "", "优先使用本人出镜、真实操作录屏和真实结果；不要用无法验证的效果素材。", ""])
    return "\n".join(lines)


def render_publish_checklist(project: str) -> str:
    return "\n".join(
        [
            "# 发布前检查清单",
            "",
            f"项目：{project}",
            "",
            "> 所有项目确认后再发布；未确认项不能默认通过。",
            "",
            "## 内容真实性",
            "",
            "- [ ] 是否删除“神器、万能、零门槛、一定有效”等夸大表达？",
            "- [ ] 所有效果、时间、比例和对比结论是否有真实测试或可靠来源？",
            "- [ ] 截图是否来自真实操作，且没有把演示结果包装成普遍结果？",
            "- [ ] 是否明确说明工具限制、失败情况或适用条件？",
            "",
            "## 用户与表达",
            "",
            "- [ ] 普通用户不看技术文档也能理解标题和正文吗？",
            "- [ ] 是否围绕具体使用场景，而不是只介绍工具功能？",
            "- [ ] 标题、封面与正文表达的是同一件事吗？",
            "",
            "## 账号人设",
            "",
            "- [ ] 是否符合“21岁学生探索AI、替普通人筛选工具”的真实视角？",
            "- [ ] 是否避免虚构“我用过、我实测、我获得结果”等个人经历？",
            "- [ ] 是否保留个人判断、使用过程或失败经验，而非单纯搬运项目介绍？",
            "",
            "## 发布操作",
            "",
            "- [ ] 图片中的隐私、账号、文件名和密钥是否已打码？",
            "- [ ] 标签数量是否适中，且与内容和目标用户有关？",
            "- [ ] 项目名称、访问方式、收费情况和使用条件是否已再次核对？",
            "",
        ]
    )


def _story_body(post: dict[str, Any]) -> list[str]:
    personal_hook = _clean_heading_text(post.get("personal_hook"), "")
    story_arc = post.get("story_arc")
    experiment_plan = post.get("experiment_plan")
    reflection = _clean_heading_text(post.get("reflection"), "")
    has_story = (
        personal_hook
        and isinstance(story_arc, list)
        and story_arc
        and isinstance(experiment_plan, list)
        and experiment_plan
        and reflection
    )
    if not has_story:
        return _seven_pages(post.get("pages"), post)

    story_transitions = (
        ("用户真实场景：", "我想到的真实场景是："),
        ("使用前的问题：", "在用工具之前，最需要解决的是："),
        ("为什么我会测试：", "我决定测试它，是因为："),
    )
    paragraphs = [personal_hook]
    for index, value in enumerate(story_arc):
        text = _clean_heading_text(value, "")
        old_prefix, new_prefix = story_transitions[min(index, 2)]
        paragraphs.append(new_prefix + text.removeprefix(old_prefix).strip())
    experiment = [
        _clean_heading_text(value, "") for value in experiment_plan if str(value).strip()
    ]
    if experiment:
        paragraphs.append(
            "为了不只看介绍，我的实验过程会这样记录：\n"
            + "\n".join(f"{index}. {value}" for index, value in enumerate(experiment, 1))
        )
    paragraphs.append(
        "最后我的判断是："
        + reflection.removeprefix("我的个人判断：").strip()
    )
    return [paragraph for paragraph in paragraphs if paragraph]


def render_creator_publish(post: dict[str, Any]) -> str:
    title = _clean_heading_text(post.get("title"), "21岁学生探索AI工具")
    if title.endswith(("…", "...")):
        cover_parts = [
            part.strip()
            for part in str(post.get("cover_text") or "").splitlines()
            if part.strip()
        ]
        cover_topic = "，".join(cover_parts) or "这个AI工具普通人值得用吗？"
        title = f"{cover_topic}｜21岁学生探索"
    tags = post.get("tags") if isinstance(post.get("tags"), list) else []
    tags_text = " ".join(
        f"#{str(tag).strip().lstrip('#')}" for tag in tags if str(tag).strip()
    ) or "#AI工具 #21岁探索AI"
    return "\n".join(
        [
            "标题：",
            title,
            "",
            "正文：",
            "\n\n".join(_story_body(post)),
            "",
            "标签：",
            tags_text,
            "",
        ]
    )


def render_creator_cover(post: dict[str, Any]) -> str:
    raw_cover = str(post.get("cover_text") or "")
    cover_lines = [line.strip() for line in raw_cover.splitlines() if line.strip()]
    main_title = cover_lines[0] if cover_lines else _shorten(str(post.get("title") or ""), 18)
    subtitle = (
        " / ".join(cover_lines[1:])
        if len(cover_lines) > 1
        else f"21岁学生视角｜{_shorten(str(post.get('pain_point') or ''), 24)}"
    )
    return "\n".join(
        [
            "封面主标题：",
            main_title,
            "",
            "副标题：",
            subtitle,
            "",
            "视觉建议：",
            "使用真实生活场景或本人出镜作为主体，搭配一处真实工具界面；主标题高对比、少于两行，不使用夸张效果图。",
            "",
        ]
    )


def _image_type(index: int, needs_screenshot: bool) -> str:
    if needs_screenshot:
        return "真实操作截图"
    if index in (1, 2, 7):
        return "真人实拍/生活场景图"
    if index in (3, 6):
        return "信息卡片/对照图"
    return "流程示意图"


def render_image_generation_plan(post: dict[str, Any]) -> str:
    pages = _seven_pages(post.get("pages"), post)
    lines = ["# Creator Ready 图片生成计划", ""]
    for index, page in enumerate(pages, 1):
        visual, needs_screenshot = _image_advice(page, index)
        lines.extend(
            [
                f"## 第{index}页",
                "",
                f"- 页面目的：{_page_purpose(index)}",
                f"- 文字：{page}",
                f"- 画面描述：{visual}",
                f"- 图片类型：{_image_type(index, needs_screenshot)}",
                "",
            ]
        )
    return "\n".join(lines)


def render_creator_review(project: str) -> str:
    return "\n".join(
        [
            "# Creator Review｜发布前人工检查",
            "",
            f"项目：{project}",
            "",
            "> 这是一份待人工确认的直接发布稿。四组检查全部通过后再复制发布。",
            "",
            "## 真实性",
            "",
            "- [ ] 所有第一人称经历都真实发生，并能提供过程截图或原始记录。",
            "- [ ] 所有效果、时间、数字和对比结论都有事实依据。",
            "- [ ] 已说明失败情况、使用门槛和不适用场景。",
            "",
            "## 个人IP感",
            "",
            "- [ ] 能看出我为什么测试，而不是换一个账号也能原样发布。",
            "- [ ] 保留了我的实验过程、犹豫和个人判断。",
            "- [ ] 符合“21岁学生探索AI、替普通人筛选工具”的真实人设。",
            "",
            "## 收藏价值",
            "",
            "- [ ] 读者收藏后能复用实验步骤、判断标准或避坑信息。",
            "- [ ] 至少提供一个具体场景和一个可执行动作。",
            "- [ ] 封面承诺与正文实际提供的价值一致。",
            "",
            "## 是否像广告",
            "",
            "- [ ] 没有连续堆砌功能、品牌名和空泛赞美。",
            "- [ ] 同时呈现优点、限制和不推荐情况。",
            "- [ ] 没有强迫下载、购买或制造焦虑的表达。",
            "",
        ]
    )


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _now_in_timezone(timezone_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(timezone_name))
    except Exception as exc:
        # Some minimal Windows Python installs have no IANA timezone database.
        # Shanghai has used UTC+8 without daylight saving since 1991, which is
        # sufficient for publishing dates and timestamps in this project.
        if timezone_name == "Asia/Shanghai":
            return datetime.now(datetime_timezone(timedelta(hours=8)))
        raise ValueError(f"无效时区：{timezone_name}") from exc


def build_creator_ready_packages(
    input_dir: Path,
    output_root: Path,
    publish_date: date | None = None,
    timezone: str = "Asia/Shanghai",
    *,
    strategies: list[dict[str, Any]] | None = None,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build copy-ready files, optionally reusing pre-generation decisions."""
    outputs = load_content_outputs(input_dir)
    posts = _index_by_project(outputs["posts"], "小红书内容")
    metadata = _metadata_by_project(outputs["metadata"])
    missing_metadata = sorted(set(posts) - set(metadata))
    if missing_metadata:
        raise ValueError(f"以下项目缺少内容元数据：{missing_metadata}")

    if strategies is None:
        strategy_layer = CreatorStrategyLayer()
        strategies = strategy_layer.evaluate_all(posts, metadata)
    strategy_by_project = {item["project_name"]: item for item in strategies}
    missing_strategy = sorted(set(posts) - set(strategy_by_project))
    if missing_strategy:
        raise ValueError(f"以下项目缺少 Creator Strategy：{missing_strategy}")

    now = _now_in_timezone(timezone)
    target_date = publish_date or now.date()
    date_dir = output_root / target_date.isoformat()
    date_dir.parent.mkdir(parents=True, exist_ok=True)
    if date_dir.exists():
        raise ValueError(
            f"Creator Ready 日期目录已存在，为避免覆盖人工修改请先确认并移走：{date_dir}"
        )
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f".{date_dir.name}-", dir=date_dir.parent)
    )
    summaries: list[dict[str, Any]] = []
    used_slugs: set[str] = set()
    try:
        for project in sorted(posts):
            slug = _project_slug(project)
            if slug in used_slugs:
                suffix = 2
                while f"{slug}-{suffix}" in used_slugs:
                    suffix += 1
                slug = f"{slug}-{suffix}"
            used_slugs.add(slug)

            package_dir = temp_dir / slug
            package_dir.mkdir()
            strategy = strategy_by_project[project]
            post = apply_strategy(posts[project], strategy)
            _write_text(package_dir / "publish.txt", render_creator_publish(post))
            _write_text(package_dir / "cover.txt", render_creator_cover(post))
            _write_text(
                package_dir / "image_generation_plan.md",
                render_image_generation_plan(post),
            )
            _write_text(
                package_dir / "creator_review.md", render_creator_review(project)
            )
            (package_dir / "creator_strategy.json").write_text(
                json.dumps(strategy, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            performance = {
                "project": project,
                "published_time": None,
                "views": None,
                "likes": None,
                "favorites": None,
                "comments": None,
                "followers_gained": None,
            }
            (package_dir / "performance_tracking.json").write_text(
                json.dumps(performance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            summaries.append(
                {
                    "project": project,
                    "directory": slug,
                    "human_value_score": metadata[project].get("human_value_score"),
                    "creator_strategy_score": strategy["creator_strategy_score"],
                    "strategy_decision": strategy["decision"],
                    "generation_mode": metadata[project].get(
                        "generation_mode", "unknown"
                    ),
                    "files": list(CREATOR_READY_FILES),
                }
            )

        selection = selection or DailyEditorAgent().rank(
            strategies, publish_date=target_date
        )
        write_daily_selection(temp_dir, selection)
        manifest = {
            "publish_date": target_date.isoformat(),
            "generated_time": now.isoformat(),
            "package_count": len(summaries),
            "daily_pick": selection["selected_project"],
            "daily_title": selection["selected_title"],
            "packages": summaries,
        }
        (temp_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_dir.replace(date_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return {**manifest, "output_directory": str(date_dir)}


def build_publishing_packages(
    input_dir: Path,
    output_root: Path,
    publish_date: date | None = None,
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Build one ready-to-review publishing package per project."""
    outputs = load_content_outputs(input_dir)
    posts = _index_by_project(outputs["posts"], "小红书内容")
    videos = _index_by_project(outputs["videos"], "视频脚本")
    metadata = _metadata_by_project(outputs["metadata"])

    post_projects = set(posts)
    if post_projects != set(videos):
        missing_video = sorted(post_projects - set(videos))
        missing_post = sorted(set(videos) - post_projects)
        raise ValueError(
            f"小红书与视频项目不一致；缺视频={missing_video}，缺图文={missing_post}"
        )
    missing_metadata = sorted(post_projects - set(metadata))
    if missing_metadata:
        raise ValueError(f"以下项目缺少内容元数据：{missing_metadata}")

    strategy_layer = CreatorStrategyLayer()
    strategies = strategy_layer.evaluate_all(posts, metadata)
    strategy_by_project = {item["project_name"]: item for item in strategies}

    now = _now_in_timezone(timezone)
    target_date = publish_date or now.date()
    date_dir = output_root / target_date.isoformat()
    date_dir.parent.mkdir(parents=True, exist_ok=True)
    if date_dir.exists():
        raise ValueError(
            f"目标日期目录已存在，为避免覆盖人工修改请先确认并移走：{date_dir}"
        )
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f".{date_dir.name}-", dir=date_dir.parent)
    )
    package_summaries: list[dict[str, Any]] = []
    used_slugs: set[str] = set()

    try:
        for project in sorted(post_projects):
            slug = _project_slug(project)
            if slug in used_slugs:
                suffix = 2
                while f"{slug}-{suffix}" in used_slugs:
                    suffix += 1
                slug = f"{slug}-{suffix}"
            used_slugs.add(slug)

            package_dir = temp_dir / slug
            package_dir.mkdir()
            strategy = strategy_by_project[project]
            post = apply_strategy(posts[project], strategy)
            video = videos[project]
            angle = _content_angle(post)
            package_metadata = {
                "project": project,
                "human_value_score": metadata[project].get("human_value_score"),
                "content_angle": angle,
                "generated_time": now.isoformat(),
            }

            _write_text(package_dir / "xiaohongshu_post.md", render_xiaohongshu_post(post))
            _write_text(package_dir / "image_plan.md", render_image_plan(post))
            _write_text(package_dir / "video_script.md", render_video_script(video))
            _write_text(package_dir / "publish_checklist.md", render_publish_checklist(project))
            (package_dir / "metadata.json").write_text(
                json.dumps(package_metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (package_dir / "creator_strategy.json").write_text(
                json.dumps(strategy, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            package_summaries.append(
                {"project": project, "directory": slug, "files": list(PACKAGE_FILES)}
            )

        manifest = {
            "publish_date": target_date.isoformat(),
            "generated_time": now.isoformat(),
            "package_count": len(package_summaries),
            "packages": package_summaries,
        }
        (temp_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_dir.replace(date_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return {**manifest, "output_directory": str(date_dir)}


def publishing_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 Content Generator 输出整理为待审核的小红书发布包"
    )
    parser.add_argument(
        "--input-dir", type=Path, default=Path("outputs/content")
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("outputs/publishing")
    )
    parser.add_argument(
        "--creator-ready-root", type=Path, default=Path("outputs/creator_ready")
    )
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    args = parser.parse_args(argv)
    try:
        target_date = args.date or _now_in_timezone(args.timezone).date()
        legacy_date_dir = args.output_root / target_date.isoformat()
        creator_date_dir = args.creator_ready_root / target_date.isoformat()
        if legacy_date_dir.exists() or creator_date_dir.exists():
            existing = legacy_date_dir if legacy_date_dir.exists() else creator_date_dir
            raise ValueError(f"目标日期目录已存在，为避免覆盖人工修改请先确认并移走：{existing}")
        result = build_publishing_packages(
            input_dir=args.input_dir,
            output_root=args.output_root,
            publish_date=target_date,
            timezone=args.timezone,
        )
        creator_result = build_creator_ready_packages(
            input_dir=args.input_dir,
            output_root=args.creator_ready_root,
            publish_date=target_date,
            timezone=args.timezone,
        )
    except (OSError, ValueError) as exc:
        print(f"Content Publishing Package 生成失败：{exc}", file=sys.stderr)
        return 1
    print(
        f"已生成 {result['package_count']} 个策划包：{result['output_directory']}\n"
        f"已生成 {creator_result['package_count']} 个 Creator Ready 包："
        f"{creator_result['output_directory']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(publishing_main())
