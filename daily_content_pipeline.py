from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from config import BASE_DIR, Settings, settings
from content_calibration import (
    create_blind_prediction,
    load_or_create_rubric,
    record_t3_performance,
    register_publication,
)
from content_generator import ContentGeneratorMVP, write_content_package
from content_publishing_package import build_creator_ready_packages
from creator_strategy import CreatorStrategyLayer, write_strategy_report
from daily_editor_agent import DailyEditorAgent, write_daily_selection
from human_value_agent import HumanValueAgent, load_projects, load_rules, write_report


LOGGER = logging.getLogger("daily-content-pipeline")


def _now(timezone_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(timezone_name))
    except Exception:
        if timezone_name == "Asia/Shanghai":
            return datetime.now(timezone(timedelta(hours=8)))
        raise


def _project_dicts(repositories: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        repository.to_dict() if hasattr(repository, "to_dict") else dict(repository)
        for repository in repositories
    ]


def _recent_categories(prediction_dir: Path, limit: int = 3) -> list[str]:
    records: list[tuple[str, str]] = []
    for path in prediction_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        category = str(payload.get("category") or "").strip()
        created_at = str(payload.get("created_at") or "")
        if category:
            records.append((created_at, category))
    return [category for _, category in sorted(records)[-limit:]]


def _selected_report(
    human_report: dict[str, Any], selection: dict[str, Any], top_n: int
) -> dict[str, Any]:
    names = [
        str(item["project_name"])
        for item in selection.get("top_candidates", [])[:top_n]
    ]
    by_name = {
        str(item["project_name"]): item for item in human_report["projects"]
    }
    projects = []
    for name in names:
        if name in by_name:
            projects.append({**by_name[name], "recommended_or_not": True})
    if not projects:
        raise ValueError("Daily Editor 没有选出可生成的内容")
    return {**human_report, "projects": projects}


def run_daily_content_pipeline(
    repositories: Iterable[Any],
    *,
    publish_date: date | str,
    cfg: Settings = settings,
    use_llm: bool = True,
    top_n: int = 3,
    output_root: Path | None = None,
) -> dict[str, Any]:
    """Run the only supported daily content path: score -> rank -> generate."""
    if top_n not in {1, 3}:
        raise ValueError("top_n 只允许 1 或 3")
    target_date = (
        publish_date.isoformat() if isinstance(publish_date, date) else str(publish_date)
    )
    root = output_root or (BASE_DIR / "outputs")
    run_dir = root / "pipeline" / target_date
    creator_root = root / "creator_ready"
    calibration_dir = root / "calibration"
    prediction_dir = calibration_dir / "predictions"
    rubric_path = calibration_dir / "rubric.json"
    if run_dir.exists() or (creator_root / target_date).exists():
        raise ValueError(
            f"{target_date} 内容已生成；为保护人工修改，本次不覆盖"
        )
    run_dir.mkdir(parents=True)
    now = _now(cfg.report_timezone)
    status: dict[str, Any] = {
        "schema_version": "1.0",
        "publish_date": target_date,
        "started_at": now.isoformat(),
        "status": "running",
        "steps": {},
    }
    status_path = run_dir / "pipeline_status.json"

    try:
        rules = load_rules(BASE_DIR / "human_value_rules.yaml")
        human_report = HumanValueAgent(rules, cfg).evaluate(
            _project_dicts(repositories), use_llm=use_llm
        )
        human_path = run_dir / "human_value_report.json"
        write_report(human_path, human_report)
        candidates = [
            item
            for item in human_report["projects"]
            if item.get("recommended_or_not") is True
        ]
        if not candidates:
            raise ValueError("Human Value 没有筛出适合普通用户的项目")
        status["steps"]["human_value"] = {
            "status": "success",
            "projects": len(human_report["projects"]),
            "candidates": len(candidates),
            "mode": human_report["mode"],
            "output": str(human_path),
        }

        strategies = CreatorStrategyLayer().evaluate_projects(candidates)
        strategy_path = run_dir / "creator_strategy.json"
        write_strategy_report(strategy_path, strategies)
        status["steps"]["creator_strategy"] = {
            "status": "success",
            "projects": len(strategies),
            "output": str(strategy_path),
        }

        rubric = load_or_create_rubric(rubric_path)
        selection = DailyEditorAgent().rank(
            strategies,
            publish_date=target_date,
            previous_categories=_recent_categories(prediction_dir),
            rubric=rubric,
        )
        selection["top_candidates"] = selection["top_candidates"][:top_n]
        selection_paths = write_daily_selection(run_dir, selection)
        status["steps"]["daily_editor"] = {
            "status": "success",
            "selected": selection["selected_project"],
            "top_n": len(selection["top_candidates"]),
            "output": str(selection_paths["json"]),
        }

        selected_report = _selected_report(human_report, selection, top_n)
        content_package = ContentGeneratorMVP(cfg).generate(
            selected_report, use_llm=use_llm
        )
        content_dir = run_dir / "content"
        content_paths = write_content_package(content_dir, content_package)
        fallback_projects = [
            item["project_name"]
            for item in content_package.get("content_metadata") or []
            if item.get("generation_mode") == "rules_fallback"
        ]
        public_content_mode = (
            "full_llm"
            if content_package["mode"] == "llm_and_templates"
            else "partial_fallback"
            if content_package["mode"] == "partial_fallback"
            else "rules_fallback"
        )
        status["steps"]["content_generator"] = {
            "status": "success",
            "projects": content_package["selected_projects"],
            "mode": content_package["mode"],
            "delivery_mode": public_content_mode,
            "fallback_projects": fallback_projects,
            "llm_calls": 1 if use_llm else 0,
            "output": str(content_dir),
        }

        selected_names = {
            item["project_name"] for item in selection["top_candidates"]
        }
        selected_strategies = [
            item for item in strategies if item["project_name"] in selected_names
        ]
        creator_result = build_creator_ready_packages(
            input_dir=content_dir,
            output_root=creator_root,
            publish_date=date.fromisoformat(target_date),
            timezone=cfg.report_timezone,
            strategies=selected_strategies,
            selection=selection,
        )
        status["steps"]["publishing_package"] = {
            "status": "success",
            "projects": creator_result["package_count"],
            "output": creator_result["output_directory"],
        }

        manifest = json.loads(
            (Path(creator_result["output_directory"]) / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        selected_project = selection["selected_project"]
        selected_package = next(
            item for item in manifest["packages"] if item["project"] == selected_project
        )
        publish_file = (
            Path(creator_result["output_directory"])
            / selected_package["directory"]
            / "publish.txt"
        )
        prediction_path = create_blind_prediction(
            selected=selection["top_candidates"][0],
            publish_file=publish_file,
            prediction_dir=prediction_dir,
            publish_date=target_date,
            rubric=rubric,
            created_at=now,
        )
        creator_dir = Path(creator_result["output_directory"])
        selected_dir = creator_dir / selected_package["directory"]
        # Stable Top 1 entrypoints make the daily artifact directly publishable
        # while preserving every candidate's existing package directory.
        for filename in ("publish.txt", "cover.txt"):
            shutil.copy2(selected_dir / filename, creator_dir / filename)
        shutil.copy2(prediction_path, creator_dir / "prediction.json")
        status["steps"]["blind_prediction"] = {
            "status": "success",
            "output": str(creator_dir / "prediction.json"),
        }
        status["status"] = (
            "degraded"
            if human_report["mode"] not in {"llm_and_rules", "rules_only"}
            or content_package["mode"] in {"partial_fallback", "templates_fallback"}
            else "success"
        )
        degraded_reasons: list[str] = []
        if content_package["mode"] == "partial_fallback":
            degraded_reasons.append(
                "Content Generator 对部分候选使用 rules fallback："
                + "、".join(fallback_projects)
            )
        elif content_package["mode"] == "templates_fallback":
            degraded_reasons.append(
                (
                    "Content Generator LLM 调用失败，全部候选使用 rules fallback："
                    if content_package.get("llm_failed")
                    else "Content Generator LLM 内容未通过校验，全部候选使用 rules fallback："
                )
                + "、".join(fallback_projects)
            )
        if human_report["mode"] not in {"llm_and_rules", "rules_only"}:
            degraded_reasons.append(
                f"Human Value 使用降级模式：{human_report['mode']}"
            )
        status["degraded_reasons"] = degraded_reasons
        status["finished_at"] = _now(cfg.report_timezone).isoformat()
        status_path.write_text(
            json.dumps(status, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "status": status["status"],
            "publish_date": target_date,
            "selected_project": selection["selected_project"],
            "selected_title": selection["selected_title"],
            "top_n": len(selection["top_candidates"]),
            "run_directory": str(run_dir),
            "creator_ready_directory": creator_result["output_directory"],
            "prediction": str(creator_dir / "prediction.json"),
            "content_package": content_package,
            "selection": selection,
            "content_generation_mode": public_content_mode,
            "fallback_projects": fallback_projects,
            "degraded_reasons": degraded_reasons,
        }
    except Exception as exc:
        status["status"] = "failed"
        status["finished_at"] = _now(cfg.report_timezone).isoformat()
        status["error"] = {"type": exc.__class__.__name__, "message": str(exc)}
        status_path.write_text(
            json.dumps(status, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise


def social_content_from_pipeline(result: dict[str, Any], report_date: str) -> dict[str, Any]:
    """Reuse Top 1 copy in the email instead of making a legacy content LLM call."""
    posts = result["content_package"]["xiaohongshu_posts"]
    videos = result["content_package"]["video_scripts"]
    post = posts[0]
    video = videos[0]
    selected = result["selection"]["top_candidates"][0]
    title = str(selected.get("recommended_title") or post["title"])
    titles = list(selected.get("title_candidates") or [title])[:3]
    return {
        "date": report_date,
        "douyin_titles": titles,
        "voiceover_30s": " ".join(
            str(video.get(key) or "")
            for key in ("hook", "problem", "solution", "ending")
        ),
        "xiaohongshu_note": {
            "title": title,
            "body": "\n\n".join(str(page) for page in post["pages"]),
            "hashtags": post.get("tags", []),
        },
        "video_topics": [
            {
                "title": item.get("recommended_title"),
                "angle": item.get("why_now"),
            }
            for item in result["selection"]["top_candidates"]
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统一每日内容生产与 T+3 校准入口")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="从日报 JSON 生成今日 Top 1/Top 3")
    generate.add_argument("input", type=Path)
    generate.add_argument("--date", type=date.fromisoformat, default=date.today())
    generate.add_argument("--top-n", type=int, choices=(1, 3), default=3)
    generate.add_argument("--skip-llm", action="store_true")
    generate.add_argument("--output-root", type=Path, default=BASE_DIR / "outputs")
    published = sub.add_parser("published", help="登记 Top 1 已发布")
    published.add_argument("prediction", type=Path)
    published.add_argument("--published-at", type=datetime.fromisoformat, required=True)
    published.add_argument("--url")
    retro = sub.add_parser("retro", help="T+3 手工录入小红书表现")
    retro.add_argument("prediction", type=Path)
    retro.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    for name in ("views", "likes", "saves", "comments", "followers-gained"):
        retro.add_argument(f"--{name}", type=int, required=True)
    retro.add_argument(
        "--rubric", type=Path, default=BASE_DIR / "outputs" / "calibration" / "rubric.json"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate":
            result = run_daily_content_pipeline(
                load_projects(args.input),
                publish_date=args.date,
                use_llm=not args.skip_llm,
                top_n=args.top_n,
                output_root=args.output_root,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "published":
            result = register_publication(
                args.prediction, published_at=args.published_at, url=args.url
            )
            print(json.dumps(result["publication"], ensure_ascii=False, indent=2))
        else:
            result = record_t3_performance(
                args.prediction,
                as_of=args.as_of,
                views=args.views,
                likes=args.likes,
                saves=args.saves,
                comments=args.comments,
                followers_gained=args.followers_gained,
                rubric_path=args.rubric,
            )
            print(json.dumps(result["retro"], ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        LOGGER.error("Daily Content Pipeline 失败：%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
