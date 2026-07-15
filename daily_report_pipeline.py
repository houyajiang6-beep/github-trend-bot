from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_summary import (
    analyze_repositories,
    deepseek_error_summary,
    fallback_analysis,
    render_report,
)
from config import Settings
from content_generator import fallback_content, generate_content
from crawler import GitHubTrendingCrawler, Repository
from email_sender import send_email
from market_insight import (
    build_growth_metrics,
    fallback_market_insight,
    generate_market_insight,
    load_previous_stars,
)


LOGGER = logging.getLogger("daily-report-pipeline")


@dataclass
class DailyReportContext:
    report_date: str
    repositories: list[Repository]
    analysis: dict[str, Any]
    market_insight: dict[str, Any]
    growth_metrics: dict[str, Any]
    status: dict[str, Any]
    cfg: Settings


def _write_actions_status(cfg: Settings, status: dict[str, Any]) -> None:
    """Persist the legacy GitHub Actions status contract."""
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    (cfg.log_dir / "actions-status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def prepare_daily_report(
    report_date: str, *, cfg: Settings, skip_ai: bool
) -> DailyReportContext:
    """Collect and analyze the data shared by both daily pipelines."""
    LOGGER.info("开始生成 %s GitHub 趋势日报", report_date)
    status: dict[str, Any] = {
        "github": {"success": False, "repositories": 0},
        "deepseek": {
            "success": False,
            "provider": cfg.ai_provider,
            "model": cfg.deepseek_model,
            "fallback": True,
            "reason": "not_started",
        },
        "market_insight": {"success": False, "fallback": True, "generated": False},
        "social_content": {"success": False, "fallback": True, "generated": False},
        "report": {"success": False},
        "gmail": {"success": False, "skipped": False},
    }
    _write_actions_status(cfg, status)

    repositories = GitHubTrendingCrawler(cfg).collect()
    status["github"] = {"success": True, "repositories": len(repositories)}
    _write_actions_status(cfg, status)
    previous_stars, elapsed_days = load_previous_stars(cfg.report_dir, report_date)
    growth_metrics = build_growth_metrics(repositories, previous_stars, elapsed_days)

    if skip_ai:
        LOGGER.warning("已通过 --skip-ai 跳过 DeepSeek 分析")
        analysis = fallback_analysis(repositories, growth_metrics)
        status["deepseek"]["reason"] = "skip_ai"
    else:
        try:
            analysis = analyze_repositories(repositories, cfg, growth_metrics)
            status["deepseek"].update(
                {"success": True, "fallback": False, "reason": ""}
            )
            LOGGER.info(
                "DeepSeek 调用成功：provider=%s model=%s http_status=2xx",
                cfg.ai_provider,
                cfg.deepseek_model,
            )
        except Exception as exc:
            reason = deepseek_error_summary(exc)
            status["deepseek"]["reason"] = reason
            LOGGER.error("DeepSeek 分析失败，降级为数据摘要：%s", reason)
            analysis = fallback_analysis(repositories, growth_metrics)
    _write_actions_status(cfg, status)

    market_insight = fallback_market_insight(repositories, growth_metrics)
    if not skip_ai:
        try:
            market_insight = generate_market_insight(
                repositories, analysis, growth_metrics, cfg
            )
            status["market_insight"].update({"success": True, "fallback": False})
            LOGGER.info(
                "DeepSeek 市场洞察成功：provider=%s model=%s http_status=2xx",
                cfg.ai_provider,
                cfg.deepseek_model,
            )
        except Exception as exc:
            LOGGER.error(
                "市场洞察生成失败，使用规则降级内容：%s",
                deepseek_error_summary(exc),
            )
    _write_actions_status(cfg, status)
    return DailyReportContext(
        report_date=report_date,
        repositories=repositories,
        analysis=analysis,
        market_insight=market_insight,
        growth_metrics=growth_metrics,
        status=status,
        cfg=cfg,
    )


def fallback_social_content(context: DailyReportContext) -> dict[str, Any]:
    return fallback_content(
        context.report_date,
        context.repositories,
        context.analysis,
        context.market_insight,
    )


def generate_legacy_social_content(
    context: DailyReportContext, *, skip_ai: bool
) -> tuple[dict[str, Any], bool, bool]:
    """Preserve the pre-Creator report path when the new pipeline is disabled."""
    social_content = fallback_social_content(context)
    success = False
    fallback = True
    if not skip_ai:
        try:
            social_content = generate_content(
                context.report_date,
                context.repositories,
                context.analysis,
                context.market_insight,
                context.cfg,
            )
            success = True
            fallback = False
            LOGGER.info(
                "DeepSeek 社媒内容成功：provider=%s model=%s http_status=2xx",
                context.cfg.ai_provider,
                context.cfg.deepseek_model,
            )
        except Exception as exc:
            LOGGER.error(
                "社媒内容生成失败，使用规则降级内容：%s",
                deepseek_error_summary(exc),
            )
    return social_content, success, fallback


def finalize_daily_report(
    context: DailyReportContext,
    social_content: dict[str, Any],
    *,
    social_success: bool,
    social_fallback: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Write the legacy report artifacts and keep Gmail behavior unchanged."""
    cfg = context.cfg
    status = context.status
    report_date = context.report_date
    status["social_content"].update(
        {"success": social_success, "fallback": social_fallback}
    )
    _write_actions_status(cfg, status)

    plain_text, html_body = render_report(
        report_date,
        context.repositories,
        context.analysis,
        context.market_insight,
        social_content,
    )
    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    html_path = cfg.report_dir / f"{report_date}.html"
    json_path = cfg.report_dir / f"{report_date}.json"
    html_path.write_text(html_body, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "date": report_date,
                "repositories": [repo.to_dict() for repo in context.repositories],
                "growth_metrics": context.growth_metrics,
                "analysis": context.analysis,
                "market_insight": context.market_insight,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    LOGGER.info("报告已保存：%s", html_path)
    status["report"] = {"success": True}
    _write_actions_status(cfg, status)

    market_dir = cfg.report_dir / "market_insight"
    try:
        market_dir.mkdir(parents=True, exist_ok=True)
        market_path = market_dir / f"{report_date}.json"
        market_path.write_text(
            json.dumps(context.market_insight, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        status["market_insight"].update(
            {"generated": True, "output": str(market_path)}
        )
        LOGGER.info("市场洞察 JSON 已保存：%s", market_path)
    except OSError as exc:
        LOGGER.error("市场洞察 JSON 保存失败，继续日报发送：%s", exc)
    _write_actions_status(cfg, status)

    content_dir = cfg.report_dir / "content"
    try:
        content_dir.mkdir(parents=True, exist_ok=True)
        content_path = content_dir / f"{report_date}.json"
        content_path.write_text(
            json.dumps(social_content, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        status["social_content"].update(
            {"generated": True, "output": str(content_path)}
        )
        LOGGER.info("社媒内容 JSON 已保存：%s", content_path)
    except OSError as exc:
        # Auxiliary content must never block the established Gmail report flow.
        LOGGER.error("社媒内容 JSON 保存失败，继续日报发送：%s", exc)
    _write_actions_status(cfg, status)

    if dry_run:
        status["gmail"] = {"success": False, "skipped": True}
        LOGGER.info("dry-run 模式：不发送邮件")
        _write_actions_status(cfg, status)
        return status

    subject = f"【GitHub趋势日报】{report_date}"
    send_email(subject, plain_text, html_body, cfg)
    status["gmail"] = {"success": True, "skipped": False}
    _write_actions_status(cfg, status)
    return status
