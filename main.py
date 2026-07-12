from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from ai_summary import analyze_repositories, fallback_analysis, render_report
from config import settings
from content_generator import fallback_content, generate_content
from crawler import GitHubTrendingCrawler
from email_sender import send_email
from market_insight import (
    build_growth_metrics,
    fallback_market_insight,
    generate_market_insight,
    load_previous_stars,
)


LOGGER = logging.getLogger(__name__)


def setup_logging() -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s - %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    rotating = logging.handlers.RotatingFileHandler(
        settings.log_dir / "github-trend-bot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=14,
        encoding="utf-8",
    )
    rotating.setFormatter(formatter)
    root.addHandler(rotating)


@contextmanager
def single_instance(lock_file: Path) -> Iterator[None]:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("a+", encoding="utf-8")
    windows_lock = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write("\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                windows_lock = True
            except OSError as exc:
                raise RuntimeError("已有一个日报任务正在运行，本次退出") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("已有一个日报任务正在运行，本次退出") from exc
        yield
    finally:
        if windows_lock:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def run(dry_run: bool, skip_ai: bool) -> None:
    now = datetime.now(ZoneInfo(settings.report_timezone))
    report_date = now.date().isoformat()
    LOGGER.info("开始生成 %s GitHub 趋势日报", report_date)
    repositories = GitHubTrendingCrawler(settings).collect()
    previous_stars, elapsed_days = load_previous_stars(settings.report_dir, report_date)
    growth_metrics = build_growth_metrics(repositories, previous_stars, elapsed_days)

    if skip_ai:
        LOGGER.warning("已通过 --skip-ai 跳过 DeepSeek 分析")
        analysis = fallback_analysis(repositories, growth_metrics)
    else:
        try:
            analysis = analyze_repositories(repositories, settings, growth_metrics)
        except Exception as exc:
            LOGGER.error("DeepSeek 分析失败，降级为数据摘要：%s", exc)
            analysis = fallback_analysis(repositories, growth_metrics)

    market_insight = fallback_market_insight(repositories, growth_metrics)
    if not skip_ai:
        try:
            market_insight = generate_market_insight(
                repositories, analysis, growth_metrics, settings
            )
        except Exception as exc:
            LOGGER.error("市场洞察生成失败，使用规则降级内容：%s", exc)

    social_content = fallback_content(
        report_date, repositories, analysis, market_insight
    )
    if not skip_ai:
        try:
            social_content = generate_content(
                report_date, repositories, analysis, market_insight, settings
            )
        except Exception as exc:
            LOGGER.error("社媒内容生成失败，使用规则降级内容：%s", exc)

    plain_text, html_body = render_report(
        report_date, repositories, analysis, market_insight
    )
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    html_path = settings.report_dir / f"{report_date}.html"
    json_path = settings.report_dir / f"{report_date}.json"
    html_path.write_text(html_body, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "date": report_date,
                "repositories": [repo.to_dict() for repo in repositories],
                "growth_metrics": growth_metrics,
                "analysis": analysis,
                "market_insight": market_insight,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    LOGGER.info("报告已保存：%s", html_path)

    content_dir = settings.report_dir / "content"
    try:
        content_dir.mkdir(parents=True, exist_ok=True)
        content_path = content_dir / f"{report_date}.json"
        content_path.write_text(
            json.dumps(social_content, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        LOGGER.info("社媒内容 JSON 已保存：%s", content_path)
    except OSError as exc:
        # Auxiliary content must never block the established Gmail report flow.
        LOGGER.error("社媒内容 JSON 保存失败，继续日报发送：%s", exc)

    if dry_run:
        LOGGER.info("dry-run 模式：不发送邮件")
        return
    subject = f"【GitHub趋势日报】{report_date}"
    send_email(subject, plain_text, html_body, settings)


def send_test_email() -> None:
    now = datetime.now(ZoneInfo(settings.report_timezone))
    subject = f"【GitHub趋势日报】配置测试 {now:%Y-%m-%d %H:%M}"
    send_email(
        subject,
        "Gmail API 配置成功。服务器可以通过 HTTPS 发送日报。",
        "<h1>Gmail API 配置成功</h1><p>服务器可以通过 HTTPS 发送日报，无需 SMTP 25 端口。</p>",
        settings,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GitHub 每日趋势日报")
    parser.add_argument("--dry-run", action="store_true", help="生成报告但不发邮件")
    parser.add_argument("--skip-ai", action="store_true", help="跳过 DeepSeek，仅测试采集")
    parser.add_argument("--test-email", action="store_true", help="只发送 Gmail 配置测试邮件")
    return parser.parse_args()


def main() -> int:
    setup_logging()
    args = parse_args()
    try:
        with single_instance(settings.log_dir / "github-trend-bot.lock"):
            if args.test_email:
                send_test_email()
            else:
                run(dry_run=args.dry_run, skip_ai=args.skip_ai)
        return 0
    except Exception:
        LOGGER.exception("任务执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
