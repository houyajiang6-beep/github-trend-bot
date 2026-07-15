from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import Settings, settings
from daily_content_pipeline import (
    run_daily_content_pipeline,
    social_content_from_pipeline,
)
from daily_report_pipeline import (
    DailyReportContext,
    fallback_social_content,
    finalize_daily_report,
    generate_legacy_social_content,
    prepare_daily_report,
)


LOGGER = logging.getLogger("pipeline-runner")


def _new_execution_status(now: datetime) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": f"{now:%Y%m%dT%H%M%S%z}-{uuid.uuid4().hex[:8]}",
        "overall_status": "running",
        "started_at": now.isoformat(),
        "finished_at": None,
        "updated_at": now.isoformat(),
        "daily_report": {
            "status": "running",
            "gmail_sent": False,
            "output": None,
        },
        "creator_pipeline": {
            "status": "degraded",
            "selected_project": None,
            "reason_code": "NOT_STARTED",
            "output": None,
            "error": None,
        },
    }


def _write_execution_status(cfg: Settings, status: dict[str, Any]) -> None:
    """Atomically persist the cross-pipeline production status."""
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.log_dir / "execution_status.json"
    temporary = path.with_suffix(".json.tmp")
    status["updated_at"] = datetime.now(ZoneInfo(cfg.report_timezone)).isoformat()
    temporary.write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def _run_creator_pipeline(
    context: DailyReportContext, *, skip_ai: bool, cfg: Settings
) -> tuple[dict[str, Any], bool, bool, dict[str, Any]]:
    if not cfg.enable_daily_content_pipeline:
        social_content, success, fallback = generate_legacy_social_content(
            context, skip_ai=skip_ai
        )
        return social_content, success, fallback, {
            "status": "degraded",
            "selected_project": None,
            "reason_code": "USER_DISABLED_LEGACY_REPORT_ONLY",
            "output": None,
            "error": None,
        }

    try:
        result = run_daily_content_pipeline(
            context.repositories,
            publish_date=context.report_date,
            cfg=cfg,
            use_llm=not skip_ai,
            top_n=3,
            output_root=cfg.creator_output_dir,
        )
        pipeline_status = result["status"]
        social_content = social_content_from_pipeline(result, context.report_date)
        LOGGER.info(
            "Creator Pipeline 完成：status=%s selected=%s output=%s",
            pipeline_status,
            result["selected_project"],
            result["creator_ready_directory"],
        )
        return social_content, True, pipeline_status == "degraded", {
            "status": pipeline_status,
            "selected_project": result["selected_project"],
            "reason_code": (
                "PIPELINE_FALLBACK_USED" if pipeline_status == "degraded" else None
            ),
            "output": result["creator_ready_directory"],
            "error": None,
        }
    except Exception as exc:
        # Creator output is optional for the established report/Gmail contract.
        # Do not invoke either Content Generator again after a partial failure.
        LOGGER.exception("Creator Pipeline 失败，保留日报与 Gmail")
        return fallback_social_content(context), False, True, {
            "status": "failed",
            "selected_project": None,
            "reason_code": "CREATOR_PIPELINE_FAILED",
            "output": None,
            "error": {"type": exc.__class__.__name__, "message": str(exc)},
        }


def run_daily_pipelines(
    *, dry_run: bool, skip_ai: bool, cfg: Settings = settings
) -> dict[str, Any]:
    """Coordinate the legacy daily report and the Creator Ready pipeline."""
    now = datetime.now(ZoneInfo(cfg.report_timezone))
    report_date = now.date().isoformat()
    execution_status = _new_execution_status(now)
    _write_execution_status(cfg, execution_status)
    try:
        context = prepare_daily_report(report_date, cfg=cfg, skip_ai=skip_ai)
        social_content, social_success, social_fallback, creator_status = (
            _run_creator_pipeline(context, skip_ai=skip_ai, cfg=cfg)
        )
        execution_status["creator_pipeline"] = creator_status
        _write_execution_status(cfg, execution_status)

        report_status = finalize_daily_report(
            context,
            social_content,
            social_success=social_success,
            social_fallback=social_fallback,
            dry_run=dry_run,
        )
        execution_status["daily_report"] = {
            "status": "success",
            "gmail_sent": bool(report_status["gmail"]["success"]),
            "output": str(cfg.report_dir / f"{report_date}.html"),
        }
        execution_status["overall_status"] = (
            "success" if creator_status["status"] == "success" else "degraded"
        )
        execution_status["finished_at"] = datetime.now(
            ZoneInfo(cfg.report_timezone)
        ).isoformat()
        _write_execution_status(cfg, execution_status)
        return execution_status
    except Exception as exc:
        execution_status["daily_report"] = {
            "status": "failed",
            "gmail_sent": False,
            "output": None,
            "error": {"type": exc.__class__.__name__, "message": str(exc)},
        }
        execution_status["overall_status"] = "failed"
        execution_status["finished_at"] = datetime.now(
            ZoneInfo(cfg.report_timezone)
        ).isoformat()
        _write_execution_status(cfg, execution_status)
        raise
