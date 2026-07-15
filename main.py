from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from config import settings
from email_sender import send_email
from runner import run_daily_pipelines


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
                handle.write("\\0")
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


def run(dry_run: bool, skip_ai: bool) -> dict:
    """Keep the historical main.run API while delegating orchestration."""
    return run_daily_pipelines(dry_run=dry_run, skip_ai=skip_ai, cfg=settings)


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
    parser.add_argument("--dry-run", action="store_true", help="生成报告但不发送邮件")
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
